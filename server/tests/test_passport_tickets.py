import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from server.companion.authorization import AuthenticatedScope, PrivateAuthorizationRequired
from server.companion.passport_tickets import PassportTickets


class PassportTicketTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 10
        self.scope = AuthenticatedScope('user:1:agent:a', 1, 1, 'a', 'mia', 'device', 'client', 1, 1,
            datetime.now(timezone.utc) + timedelta(days=1), frozenset({'memory:read'}))
        self.auth = SimpleNamespace(_closed=False, require=AsyncMock())
        self.conn = SimpleNamespace(read_config_from_api=True, _youmi_scope=self.scope,
            _youmi_authorization=self.auth, client_abort=False, stop_event=asyncio.Event(),
            session_id='session', sentence_id='turn')
        self.tickets = PassportTickets(clock=lambda: self.now, ttl=10, capacity=2)

    async def test_single_use_even_under_concurrency(self):
        ticket = await self.tickets.issue(self.conn)
        results = await asyncio.gather(self.tickets.consume(ticket), self.tickets.consume(ticket),
                                       return_exceptions=True)
        successes = [value for value in results if not isinstance(value, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertIs(successes[0].connection, self.conn)
        self.assertEqual(sum(isinstance(value, PrivateAuthorizationRequired) for value in results), 1)
        self.assertEqual(len(ticket), 43)

    async def test_expiry_abort_scope_change_and_revocation(self):
        ticket = await self.tickets.issue(self.conn)
        self.now += 10
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.tickets.consume(ticket)
        for change in ('client_abort', 'sentence_id', '_youmi_scope', '_closed'):
            with self.subTest(change=change):
                ticket = await self.tickets.issue(self.conn)
                target = self.auth if change == '_closed' else self.conn
                old = getattr(target, change)
                setattr(target, change, True if change in ('client_abort', '_closed') else None)
                with self.assertRaises(PrivateAuthorizationRequired):
                    await self.tickets.consume(ticket)
                setattr(target, change, old)
        ticket = await self.tickets.issue(self.conn)
        self.auth.require.side_effect = PrivateAuthorizationRequired()
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.tickets.consume(ticket)
        self.auth.require.side_effect = None
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.tickets.consume(ticket)

    async def test_change_during_manager_check_and_capacity(self):
        ticket = await self.tickets.issue(self.conn)
        async def abort_during_check(*_):
            self.conn.client_abort = True
        self.auth.require.side_effect = abort_during_check
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.tickets.consume(ticket)
        self.conn.client_abort = False
        self.auth.require.side_effect = None
        await self.tickets.issue(self.conn)
        await self.tickets.issue(self.conn)
        with self.assertRaisesRegex(RuntimeError, 'passport_ticket_capacity'):
            await self.tickets.issue(self.conn)
        self.now += 10
        await self.tickets.issue(self.conn)

    async def test_ticket_expiring_during_manager_check_is_not_usable(self):
        ticket = await self.tickets.issue(self.conn)
        async def expire_during_check(*_):
            self.now += 10
        self.auth.require.side_effect = expire_during_check
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.tickets.consume(ticket)
        self.auth.require.side_effect = None
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.tickets.consume(ticket)

    async def test_untrusted_connection_cannot_issue(self):
        self.conn.read_config_from_api = False
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.tickets.issue(self.conn)

    async def test_claimed_turn_requires_fresh_authorization_before_later_output(self):
        turn = await self.tickets.consume(await self.tickets.issue(self.conn))
        await self.tickets.require_live(turn)
        self.auth.require.side_effect = PrivateAuthorizationRequired()
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.tickets.require_live(turn)

    async def test_replaced_authorizer_and_closing_connection_invalidate_ticket(self):
        ticket = await self.tickets.issue(self.conn)
        self.conn._youmi_authorization = SimpleNamespace(_closed=False, require=AsyncMock())
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.tickets.consume(ticket)
        self.conn._youmi_authorization = self.auth
        ticket = await self.tickets.issue(self.conn)
        self.conn.stop_event.set()
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.tickets.consume(ticket)

    async def test_active_handle_rechecks_authorization_and_can_be_closed(self):
        handle, turn = await self.tickets.open(await self.tickets.issue(self.conn))
        self.assertIs((await self.tickets.resolve(handle)), turn)
        self.conn.stop_event.set()
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.tickets.resolve(handle)
        self.conn.stop_event.clear()
        await self.tickets.close(handle)
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.tickets.resolve(handle)

    async def test_active_handle_expiry_and_close_during_authorization(self):
        handle, _ = await self.tickets.open(await self.tickets.issue(self.conn))
        async def close_during_check(*_):
            await self.tickets.close(handle)
        self.auth.require.side_effect = close_during_check
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.tickets.resolve(handle)
        self.auth.require.side_effect = None
        handle, _ = await self.tickets.open(await self.tickets.issue(self.conn))
        self.now += 300
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.tickets.resolve(handle)


if __name__ == '__main__':
    unittest.main()
