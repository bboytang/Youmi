import asyncio
import importlib
import sys
import types
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from integrations.astrbot_plugin_youmi_autosession.rotation import rotate


class Locks:
    def __init__(self):
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire_lock(self, origin):
        async with self.lock:
            yield


class RotationTests(IsolatedAsyncioTestCase):
    def manager(self):
        return SimpleNamespace(get_curr_conversation_id=AsyncMock(return_value='old'),
            get_conversation=AsyncMock(return_value=SimpleNamespace(persona_id='persona')),
            new_conversation=AsyncMock())

    async def test_rotation_preserves_persona(self):
        manager = self.manager()
        self.assertTrue(await rotate(manager, Locks(), 'qq:private:1', 'qq', 'old'))
        manager.new_conversation.assert_awaited_once_with('qq:private:1', 'qq', persona_id='persona')

    async def test_does_not_override_another_session_change(self):
        manager = self.manager()
        manager.get_curr_conversation_id.return_value = 'newer'
        self.assertFalse(await rotate(manager, Locks(), 'qq:private:1', 'qq', 'old'))
        manager.new_conversation.assert_not_awaited()


class HookTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        def decorator(**kwargs):
            return lambda function: function

        fake_filter = SimpleNamespace(on_agent_done=decorator, after_message_sent=decorator)
        class Star:
            def __init__(self, context):
                self.context = context
        modules = {
            'astrbot': types.ModuleType('astrbot'),
            'astrbot.api': SimpleNamespace(logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)),
            'astrbot.api.event': SimpleNamespace(filter=fake_filter),
            'astrbot.api.star': SimpleNamespace(Star=Star),
            'astrbot.core': types.ModuleType('astrbot.core'),
            'astrbot.core.utils': types.ModuleType('astrbot.core.utils'),
            'astrbot.core.utils.session_lock': SimpleNamespace(session_lock_manager=Locks()),
        }
        self.modules = patch.dict(sys.modules, modules)
        self.modules.start()
        sys.modules.pop('integrations.astrbot_plugin_youmi_autosession.main', None)
        self.module = importlib.import_module('integrations.astrbot_plugin_youmi_autosession.main')
        self.manager = RotationTests.manager(self)
        self.plugin = self.module.YoumiAutoSession(SimpleNamespace(conversation_manager=self.manager), {})
        self.extras = {'youmi_primary_chat': True, 'youmi_turn': SimpleNamespace(source_kind='text', agent_done=True, sent=True)}
        self.event = SimpleNamespace(unified_msg_origin='qq:private:1', get_platform_id=lambda: 'qq',
            get_extra=lambda key, default=None: self.extras.get(key, default),
            set_extra=lambda key, value: self.extras.__setitem__(key, value))

    async def asyncTearDown(self):
        await self.plugin.terminate()
        sys.modules.pop('integrations.astrbot_plugin_youmi_autosession.main', None)
        self.modules.stop()

    async def test_threshold_rotates_only_after_successful_send(self):
        response = SimpleNamespace(usage=SimpleNamespace(input=32000))
        await self.plugin.agent_done(self.event, None, response)
        self.manager.new_conversation.assert_not_awaited()
        await self.plugin.after(self.event)
        await asyncio.gather(*self.plugin.rotation_tasks)
        self.manager.new_conversation.assert_awaited_once()
        await self.plugin.after(self.event)
        self.manager.new_conversation.assert_awaited_once()

    async def test_under_limit_missing_usage_and_scheduled_do_not_rotate(self):
        for usage in (None, SimpleNamespace(input=31999)):
            await self.plugin.agent_done(self.event, None, SimpleNamespace(usage=usage))
        self.extras['youmi_turn'].source_kind = 'scheduled'
        await self.plugin.agent_done(self.event, None, SimpleNamespace(usage=SimpleNamespace(input=32000)))
        await self.plugin.after(self.event)
        self.manager.new_conversation.assert_not_awaited()

    async def test_after_hook_does_not_deadlock_when_session_lock_is_held(self):
        lock_manager = self.module.session_lock_manager
        await self.plugin.agent_done(self.event, None, SimpleNamespace(usage=SimpleNamespace(input=32000)))
        async with lock_manager.acquire_lock(self.event.unified_msg_origin):
            await asyncio.wait_for(self.plugin.after(self.event), timeout=0.1)
            await asyncio.sleep(0)
            self.manager.new_conversation.assert_not_awaited()
        await asyncio.gather(*self.plugin.rotation_tasks)
        self.manager.new_conversation.assert_awaited_once()

    async def test_failed_send_does_not_rotate(self):
        await self.plugin.agent_done(self.event, None, SimpleNamespace(usage=SimpleNamespace(input=32000)))
        self.extras['youmi_turn'].sent = False
        await self.plugin.after(self.event)
        self.manager.new_conversation.assert_not_awaited()

    async def test_no_rotation_when_original_was_removed(self):
        self.manager.get_conversation.return_value = None
        self.assertFalse(await rotate(self.manager, Locks(), 'qq:private:1', 'qq', 'old'))
        self.manager.new_conversation.assert_not_awaited()
