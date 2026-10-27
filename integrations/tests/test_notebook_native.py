"""Exercise notebook tools and hook ordering with the pinned real AstrBot event API."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
import sqlite3

from astrbot.core.agent.message import Message
from astrbot.core.pipeline.context_utils import call_event_hook
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.star import StarMetadata, star_map
from astrbot.core.star.star_handler import EventType, StarHandlerMetadata, star_handlers_registry

from integrations.astrbot_plugin_youmi.client import BridgeError
from integrations.astrbot_plugin_youmi_notebook.main import YoumiNotebook
from integrations.astrbot_plugin_youmi_notebook.notebook import MARKER, Notebook


def event(peer='67890'):
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.self_id, message.session_id, message.message_id = '12345', peer, 'test-message'
    message.sender = MessageMember(peer, 'fixture')
    message.message, message.message_str = [], '测试'
    message.raw_message = {'message': []}
    result = AiocqhttpMessageEvent('测试', message, PlatformMetadata('aiocqhttp', 'fixture', 'qq-ci'), peer, SimpleNamespace())
    result.set_extra('youmi_primary_chat', True)
    result.set_extra('youmi_turn', SimpleNamespace(prepared={'turn_token': 'verified-token', 'turn_key': 'turn-key'}))
    return result


class NativeNotebookTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.plugin = object.__new__(YoumiNotebook)
        self.plugin.bridge_config = {'qq_platform_id': 'qq-ci', 'scheduled_bot_uin': '12345'}
        self.plugin.store = Notebook(Path(self.temp.name) / 'notebook.sqlite3')
        self.plugin.client = SimpleNamespace(call=AsyncMock(return_value={'turn_key': 'turn-key'}))

    def tearDown(self):
        self.temp.cleanup()

    async def test_actual_native_event_writes_and_recollects_secret_in_a_new_session(self):
        saved = json.loads(await self.plugin.write(event(), '想留住的瞬间', '我：这让我有点开心。', 'secret'))
        self.assertTrue(saved['saved'])
        fresh_event = event()
        fresh_event.message_obj.message_id = 'a-new-message'
        found = json.loads(await self.plugin.read(fresh_event, query='开心'))
        self.assertEqual(found['entries'][0]['id'], saved['entry_id'])
        diary = json.loads(await self.plugin.read(fresh_event, entry_id=saved['entry_id']))
        self.assertIn('私密心事', diary['diary'])
        self.plugin.client.call.assert_awaited_with('status', {'turn_token': 'verified-token'})

    async def test_other_peer_and_group_cannot_access_secret(self):
        saved = json.loads(await self.plugin.write(event(), '标题', 'private-fixture', 'secret'))
        other = json.loads(await self.plugin.read(event('11111'), entry_id=saved['entry_id']))
        self.assertEqual(other, {'found': False})
        group = event()
        group.get_group_id = lambda: 'a-group'
        self.plugin.client.call.reset_mock()
        result = json.loads(await self.plugin.read(group, entry_id=saved['entry_id']))
        self.assertEqual(result['error'], 'notebook_not_authorized')
        self.plugin.client.call.assert_not_awaited()

    async def test_revoked_or_wrong_turn_proof_cannot_read_or_write(self):
        self.plugin.client.call.side_effect = BridgeError('authorization_required', 403)
        self.assertFalse(json.loads(await self.plugin.write(event(), '标题', '正文'))['saved'])
        self.assertEqual(json.loads(await self.plugin.read(event()))['error'], 'authorization_required')
        self.plugin.client.call.side_effect = None
        self.plugin.client.call.return_value = {'turn_key': 'different-turn'}
        self.assertEqual(json.loads(await self.plugin.read(event()))['error'], 'notebook_not_authorized')
        self.assertEqual(self.plugin.store.read('qq:12345:67890')['entries'], [])

    async def test_reminder_survives_new_requests_without_loading_secret_content(self):
        await self.plugin.write(event(), '秘密', 'private-fixture', 'secret')
        self.plugin.client.call.reset_mock()
        for _ in range(2):
            req = ProviderRequest(prompt='你好', system_prompt='当前晓筱人设')
            await self.plugin.request(event(), req)
            await self.plugin.request(event(), req)
            self.assertEqual(req.system_prompt.count(MARKER), 1)
            self.assertIn('当前晓筱人设', req.system_prompt)
            self.assertNotIn('private-fixture', req.system_prompt)
            self.assertIn('新会话和重启后仍在', req.system_prompt)
        self.plugin.client.call.assert_not_awaited()

    async def test_unprepared_or_unrelated_event_gets_no_reminder(self):
        for scenario in ['no-turn', 'not-primary', 'other-adapter']:
            current = event()
            if scenario == 'no-turn': current.set_extra('youmi_turn', None)
            elif scenario == 'not-primary': current.set_extra('youmi_primary_chat', False)
            else: current.get_platform_id = lambda: 'other'
            req = ProviderRequest(prompt='hi', system_prompt='untouched')
            await self.plugin.request(current, req)
            self.assertEqual(req.system_prompt, 'untouched')

    async def test_scheduled_contact_gets_temporary_reminder_and_can_write(self):
        current = event()
        current.get_platform_name = lambda: 'cron'
        current.set_extra('youmi_primary_chat', False)
        context = SimpleNamespace(messages=[Message(role='system', content='当前人设')])
        await self.plugin.scheduled(current, context)
        await self.plugin.scheduled(current, context)
        messages = [m for m in context.messages if MARKER in str(m.content)]
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0]._no_save)
        self.assertTrue(json.loads(await self.plugin.write(current, '一个想法', '我：这是我的感受。'))['saved'])

    async def test_failed_storage_is_not_reported_as_saved_and_does_not_leak_exception(self):
        with patch.object(self.plugin.store, 'write', side_effect=sqlite3.OperationalError('private-fixture')):
            result = json.loads(await self.plugin.write(event(), '标题', '正文'))
        self.assertEqual(result, {'saved': False, 'error': 'notebook_storage_unavailable'})

    async def test_actual_dispatch_runs_notebook_after_youmi_prepare(self):
        old_handlers = list(star_handlers_registry._handlers)
        old_map, old_stars = dict(star_handlers_registry.star_handlers_map), dict(star_map)
        star_handlers_registry._handlers.clear()
        star_handlers_registry.star_handlers_map.clear()
        async def prepare(current, req):
            current.set_extra('youmi_turn', SimpleNamespace(prepared={'turn_token': 'verified-token', 'turn_key': 'turn-key'}))
        try:
            for module, name, priority, method in [('youmi-fixture', 'prepare', 100000, prepare),
                    ('notebook-fixture', 'request', -100000, self.plugin.request)]:
                star_map[module] = StarMetadata(name=module, activated=True)
                star_handlers_registry.append(StarHandlerMetadata(EventType.OnLLMRequestEvent,
                    module + '.' + name, name, module, method, [], extras_configs={'priority': priority}))
            current = event()
            current.set_extra('youmi_turn', None)
            req = ProviderRequest(prompt='hi', system_prompt='当前人设')
            await call_event_hook(current, EventType.OnLLMRequestEvent, req)
            self.assertIn(MARKER, req.system_prompt)
        finally:
            star_handlers_registry._handlers[:] = old_handlers
            star_handlers_registry.star_handlers_map.clear()
            star_handlers_registry.star_handlers_map.update(old_map)
            star_map.clear()
            star_map.update(old_stars)
