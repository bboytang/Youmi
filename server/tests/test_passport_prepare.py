import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from server.companion.astrbot_api import AstrBotHandler
from server.companion.authorization import AuthenticatedScope, PrivateAuthorizationRequired
from server.companion.context import ContextBuilder
from server.companion.memory import MemoryStore
from server.companion.passport_dispatch import PassportDispatch
from server.companion.passport_tickets import PassportTickets
from server.companion.passport_mcp import PassportMCP
from server.companion import shared_history
from server.companion.semantic import SemanticMemoryIndex
from test_astrbot_bridge import ExistingWorkerFixture, ManagerFixture
from test_companion_context import UnitEncoder


class PassportPrepareTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.root = root
        (root / 'token').write_text('s' * 43)
        self.store = MemoryStore(root / 'memory.sqlite3')
        self.scope = AuthenticatedScope('user:1:agent:a', 1, 1, 'a', 'mia', '02:00:00:00:00:01', 'client', 0, 1,
            datetime.now(timezone.utc) + timedelta(days=1), frozenset({'memory:read', 'memory:write', 'memory:manage'}))
        self.auth = ManagerFixture(self.scope)
        async def manager(endpoint, snapshot):
            await self.auth.restore(snapshot)
            if endpoint != '/config/youmi-auth/passport-config':
                raise PrivateAuthorizationRequired()
            return self.auth.models
        self.auth._request = manager
        self.conn = SimpleNamespace(read_config_from_api=True, _youmi_scope=self.scope,
            _youmi_authorization=self.auth, client_abort=False, stop_event=asyncio.Event(),
            session_id='device-session', sentence_id='turn')
        self.tickets = PassportTickets()
        self.dispatch = PassportDispatch(self.tickets)
        self.ticket_patch = patch('server.companion.astrbot_api.passport_tickets', self.tickets)
        self.dispatch_patch = patch('server.companion.astrbot_api.passport_dispatch', self.dispatch)
        self.mcp = PassportMCP(self.tickets)
        self.mcp_patch = patch('server.companion.astrbot_api.passport_mcp', self.mcp)
        self.ticket_patch.start()
        self.dispatch_patch.start()
        self.mcp_patch.start()
        bridge = {'service_token_file': str(root / 'token'), 'allow_internal_http': True, 'bindings': [],
                  'models': {'ci-model': {'context_window': 32768, 'response_tokens': 2048,
                                        'capacity_source': 'synthetic test'}}}
        self.handler = AstrBotHandler({}, bridge, store=self.store, worker=ExistingWorkerFixture(self.store),
            builder=ContextBuilder(SemanticMemoryIndex(self.store, UnitEncoder())), authorization=self.auth)
        app = web.Application()
        app.router.add_route('*', '/internal/astrbot/{action}', self.handler.handle)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.waiter = await self.dispatch.submit(self.conn, '我喜欢蓝色')
        event = await self.dispatch.next_event(timeout=1)
        self.handle, _ = await self.tickets.open(event['ticket'])

    async def asyncTearDown(self):
        self.waiter.cancel()
        await self.client.close()
        self.ticket_patch.stop()
        self.dispatch_patch.stop()
        self.mcp_patch.stop()
        self.store.close()
        self.temp.cleanup()

    async def post(self, action, body):
        async with self.client.post('/internal/astrbot/' + action, json={'protocol_version': 1, **body},
                                    headers={'Authorization': 'Bearer ' + 's' * 43}) as response:
            return response.status, await response.json()

    async def test_device_source_is_authoritative_and_scope_cannot_be_forged(self):
        body = {'handle': self.handle, 'attempt_id': 'attempt', 'model': 'ci-model'}
        self.assertEqual((await self.post('passport-prepare', body | {'query_text': '伪造输入'}))[0], 400)
        status, prepared = await self.post('passport-prepare', body)
        self.assertEqual(status, 200, prepared)
        self.assertEqual(prepared['dynamic_context']['role_data'], 'owner-authored role')
        row = self.store.db.execute('SELECT * FROM mia_astrbot_turns WHERE turn_key=?', (prepared['turn_key'],)).fetchone()
        payload = json.loads(row['payload_json'])
        self.assertEqual(payload['excerpt'], '我喜欢蓝色')
        self.assertEqual(json.loads(row['scope_json']), self.scope.snapshot())
        self.assertEqual((await self.post('passport-prepare', body))[0], 200)
        self.assertEqual((await self.post('passport-prepare', body | {'attempt_id': 'other'}))[0], 409)
        self.auth.active = False
        self.assertEqual((await self.post('passport-prepare', body))[0], 403)

    async def test_reply_requires_matching_claim_and_completes_once(self):
        status, prepared = await self.post('passport-prepare',
            {'handle': self.handle, 'attempt_id': 'attempt', 'model': 'ci-model'})
        self.assertEqual(status, 200, prepared)
        token = prepared['turn_token']
        reply = {'handle': self.handle, 'turn_token': token, 'completion_id': 'completion', 'text': '好的。'}
        self.assertEqual((await self.post('passport-reply', reply))[0], 409)
        claim = {'turn_token': token, 'completion_id': 'completion', 'final_text': '好的。'}
        self.assertEqual((await self.post('claim', claim))[0], 200)
        complete = {'turn_token': token, 'completion_id': 'completion', 'result': 'adapter_accepted',
                    'final_text': '好的。', 'message_components': ['plain'], 'agent_tool_summary': []}
        self.assertEqual((await self.post('complete', complete))[0], 409)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM mia_jobs').fetchone()[0], 0)
        self.assertEqual((await self.post('passport-reply', reply | {'text': '另一句话'}))[0], 409)
        self.assertEqual((await self.post('passport-reply', reply))[0], 200)
        self.assertEqual(await self.waiter, '好的。')
        self.assertEqual((await self.post('passport-reply', reply))[0], 409)
        self.assertEqual((await self.post('complete', complete | {'result': 'failed'}))[0], 409)
        status, result = await self.post('complete', complete)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual((await self.post('complete', complete))[0], 200)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM mia_jobs').fetchone()[0], 1)

    async def test_long_checked_reply_is_delivered_and_completed(self):
        _, prepared = await self.post('passport-prepare',
            {'handle': self.handle, 'attempt_id': 'long-attempt', 'model': 'ci-model'})
        text = '好。' * 2500
        token = prepared['turn_token']
        claim = {'turn_token': token, 'completion_id': 'long-completion', 'final_text': text}
        self.assertEqual((await self.post('claim', claim))[0], 200)
        reply = {'handle': self.handle, 'turn_token': token, 'completion_id': 'long-completion', 'text': text}
        self.assertEqual((await self.post('passport-reply', reply))[0], 200)
        self.assertEqual(await self.waiter, text)
        complete = claim | {'result': 'adapter_accepted', 'message_components': ['plain'], 'agent_tool_summary': []}
        self.assertEqual((await self.post('complete', complete))[0], 200)

    async def test_unicode_reply_uses_bounded_utf8_transport(self):
        from integrations.astrbot_plugin_youmi.client import BridgeClient
        _, prepared = await self.post('passport-prepare',
            {'handle': self.handle, 'attempt_id': 'unicode-attempt', 'model': 'ci-model'})
        bridge = BridgeClient({'youmi_base_url': str(self.client.make_url('/')).rstrip('/'),
            'service_credential_file': str(self.root / 'token'), 'allow_internal_http': True})
        try:
            text = '🙂。' * 8000
            claim = {'turn_token': prepared['turn_token'], 'completion_id': 'unicode-completion', 'final_text': text}
            self.assertEqual((await bridge.call('claim', claim))['checked_text'], text)
            await bridge.call('passport-reply', {'handle': self.handle, 'turn_token': claim['turn_token'],
                'completion_id': claim['completion_id'], 'text': text})
            self.assertEqual(await self.waiter, text)
            result = await bridge.call('complete', claim | {'result': 'adapter_accepted',
                'message_components': ['plain'], 'agent_tool_summary': []})
            self.assertEqual(result['status'], 'completed')
        finally:
            await bridge.close()

    async def test_revocation_and_context_change_prevent_reply_delivery(self):
        status, prepared = await self.post('passport-prepare',
            {'handle': self.handle, 'attempt_id': 'attempt', 'model': 'ci-model'})
        self.assertEqual(status, 200, prepared)
        token = prepared['turn_token']
        claim = {'turn_token': token, 'completion_id': 'completion', 'final_text': '好的。'}
        self.assertEqual((await self.post('claim', claim))[0], 200)
        reply = {'handle': self.handle, 'turn_token': token, 'completion_id': 'completion', 'text': '好的。'}
        self.auth.active = False
        self.assertEqual((await self.post('passport-reply', reply))[0], 403)
        self.assertFalse(self.waiter.done())
        self.auth.active = True
        self.store.set_paused(self.scope.owner, True)
        self.assertEqual((await self.post('passport-reply', reply))[0], 409)
        self.assertFalse(self.waiter.done())
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM mia_jobs').fetchone()[0], 0)

    async def test_only_declared_device_mcp_tool_executes_once(self):
        definition = {'type': 'function', 'function': {'name': 'set_volume', 'description': '设置音量',
                      'parameters': {'type': 'object', 'properties': {'volume': {'type': 'integer'}}}}}
        executor = SimpleNamespace(get_tools=lambda: {'set_volume': SimpleNamespace(description=definition)},
            execute=AsyncMock(return_value=SimpleNamespace(action=SimpleNamespace(name='REQLLM'), result='音量已设置')))
        self.conn.func_handler = SimpleNamespace(device_mcp_executor=executor)
        status, prepared = await self.post('passport-prepare',
            {'handle': self.handle, 'attempt_id': 'attempt', 'model': 'ci-model'})
        self.assertEqual(status, 200, prepared)
        auth = {'handle': self.handle, 'turn_token': prepared['turn_token']}
        status, listed = await self.post('passport-tools', auth)
        self.assertEqual(status, 200, listed)
        name = listed['tools'][0]['name']
        call = auth | {'call_id': 'call-1', 'name': name, 'arguments': {'volume': 3}}
        results = await asyncio.gather(self.post('passport-tool', call), self.post('passport-tool', call))
        self.assertEqual(sorted(status for status, _ in results), [200, 409])
        executor.execute.assert_awaited_once_with(self.conn, 'set_volume', {'volume': 3})
        self.assertEqual((await self.post('passport-tool', call | {'call_id': 'new', 'name': 'passport_device_' + '0' * 24}))[0], 403)
        self.auth.active = False
        self.assertEqual((await self.post('passport-tool', call | {'call_id': 'other'}))[0], 403)
        executor.execute.assert_awaited_once()

    async def test_revoked_inflight_device_tool_is_not_replayed(self):
        definition = {'type': 'function', 'function': {'name': 'set_volume', 'description': '设置音量',
                      'parameters': {'type': 'object', 'properties': {}}}}
        async def revoked(*_):
            self.auth.active = False
            return SimpleNamespace(action=SimpleNamespace(name='REQLLM'), result='done')
        executor = SimpleNamespace(get_tools=lambda: {'set_volume': SimpleNamespace(description=definition)},
                                   execute=AsyncMock(side_effect=revoked))
        self.conn.func_handler = SimpleNamespace(device_mcp_executor=executor)
        _, prepared = await self.post('passport-prepare',
            {'handle': self.handle, 'attempt_id': 'attempt', 'model': 'ci-model'})
        auth = {'handle': self.handle, 'turn_token': prepared['turn_token']}
        _, listed = await self.post('passport-tools', auth)
        call = auth | {'call_id': 'call-1', 'name': listed['tools'][0]['name'], 'arguments': {}}
        self.assertEqual((await self.post('passport-tool', call))[0], 403)
        self.auth.active = True
        self.assertEqual((await self.post('passport-tool', call))[0], 409)
        executor.execute.assert_awaited_once()

    async def test_changed_device_tool_schema_is_not_executed(self):
        definition = {'type': 'function', 'function': {'name': 'set_volume', 'description': '设置音量',
                      'parameters': {'type': 'object', 'properties': {}}}}
        executor = SimpleNamespace(get_tools=lambda: {'set_volume': SimpleNamespace(description=definition)},
                                   execute=AsyncMock())
        self.conn.func_handler = SimpleNamespace(device_mcp_executor=executor)
        _, prepared = await self.post('passport-prepare',
            {'handle': self.handle, 'attempt_id': 'attempt', 'model': 'ci-model'})
        auth = {'handle': self.handle, 'turn_token': prepared['turn_token']}
        _, listed = await self.post('passport-tools', auth)
        definition['function']['description'] = '能力发生变化'
        call = auth | {'call_id': 'call-1', 'name': listed['tools'][0]['name'], 'arguments': {}}
        self.assertEqual((await self.post('passport-tool', call))[0], 403)
        executor.execute.assert_not_awaited()

    async def test_completed_qq_and_passport_turns_share_recent_context_both_directions(self):
        virtual = replace(self.scope, device_id='astrbot-ci', client_id='astrbot:12345')
        (self.root / 'credential').write_text('c' * 43)
        self.handler.bridge['bindings'] = [{'bot_uin': '12345', 'conversation_key': 'qq:private:67890',
            'device_id': virtual.device_id, 'client_id': virtual.client_id,
            'credential_file': str(self.root / 'credential')}]
        scopes = (self.scope, virtual)
        async def require(scope, permission):
            if not self.auth.active or scope not in scopes or permission not in scope.permissions:
                raise PrivateAuthorizationRequired()
        async def restore(snapshot):
            for scope in scopes:
                if snapshot == scope.snapshot():
                    await require(scope, 'memory:read')
                    return scope
            raise PrivateAuthorizationRequired()
        async def verify(device, client, credential):
            if (device, client, credential) != (virtual.device_id, virtual.client_id, 'c' * 43):
                raise PrivateAuthorizationRequired()
            await require(virtual, 'memory:read')
            return virtual
        async def manager(endpoint, snapshot):
            scope = await restore(snapshot)
            if endpoint not in {'/config/youmi-auth/bridge-config', '/config/youmi-auth/passport-config'}:
                raise PrivateAuthorizationRequired()
            return {'youmi_scope': {**scope.__dict__, 'permissions': list(scope.permissions),
                    'expires_at': scope.expires_at.isoformat()}, 'prompt': 'owner-authored role'}
        self.auth.require, self.auth.restore, self.auth.verify, self.auth._request = require, restore, verify, manager
        qq = {'bot_uin': '12345', 'conversation_key': 'qq:private:67890', 'sender_uin': '67890',
              'source_message_id': 'qq-one', 'attempt_id': 'qq-attempt', 'query_text': '我们刚刚在讨论海边',
              'model': 'ci-model', 'external_input_bytes': 0, 'origin': 'user', 'source_kind': 'text'}
        status, qprepared = await self.post('prepare', qq)
        self.assertEqual(status, 200, qprepared)
        qclaim = {'turn_token': qprepared['turn_token'], 'completion_id': 'qq-done', 'final_text': '我们聊到海边的晚霞。'}
        self.assertEqual((await self.post('claim', qclaim))[0], 200)
        qcomplete = qclaim | {'result': 'adapter_accepted', 'message_components': ['plain'], 'agent_tool_summary': []}
        self.assertEqual((await self.post('complete', qcomplete))[0], 200)
        status, prepared = await self.post('passport-prepare',
            {'handle': self.handle, 'attempt_id': 'passport-attempt', 'model': 'ci-model'})
        self.assertEqual(status, 200, prepared)
        self.assertEqual(prepared['dynamic_context']['shared_recent_turns'][0]['user_text'], qq['query_text'])
        claim = {'turn_token': prepared['turn_token'], 'completion_id': 'passport-done', 'final_text': '晚霞很漂亮。'}
        self.assertEqual((await self.post('claim', claim))[0], 200)
        self.assertEqual((await self.post('passport-reply', {'handle': self.handle, 'turn_token': prepared['turn_token'],
            'completion_id': 'passport-done', 'text': '晚霞很漂亮。'}))[0], 200)
        complete = claim | {'result': 'adapter_accepted', 'message_components': ['plain'], 'agent_tool_summary': []}
        self.assertEqual((await self.post('complete', complete))[0], 200)
        status, qnext = await self.post('prepare', qq | {'source_message_id': 'qq-two', 'attempt_id': 'second',
                                                        'query_text': '然后呢？'})
        self.assertEqual(status, 200, qnext)
        self.assertEqual(qnext['dynamic_context']['shared_recent_turns'][0]['assistant_text'], '晚霞很漂亮。')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_shared_history_turns').fetchone()[0], 2)

    async def test_shared_history_clear_invalidates_inflight_reply(self):
        with self.store.lock, self.store.db:
            shared_history.record_locked(self.store, self.scope.owner, 'old-qq', 'qq', '先前话题', '先前回复')
        status, prepared = await self.post('passport-prepare',
            {'handle': self.handle, 'attempt_id': 'attempt', 'model': 'ci-model'})
        self.assertEqual(status, 200, prepared)
        self.assertEqual(prepared['dynamic_context']['shared_recent_turns'][0]['user_text'], '先前话题')
        claim = {'turn_token': prepared['turn_token'], 'completion_id': 'completion', 'final_text': '好的。'}
        self.assertEqual((await self.post('claim', claim))[0], 200)
        shared_history.configure(self.store, self.scope.owner, clear=True)
        reply = {'handle': self.handle, 'turn_token': prepared['turn_token'], 'completion_id': 'completion', 'text': '好的。'}
        self.assertEqual((await self.post('passport-reply', reply))[0], 409)
        self.assertFalse(self.waiter.done())

    async def test_history_expiry_invalidates_inflight_claim(self):
        base = int(self.store.clock())
        self.store.clock = lambda: base - shared_history.RETENTION + 2
        with self.store.lock, self.store.db:
            shared_history.record_locked(self.store, self.scope.owner, 'old-qq', 'qq', '先前话题', '先前回复')
        self.store.clock = lambda: base
        status, prepared = await self.post('passport-prepare',
            {'handle': self.handle, 'attempt_id': 'attempt', 'model': 'ci-model'})
        self.assertEqual(status, 200, prepared)
        self.assertEqual(len(prepared['dynamic_context']['shared_recent_turns']), 1)
        self.store.clock = lambda: base + 2
        claim = {'turn_token': prepared['turn_token'], 'completion_id': 'completion', 'final_text': '好的。'}
        self.assertEqual((await self.post('claim', claim))[0], 409)

    async def test_older_summary_is_loaded_without_copying_shared_text_into_turn_ledger(self):
        for index in range(15):
            with self.store.lock, self.store.db:
                shared_history.record_locked(self.store, self.scope.owner, 'qq-' + str(index), 'qq',
                                             '之前的话题' + str(index), '之前的回答' + str(index))
        async def summarize(store, scope, authorization, models, channel, snapshot):
            self.assertEqual(channel, 'passport')
            shared_history.save_summary(store, scope.owner, channel, snapshot, '此前讨论了几个话题。')
            return '此前讨论了几个话题。'
        body = {'handle': self.handle, 'attempt_id': 'attempt', 'model': 'ci-model'}
        with patch('server.companion.shared_history_summary.ensure', side_effect=summarize) as ensure:
            status, prepared = await self.post('passport-prepare', body)
            self.assertEqual(status, 200, prepared)
            self.assertEqual(prepared['dynamic_context']['shared_earlier_summary'], '此前讨论了几个话题。')
            self.assertEqual(len(prepared['dynamic_context']['shared_recent_turns']), 12)
            row = self.store.db.execute('SELECT payload_json FROM mia_astrbot_turns WHERE turn_key=?',
                                        (prepared['turn_key'],)).fetchone()
            self.assertNotIn('此前讨论了几个话题', row['payload_json'])
            self.assertNotIn('之前的话题0', row['payload_json'])
            self.assertEqual((await self.post('passport-prepare', body))[0], 200)
            self.assertEqual(ensure.await_count, 1)

    async def test_device_explicit_clear_command_deletes_only_shared_history(self):
        with self.store.lock, self.store.db:
            shared_history.record_locked(self.store, self.scope.owner, 'old-qq', 'qq', '先前话题', '先前回复')
        self.waiter.cancel()
        await asyncio.sleep(0)
        self.conn.sentence_id = 'clear-turn'
        self.waiter = await self.dispatch.submit(self.conn, '清除跨设备对话记录')
        event = await self.dispatch.next_event(timeout=1)
        self.handle, _ = await self.tickets.open(event['ticket'])
        status, prepared = await self.post('passport-prepare',
            {'handle': self.handle, 'attempt_id': 'clear', 'model': 'ci-model'})
        self.assertEqual(status, 200, prepared)
        self.assertIn('已清除跨设备近期对话', prepared['dynamic_context']['memory_action_notice'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_shared_history_turns').fetchone()[0], 0)
        self.assertNotIn('shared_recent_turns', prepared['dynamic_context'])


if __name__ == '__main__':
    unittest.main()
