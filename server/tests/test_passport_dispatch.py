import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from server.companion.authorization import AuthenticatedScope
from server.companion.passport_dispatch import PassportDispatch
from server.companion.passport_tickets import PassportTickets


class PassportDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 10
        self.scope = AuthenticatedScope('user:1:agent:a', 1, 1, 'a', 'mia', 'device', 'client', 1, 1,
            datetime.now(timezone.utc) + timedelta(days=1), frozenset({'memory:read'}))
        self.auth = SimpleNamespace(_closed=False, require=AsyncMock())
        self.conn = SimpleNamespace(read_config_from_api=True, _youmi_scope=self.scope,
            _youmi_authorization=self.auth, client_abort=False, stop_event=asyncio.Event(),
            session_id='session', sentence_id='turn')
        self.tickets = PassportTickets(clock=lambda: self.now)
        self.dispatch = PassportDispatch(self.tickets, clock=lambda: self.now, capacity=2)

    async def test_live_turn_is_delivered_once_and_can_be_opened(self):
        waiter = await self.dispatch.submit(self.conn, '继续刚才的话题')
        event = await self.dispatch.next_event(timeout=1)
        self.assertEqual(event['text'], '继续刚才的话题')
        self.assertEqual((event['session_id'], event['turn_id']), ('session', 'turn'))
        handle, turn = await self.tickets.open(event['ticket'])
        self.assertIs(turn.connection, self.conn)
        self.assertIs((await self.tickets.resolve(handle)), turn)
        input_turn, original = await self.dispatch.request_for(handle)
        self.assertIs(input_turn, turn)
        self.assertEqual(original, '继续刚才的话题')
        await self.dispatch.reply(handle, '好的，继续聊。')
        self.assertEqual(await waiter, '好的，继续聊。')
        from server.companion.authorization import PrivateAuthorizationRequired
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.dispatch.request_for(handle)
        with self.assertRaisesRegex(RuntimeError, 'passport_turn_not_waiting'):
            await self.dispatch.reply(handle, '重复回复')
        self.assertIsNone(await self.dispatch.next_event(timeout=0))
        with self.assertRaisesRegex(RuntimeError, 'duplicate_passport_turn'):
            await self.dispatch.submit(self.conn, '重复')

    async def test_reply_limit_matches_checked_text_limit(self):
        waiter = await self.dispatch.submit(self.conn, '你好')
        event = await self.dispatch.next_event(timeout=1)
        handle, _ = await self.tickets.open(event['ticket'])
        with self.assertRaisesRegex(ValueError, 'invalid_passport_reply'):
            await self.dispatch.reply(handle, 'a' * 16001)
        await self.dispatch.reply(handle, 'a' * 16000)
        self.assertEqual(await waiter, 'a' * 16000)

    async def test_interrupted_and_expired_events_are_not_delivered(self):
        await self.dispatch.submit(self.conn, '不应送出')
        self.conn.client_abort = True
        self.assertIsNone(await self.dispatch.next_event(timeout=0))
        self.conn.client_abort = False
        self.conn.sentence_id = 'turn-two'
        await self.dispatch.submit(self.conn, '过期内容')
        self.now += 30
        self.assertIsNone(await self.dispatch.next_event(timeout=0))

    async def test_cancelled_waiter_or_abort_prevents_output(self):
        waiter = await self.dispatch.submit(self.conn, '你好')
        event = await self.dispatch.next_event(timeout=1)
        handle, _ = await self.tickets.open(event['ticket'])
        self.conn.client_abort = True
        from server.companion.authorization import PrivateAuthorizationRequired
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.dispatch.reply(handle, '不应播放')
        self.conn.client_abort = False
        waiter.cancel()
        with self.assertRaisesRegex(RuntimeError, 'passport_turn_not_waiting'):
            await self.dispatch.reply(handle, '不应重播')

    async def test_cancelled_waiter_removes_queued_event(self):
        waiter = await self.dispatch.submit(self.conn, '未送出的对话')
        waiter.cancel()
        await asyncio.sleep(0)
        self.assertIsNone(await self.dispatch.next_event(timeout=0))

    async def test_capacity_invalid_text_and_concurrent_deduplication(self):
        with self.assertRaises(ValueError):
            await self.dispatch.submit(self.conn, ' ')
        attempts = await asyncio.gather(self.dispatch.submit(self.conn, '第一条'),
                                        self.dispatch.submit(self.conn, '第二条'), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, RuntimeError) for result in attempts), 1)
        self.conn.sentence_id = 'turn-two'
        await self.dispatch.submit(self.conn, '另一轮')
        self.conn.sentence_id = 'turn-three'
        with self.assertRaisesRegex(RuntimeError, 'passport_dispatch_capacity'):
            await self.dispatch.submit(self.conn, '超过容量')


if __name__ == '__main__':
    unittest.main()
