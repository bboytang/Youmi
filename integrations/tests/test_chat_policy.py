import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from integrations.astrbot_plugin_youmi_chat_policy.policy import PROMPT, apply_policy


ROOT = Path(__file__).resolve().parents[1] / 'astrbot_plugin_youmi_chat_policy'


class ChatPolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_text_and_disabled_default(self):
        self.assertEqual(PROMPT, (ROOT / 'prompt.txt').read_text().rstrip('\n'))
        self.assertTrue(PROMPT.startswith('你是一个面向成年用户的对话和创作助手。'))
        self.assertTrue(PROMPT.endswith('请等待用户提出具体任务。'))
        self.assertEqual(sum(line.startswith(f'{n}. ') for line in PROMPT.splitlines() for n in range(1, 8)), 7)
        self.assertFalse(json.loads((ROOT / '_conf_schema.json').read_text())['enabled']['default'])

    def test_preserves_persona_exact_append_and_no_duplicates(self):
        original = '晓筱的性格与状态'
        self.assertEqual(apply_policy(original, False), original)
        self.assertEqual(apply_policy(original, 'false'), original)
        enabled = apply_policy(original, True)
        self.assertEqual(enabled, original + '\n\n' + PROMPT)
        self.assertEqual(apply_policy(enabled, True), enabled)
        self.assertEqual(apply_policy('', True), PROMPT)
        self.assertEqual(apply_policy(original, False), original)

    async def test_hook_scopes_and_live_toggle(self):
        # Isolate the host API: this tests the plugin hook, not a real model or server.
        class Star:
            def __init__(self, context):
                pass

        modules = {name: types.ModuleType(name) for name in (
            'astrbot', 'astrbot.api', 'astrbot.api.event', 'astrbot.api.provider', 'astrbot.api.star')}
        modules['astrbot.api'].AstrBotConfig = dict
        modules['astrbot.api.event'].AstrMessageEvent = object
        modules['astrbot.api.event'].filter = types.SimpleNamespace(on_llm_request=lambda **kw: lambda f: f)
        modules['astrbot.api.provider'].ProviderRequest = object
        modules['astrbot.api.star'].Star = Star
        name = 'integrations.astrbot_plugin_youmi_chat_policy.main'
        with patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location(name, ROOT / 'main.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        config = {'enabled': True}
        plugin = module.YoumiChatPolicy(None, config)
        for extra in ({}, {'youmi_primary_chat': True}, {'youmi_turn': object()}):
            event = types.SimpleNamespace(get_extra=lambda key, default=None: extra.get(key, default))
            req = types.SimpleNamespace(system_prompt='persona')
            await plugin.request(event, req)
            self.assertEqual(req.system_prompt, 'persona')
        event = types.SimpleNamespace(get_extra=lambda key, default=None: {
            'youmi_primary_chat': True, 'youmi_turn': object()}.get(key, default))
        req = types.SimpleNamespace(system_prompt='persona')
        await plugin.request(event, req)
        await plugin.request(event, req)
        self.assertEqual(req.system_prompt, 'persona\n\n' + PROMPT)
        config['enabled'] = False
        req = types.SimpleNamespace(system_prompt='persona')
        await plugin.request(event, req)
        self.assertEqual(req.system_prompt, 'persona')
