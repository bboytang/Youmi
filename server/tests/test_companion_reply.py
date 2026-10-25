"""Connection bridge mechanisms; provider, policy and vectors are unit fixtures."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import numpy as np

from server.companion import reply, youmi_adapter as youmi
from server.companion.authorization import AuthenticatedScope, PrivateAuthorizationRequired, execute_tool
from server.companion.context import ContextBuilder, ContextError
from server.companion.embedding import DIMENSION, MODEL_KEY, REVISION, EmbeddingUnavailable
from server.companion.memory import MemoryStore
from server.companion.semantic import SemanticMemoryIndex


class UnitEncoder:
    model_id, revision, dimension = MODEL_KEY, REVISION, DIMENSION

    def encode(self, text, query=False):
        result = np.zeros(DIMENSION, dtype=np.float32)
        result[0] = 1
        return result


class Policy:
    active = True

    async def require(self, scope, permission):
        if not self.active or not isinstance(scope, AuthenticatedScope) or permission not in scope.permissions:
            raise PrivateAuthorizationRequired()


class Provider:
    model_name, max_tokens, base_url = 'unit-model', 1024, 'https://unit.invalid/v4'

    def __init__(self):
        self.calls, self.closed = [], 0
        self.hook = None

    def response(self, session, dialogue, **kwargs):
        self.calls.append({'messages': dialogue, **kwargs})
        try:
            yield '😌第一句。'
            if self.hook:
                self.hook()
            yield '第二句。'
        finally:
            self.closed += 1

    def response_with_functions(self, session, dialogue, functions, **kwargs):
        for text in self.response(session, dialogue, tools=functions, **kwargs):
            yield text, None


def dto(role, content=None, calls=None, tool_id=None, marker=None, temporary=False):
    return SimpleNamespace(role=role, content=content, tool_calls=calls, tool_call_id=tool_id,
        uniq_id=marker or role + str(id(content)), is_temporary=temporary)


class ReplyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp.name) / 'memory.sqlite3')
        self.auth, self.provider = Policy(), Provider()
        self.scope = AuthenticatedScope('user:1:agent:a', 1, 1, 'a', 'mia', 'device', 'client', 0, 1,
            datetime.now(timezone.utc) + timedelta(days=1), frozenset({'memory:read', 'memory:write', 'memory:manage'}))
        self.index = SemanticMemoryIndex(self.store, UnitEncoder())
        self.conn = SimpleNamespace(_youmi_scope=self.scope, _youmi_authorization=self.auth, read_config_from_api=True,
            _youmi_context_builder=ContextBuilder(self.index), llm=self.provider, session_id='session', sentence_id='turn',
            _youmi_role_data='中文简短', dialogue=SimpleNamespace(dialogue=[dto('user', '我喜欢什么？', marker='user-1')]))
        self.profile = Path(self.temp.name) / 'profile.json'
        self.write_profile()
        self.env = patch.dict(os.environ, {'MIA_CONTEXT_PROFILE_FILE': str(self.profile)})
        self.env.start()
        self.tools = [{'type': 'function', 'function': {'name': 'weather', 'parameters': {'type': 'object'}}}]

    def tearDown(self):
        self.env.stop()
        self.store.close()
        self.temp.cleanup()

    def write_profile(self, **changes):
        value = {'provider_module': Provider.__module__, 'model': 'unit-model', 'base_url': Provider.base_url,
            'context_window': 16384, 'capacity_source': 'Synthetic unit profile, not production evidence'}
        self.profile.write_text(json.dumps(dict(value, **changes)))

    async def fact(self):
        text = '我喜欢蓝色。'
        marker = self.store.apply_extraction(self.scope.owner, 's', 0, [text],
            {'memories': [{'category': 'fact', 'content': text, 'source_index': 0}]}, 'key')['memories'][0]
        await self.index.rebuild_batch(self.scope, self.auth)
        return marker

    async def test_youmi_profile_environment_takes_precedence_with_legacy_fallback(self):
        with patch.dict(os.environ, {'YOUMI_CONTEXT_PROFILE_FILE': str(self.profile),
                                     'MIA_CONTEXT_PROFILE_FILE': str(self.profile) + '.absent'}):
            turn = await reply.prepare(self.conn, [], 0)
            self.assertEqual(turn.bundle.limits.context_window, 16384)

    def append_tools(self, count=1, prefix='call', text='晴天'):
        self.conn.dialogue.dialogue.append(dto('assistant', calls=[{'id': f'{prefix}-{i}', 'type': 'function',
            'function': {'name': 'weather', 'arguments': '{}'}} for i in range(count)]))
        for i in reversed(range(count)):
            self.conn.dialogue.dialogue.append(dto('tool', text, tool_id=f'{prefix}-{i}'))

    async def test_invalid_or_mismatched_profile_falls_back_to_conservative_default(self):
        for fields in ({'model': 'other'}, {'base_url': 'https://other.invalid'}, {'provider_module': 'other'},
                       {'context_window': None}, {'capacity_source': ''}):
            self.write_profile(**fields)
            turn = await reply.prepare(self.conn, [], 0)
            self.assertEqual(turn.bundle.limits.context_window, reply.DEFAULT_CONTEXT_WINDOW)
            self.assertEqual(turn.bundle.limits.response_tokens, self.provider.max_tokens)
            self.assertEqual(turn.bundle.limits.input_limit(), min(
                reply.DEFAULT_INPUT_TOKENS, reply.DEFAULT_CONTEXT_WINDOW - self.provider.max_tokens))
            self.assertIn('conservative fallback', turn.bundle.limits.capacity_source)
        # A missing output budget falls back to the conservative 2048.
        self.write_profile()
        self.provider.max_tokens = None
        turn = await reply.prepare(self.conn, [], 0)
        self.assertEqual(turn.bundle.limits.response_tokens, reply.DEFAULT_RESPONSE_TOKENS)
        self.assertEqual(self.provider.calls, [])

    async def test_client_profile_is_ignored_and_missing_server_file_uses_default(self):
        self.conn.config = {'context_window': 100000, 'max_tokens': 128}
        self.profile.unlink()
        turn = await reply.prepare(self.conn, [], 0)
        self.assertEqual(turn.bundle.limits.context_window, reply.DEFAULT_CONTEXT_WINDOW)
        self.assertEqual(turn.bundle.limits.response_tokens, self.provider.max_tokens)
        self.assertEqual(turn.bundle.limits.input_limit(), min(
            reply.DEFAULT_INPUT_TOKENS, reply.DEFAULT_CONTEXT_WINDOW - self.provider.max_tokens))
        self.assertEqual(self.provider.calls, [])

    async def test_effective_limits_change_invalidates_turn_and_invalid_profile_falls_back(self):
        self.conn.config = {'input_tokens': 24576, 'context_window': 32768}
        first = await reply.prepare(self.conn, [], 0)
        self.assertEqual(first.bundle.limits.input_limit(), 8192)
        self.write_profile(input_tokens=24576, context_window=32768)
        with self.assertRaisesRegex(ContextError, 'context_limits_changed'):
            await reply.validate(self.conn, first)
        second = await reply.prepare(self.conn, [], 0)
        self.assertEqual(second.bundle.limits.input_limit(), 24576)
        for value in (True, None, 0, '24576'):
            self.write_profile(input_tokens=value)
            with self.subTest(value=value):
                turn = await reply.prepare(self.conn, [], 0)
                self.assertEqual(turn.bundle.limits.context_window, reply.DEFAULT_CONTEXT_WINDOW)
                self.assertEqual(turn.bundle.limits.input_limit(), min(
                    reply.DEFAULT_INPUT_TOKENS,
                    reply.DEFAULT_CONTEXT_WINDOW - self.provider.max_tokens))
        self.assertEqual(self.provider.calls, [])

    async def test_explicit_manager_capacity_overrides_profile_and_catalog(self):
        self.conn._youmi_llm_config = {'model_name': 'unit-model', 'base_url': Provider.base_url,
                                       'context_window': 65536, 'input_tokens': 32768}
        turn = await reply.prepare(self.conn, [], 0)
        self.assertEqual(turn.bundle.limits.context_window, 65536)
        self.assertEqual(turn.bundle.limits.input_limit(), 32768)
        self.write_profile(input_tokens=4096)
        await reply.validate(self.conn, turn)
        self.assertEqual(self.provider.calls, [])

    async def test_invalid_explicit_capacity_falls_back_to_profile(self):
        self.conn._youmi_llm_config = {'model_name': 'unit-model', 'base_url': Provider.base_url,
                                       'context_window': '65536'}
        turn = await reply.prepare(self.conn, [], 0)
        self.assertEqual(turn.bundle.limits.context_window, 16384)
        self.assertEqual(turn.bundle.limits.input_limit(), 8192)

    async def test_catalog_and_conservative_default_cover_models_without_profiles(self):
        self.profile.unlink()
        self.provider.model_name = 'glm-4-flash-250414'
        turn = await reply.prepare(self.conn, [], 0)
        self.assertEqual(turn.bundle.limits.context_window, reply.DEFAULT_CONTEXT_WINDOW)
        self.assertIn('bigmodel', turn.bundle.limits.capacity_source)
        self.provider.model_name = 'doubao-seed-2-1-lite-260915'
        turn = await reply.prepare(self.conn, [], 0)
        self.assertIn('1024K', turn.bundle.limits.capacity_source)
        self.provider.model_name = 'brand-new-model-1'
        turn = await reply.prepare(self.conn, [], 0)
        self.assertEqual(turn.bundle.limits.context_window, reply.DEFAULT_CONTEXT_WINDOW)
        self.assertIn('conservative fallback', turn.bundle.limits.capacity_source)
        self.provider.max_tokens = None
        turn = await reply.prepare(self.conn, [], 0)
        self.assertEqual(turn.bundle.limits.response_tokens, reply.DEFAULT_RESPONSE_TOKENS)
        self.assertEqual(self.provider.calls, [])

    async def test_ten_parallel_and_multistep_continuations_keep_facts_without_reexecuting_tools(self):
        fact = await self.fact()
        first = await reply.prepare(self.conn, self.tools, 0)
        for i in range(10):
            self.append_tools(1 + i % 2, str(i), '' if i == 1 else '晴天')
            turn = await reply.prepare(self.conn, self.tools, i + 1)
            self.assertEqual(turn.bundle.snapshot, first.bundle.snapshot)
            self.assertIn(fact, turn.bundle.diagnostics()['selected_fact_ids'])
            self.assertEqual(turn.bundle.turn_id, 'turn')
        self.assertEqual(self.provider.calls, [])

    async def test_rebuild_after_delete_discards_history_results_and_tools(self):
        fact = await self.fact()
        await reply.prepare(self.conn, self.tools, 0)
        self.append_tools(text='PRIVATE_OLD_TOOL_RESULT')
        self.store.delete_memory(self.scope.owner, fact)
        turn = await reply.prepare(self.conn, self.tools, 1)
        self.assertNotIn(fact, turn.bundle.request_json)
        self.assertNotIn('PRIVATE_OLD_TOOL_RESULT', turn.bundle.request_json)
        self.assertNotIn('tools', turn.bundle.request())
        self.assertEqual(turn.bundle.request()['messages'][-1]['content'], '我喜欢什么？')
        self.conn.func_handler = SimpleNamespace(handle_llm_function_call=lambda *args: self.fail('Tool replayed'))
        with self.assertRaisesRegex(ContextError, 'context_tool_not_declared'):
            await execute_tool(self.conn, {'name': 'weather'})

    async def test_missing_or_modified_tool_result_is_not_repaired(self):
        await reply.prepare(self.conn, self.tools, 0)
        self.append_tools()
        self.conn.dialogue.dialogue.pop()
        with self.assertRaisesRegex(ContextError, 'incomplete_tool_sequence'):
            await reply.prepare(self.conn, self.tools, 1)
        self.conn.dialogue.dialogue[0].content = 'changed question'
        with self.assertRaisesRegex(ContextError, 'context_turn_modified'):
            await reply.prepare(self.conn, self.tools, 1)

    async def test_new_turn_drops_model_history_when_any_snapshot_component_changes(self):
        await reply.prepare(self.conn, [], 0)
        self.conn.dialogue.dialogue.append(dto('assistant', 'OLD_MODEL_PRIVATE'))
        youmi.begin_turn(self.conn, '新问题')
        self.conn.dialogue.dialogue.append(dto('user', '新问题', marker='user-2'))
        self.store.reset_personality(self.scope.owner)
        turn = await reply.prepare(self.conn, [], 0)
        self.assertNotIn('OLD_MODEL_PRIVATE', turn.bundle.request_json)

    async def test_unchanged_snapshot_retains_existing_display_history_without_mutation(self):
        await reply.prepare(self.conn, [], 0)
        self.conn.dialogue.dialogue.append(dto('assistant', '上一轮回答'))
        youmi.begin_turn(self.conn, '下一轮问题')
        self.conn.dialogue.dialogue.extend([dto('user', '临时示例', temporary=True),
                                           dto('user', '下一轮问题', marker='user-2')])
        before = repr(self.conn.dialogue.dialogue)
        turn = await reply.prepare(self.conn, [], 0)
        self.assertIn('上一轮回答', turn.bundle.request_json)
        self.assertNotIn('临时示例', turn.bundle.request_json)
        self.assertEqual(before, repr(self.conn.dialogue.dialogue))

    async def test_revocation_precedes_database_or_provider_access(self):
        await reply.prepare(self.conn, [], 0)
        self.auth.active = False
        self.store.close()
        with self.assertRaises(PrivateAuthorizationRequired):
            await reply.prepare(self.conn, [], 1)
        self.assertEqual(self.provider.calls, [])

    async def test_stream_stops_and_closes_on_deletion_before_next_delivery(self):
        fact = await self.fact()
        self.conn.loop = asyncio.get_running_loop()
        self.provider.hook = lambda: self.store.delete_memory(self.scope.owner, fact)
        delivered = []
        def consume():
            with self.assertRaisesRegex(ContextError, 'context_changed'):
                for delta in reply.response(self.conn, None, 0):
                    delivered.append(delta)
        await asyncio.to_thread(consume)
        self.assertEqual(delivered, ['😌第一句。'])
        self.assertEqual(self.provider.closed, 1)
        self.assertEqual(self.provider.calls[0]['max_tokens'], 1024)

    async def test_each_validation_checks_current_permission_once_without_reusing_proof(self):
        await self.fact()
        turn = await reply.prepare(self.conn, [], 0)
        self.auth.require = AsyncMock(wraps=self.auth.require)
        for count in (1, 2):
            await reply.validate(self.conn, turn)
            self.assertEqual(self.auth.require.await_count, count)
        self.auth.active = False
        with self.assertRaises(PrivateAuthorizationRequired):
            await reply.validate(self.conn, turn)
        self.assertEqual(self.auth.require.await_count, 3)

    async def test_stream_stops_on_revocation_before_next_delivery_and_releases_slot(self):
        self.conn.loop = asyncio.get_running_loop()
        self.provider.hook = lambda: setattr(self.auth, 'active', False)
        delivered = []
        def consume():
            with self.assertRaises(PrivateAuthorizationRequired):
                for delta in reply.response(self.conn, None, 0):
                    delivered.append(delta)
        await asyncio.to_thread(consume)
        self.assertEqual(delivered, ['😌第一句。'])
        self.assertEqual(self.provider.closed, 1)
        self.assertEqual(reply.model_requests.snapshot()['active'], 0)

    async def test_scope_or_authorization_changed_during_await_cannot_deliver_old_context(self):
        turn = await reply.prepare(self.conn, [], 0)
        original = self.auth.require
        for name, value in (('_youmi_scope', None), ('_youmi_authorization', Policy())):
            self.conn._youmi_scope, self.conn._youmi_authorization = self.scope, self.auth
            async def require(scope, permission):
                await original(scope, permission)
                setattr(self.conn, name, value)
            self.auth.require = require
            with self.subTest(changed=name), self.assertRaisesRegex(ContextError, 'context_turn_unavailable'):
                await reply.validate(self.conn, turn)

    async def test_tool_dispatch_rechecks_revision_authorization_and_declared_tools(self):
        fact = await self.fact()
        await reply.prepare(self.conn, self.tools, 0)
        calls = []
        async def dispatch(*args):
            calls.append(args)
            return 'success'
        self.conn.func_handler = SimpleNamespace(handle_llm_function_call=dispatch)
        self.assertEqual(await execute_tool(self.conn, {'name': 'weather'}), 'success')
        self.store.delete_memory(self.scope.owner, fact)
        with self.assertRaisesRegex(ContextError, 'context_changed'):
            await execute_tool(self.conn, {'name': 'weather'})
        self.auth.active = False
        with self.assertRaises(PrivateAuthorizationRequired):
            await execute_tool(self.conn, {'name': 'weather'})
        self.assertEqual(len(calls), 1)

    async def test_database_outage_stops_before_reverting_to_a_different_character(self):
        self.conn._youmi_context_builder = reply.builder_for(None)
        self.conn.dialogue.dialogue.insert(0, dto('user', 'OLD_PRIVATE'))
        with self.assertRaisesRegex(ContextError, 'character_unavailable'):
            await reply.prepare(self.conn, [], 0)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(EmbeddingUnavailable):
                reply.OfflineEncoder().encode('hello')

    async def test_role_capture_is_idempotent_and_keeps_memory_out_of_style(self):
        self.conn.prompt = '中文< memory>保留普通文本</ memory><memory>STALE_FACT</memory>'
        prompt = youmi.compose_persona(self.conn, self.conn.prompt)
        youmi.compose_persona(self.conn, prompt)
        self.assertNotIn('STALE_FACT', self.conn._youmi_role_data)
        self.assertNotIn('你的名字是 Youmi', self.conn._youmi_role_data)


if __name__ == '__main__':
    unittest.main()
