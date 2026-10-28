import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from server.companion.astrbot_api import AstrBotHandler
from server.companion.authorization import AuthenticatedScope, PrivateAuthorizationRequired
from server.companion.passport_tickets import PassportTickets
from server.companion.passport_dispatch import PassportDispatch


class PassportBridgeHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        token = Path(self.temp.name) / 'token'
        token.write_text('s' * 43)
        self.tickets = PassportTickets()
        self.patch = patch('server.companion.astrbot_api.passport_tickets', self.tickets)
        self.patch.start()
        self.dispatch = PassportDispatch(self.tickets)
        self.dispatch_patch = patch('server.companion.astrbot_api.passport_dispatch', self.dispatch)
        self.dispatch_patch.start()
        scope = AuthenticatedScope('user:1:agent:a', 1, 1, 'a', 'mia', 'device', 'client', 1, 1,
            datetime.now(timezone.utc) + timedelta(days=1), frozenset({'memory:read'}))
        self.auth = SimpleNamespace(_closed=False, require=AsyncMock())
        self.connection = SimpleNamespace(read_config_from_api=True, _youmi_scope=scope,
            _youmi_authorization=self.auth, client_abort=False, stop_event=asyncio.Event(),
            session_id='session', sentence_id='turn')
        self.handler = AstrBotHandler({}, {'service_token_file': str(token), 'allow_internal_http': True},
                                      store=object(), builder=object(), authorization=self.auth)
        app = web.Application()
        app.router.add_route('*', '/internal/astrbot/{action}', self.handler.handle)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.patch.stop()
        self.dispatch_patch.stop()
        self.temp.cleanup()

    async def post(self, action, body, token='s' * 43):
        async with self.client.post('/internal/astrbot/' + action,
                                    json={'protocol_version': 1, **body},
                                    headers={'Authorization': 'Bearer ' + token}) as response:
            return response.status, await response.json(), response.headers

    async def test_service_auth_single_use_live_scope_and_close(self):
        ticket = await self.tickets.issue(self.connection)
        status, _, _ = await self.post('passport-open', {'ticket': ticket}, token='wrong')
        self.assertEqual(status, 401)
        status, opened, headers = await self.post('passport-open', {'ticket': ticket})
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(set(opened), {'handle', 'session_id', 'turn_id'})
        handle = opened['handle']
        self.assertEqual((await self.post('passport-open', {'ticket': ticket}))[0], 403)
        self.assertEqual((await self.post('passport-check', {'handle': handle}))[0], 200)
        self.auth.require.side_effect = PrivateAuthorizationRequired()
        self.assertEqual((await self.post('passport-check', {'handle': handle}))[0], 403)
        self.auth.require.side_effect = None
        self.assertEqual((await self.post('passport-close', {'handle': handle}))[0], 200)
        self.assertEqual((await self.post('passport-check', {'handle': handle}))[0], 403)

    async def test_tls_required_and_extra_input_rejected_without_consuming_ticket(self):
        ticket = await self.tickets.issue(self.connection)
        self.handler.bridge['allow_internal_http'] = False
        self.assertEqual((await self.post('passport-open', {'ticket': ticket}))[0], 403)
        self.handler.bridge['allow_internal_http'] = True
        self.assertEqual((await self.post('passport-open', {'ticket': ticket, 'owner': 'forged'}))[0], 400)
        self.assertEqual((await self.post('passport-open', {'ticket': ticket}))[0], 200)

    async def test_device_event_only_available_to_authenticated_service(self):
        await self.dispatch.submit(self.connection, '设备语音转写')
        self.assertEqual((await self.post('passport-next', {}, token='wrong'))[0], 401)
        status, body, headers = await self.post('passport-next', {})
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(body['event']['text'], '设备语音转写')
        status, opened, _ = await self.post('passport-open', {'ticket': body['event']['ticket']})
        self.assertEqual(status, 200)
        self.assertIn('handle', opened)


if __name__ == '__main__':
    unittest.main()
