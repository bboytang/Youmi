import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from server.companion.astrbot_identity import PassportIdentity
from server.companion.authorization import AuthenticatedScope, PrivateAuthorizationRequired
from server.companion.passport_dispatch import PassportDispatch
from server.companion.passport_tickets import PassportTickets


class PassportIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.scope = AuthenticatedScope('user:1:agent:a', 1, 1, 'a', 'mia', '02:00:00:00:00:01', 'client', 1, 1,
            datetime.now(timezone.utc) + timedelta(days=1), frozenset({'memory:read'}))
        scope = {**self.scope.__dict__, 'expires_at': self.scope.expires_at.isoformat(),
                 'permissions': list(self.scope.permissions)}
        self.auth = SimpleNamespace(_closed=False, require=AsyncMock(),
                                    _request=AsyncMock(return_value={'youmi_scope': scope, 'prompt': None}))
        self.conn = SimpleNamespace(read_config_from_api=True, _youmi_scope=self.scope,
            _youmi_authorization=self.auth, client_abort=False, stop_event=asyncio.Event(),
            session_id='session', sentence_id='turn')
        self.tickets = PassportTickets()
        self.dispatch = PassportDispatch(self.tickets)
        self.waiter = await self.dispatch.submit(self.conn, '真实设备原话')
        event = await self.dispatch.next_event(timeout=1)
        self.handle, self.turn = await self.tickets.open(event['ticket'])
        self.identity = PassportIdentity(self.auth, self.handle, self.dispatch)

    async def test_source_and_manager_config_use_physical_grant(self):
        turn, text = await self.identity.source()
        self.assertIs(turn, self.turn)
        self.assertEqual(text, '真实设备原话')
        self.assertEqual((await self.identity.config(turn.scope))['prompt'], '')
        self.auth._request.assert_awaited_once_with('/config/youmi-auth/passport-config', self.scope.snapshot())

    async def test_forged_scope_and_changed_manager_proof_are_rejected(self):
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.identity.config(replace(self.scope, owner='user:2:agent:a'))
        self.auth._request.return_value['youmi_scope']['credential_version'] = 2
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.identity.config(self.scope)

    async def test_interruption_during_manager_config_invalidates_result(self):
        result = self.auth._request.return_value
        async def changed(*_):
            self.conn.client_abort = True
            return result
        self.auth._request.side_effect = changed
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.identity.config(self.scope)

    async def test_completed_input_cannot_be_reused(self):
        await self.dispatch.reply(self.handle, '回复')
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.identity.source()


if __name__ == '__main__':
    unittest.main()
