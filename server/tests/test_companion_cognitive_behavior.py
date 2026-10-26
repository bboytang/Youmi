"""Real context/native/bridge policy effects; no claim of QQ sampling overrides."""

import asyncio
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from server.companion import memory_state, reply
from server.companion.cognitive_context import YoumiCognitiveContextBuilder
from server.companion.embedding import EmbeddingUnavailable
from server.companion.context import ContextError, ContextLimits, estimate
from server.companion.memory_emotion_context import YoumiEmotionContextBuilder
from server.companion.memory_runtime import YoumiMemoryRuntime
from server.tests import test_companion_context as fixture
from server.tests import test_companion_reply as native
sys.path.insert(0, str(Path(__file__).parent))
from server.tests import test_astrbot_bridge as bridge


def configure(store, owner, *, recall=7, expression=.5, creativity=.7):
    current = memory_state.cognitive_status(store, owner)
    current['dimensions']['recall']['baseline'] = recall
    current['dimensions']['expression']['baseline'] = expression
    current['dimensions']['creativity']['baseline'] = creativity
    return memory_state._configure_cognition(store, owner, dict(action='configure', version=current['version'],
        enabled=True, dimensions=current['dimensions']))


class CognitiveContextTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.ContextBuilderTests.setUp
    tearDown = fixture.ContextBuilderTests.tearDown
    fact = fixture.ContextBuilderTests.fact

    async def bundle(self, **kwargs):
        return await YoumiCognitiveContextBuilder(YoumiMemoryRuntime(self.index)).build(
            self.scope, self.auth, 'cognitive', '你好', limits=kwargs.pop('limits', self.limits), **kwargs)

    async def test_disabled_policy_keeps_exact_model_request(self):
        runtime = YoumiMemoryRuntime(self.index)
        old = await YoumiEmotionContextBuilder(runtime).build(self.scope, self.auth, 'old', '你好', limits=self.limits)
        new = await self.bundle()
        self.assertEqual(new.request(), old.request())
        self.assertNotIn('temperature', new.request())

    async def test_shared_policy_changes_request_and_keeps_persona_and_budget(self):
        from server.companion import character
        persona = '姓名：完整合成角色\n规则：不改原文\n' * 120
        character.update(self.store, self.scope.owner, dict(enabled=True, version=0,
            values=dict(character.YOUMI_V1, background=persona)))
        configure(self.store, self.scope.owner, expression=.2, creativity=.8)
        bundle = await self.bundle()
        request = bundle.request()
        self.assertIn(persona, request['messages'][0]['content'])
        self.assertEqual(request['messages'][0]['content'].count('【认知表达策略】'), 1)
        self.assertEqual(request['temperature'], .8)
        self.assertEqual(request['max_tokens'], int(self.limits.response_tokens * .4))
        self.assertLessEqual(estimate(request) + request['max_tokens'], self.limits.context_window)
        self.assertEqual(bundle.diagnostics()['cognition']['status'], 'applied')

    async def test_policy_version_change_invalidates_prepared_context(self):
        state = configure(self.store, self.scope.owner)
        builder = YoumiCognitiveContextBuilder(YoumiMemoryRuntime(self.index))
        bundle = await builder.build(self.scope, self.auth, 'cog-version', '你好', limits=self.limits)
        memory_state._configure_cognition(self.store, self.scope.owner,
            dict(action='configure', version=state['version'], enabled=False, dimensions=state['dimensions']))
        with self.assertRaisesRegex(ContextError, 'context_changed'):
            await builder.validate(bundle, self.auth)

    async def test_optional_guidance_is_omitted_instead_of_displacing_mandatory_content(self):
        configure(self.store, self.scope.owner)
        # Find the exact base cost, then leave only 1 extra estimated byte.
        runtime = YoumiMemoryRuntime(self.index)
        old = await YoumiEmotionContextBuilder(runtime).build(self.scope, self.auth, 'base', '你好', limits=self.limits)
        limit = estimate(old.request()) + 1
        bundle = await self.bundle(limits=replace(self.limits, input_tokens=limit))
        self.assertEqual(bundle.diagnostics()['cognition']['status'], 'budget_omitted')
        self.assertNotIn('temperature', bundle.request())
        self.assertEqual(bundle.request()['messages'], old.request()['messages'])

    @unittest.skipUnless(importlib.util.find_spec('tantivy'), 'Optional native shadow dependency')
    async def test_recall_effective_limit_controls_only_qualified_owned_shadow_results(self):
        for index in range(8):
            self.fact('我喜欢热茶' + str(index))
        runtime = YoumiMemoryRuntime(self.index)
        self.assertEqual(len((await runtime.shadow_retrieve(self.scope, self.auth, '热茶', use_vectors=False))['results']), 4)
        configure(self.store, self.scope.owner, recall=1)
        self.assertEqual(len((await runtime.shadow_retrieve(self.scope, self.auth, '热茶', use_vectors=False))['results']), 1)
        configure(self.store, self.scope.owner, recall=7)
        self.assertEqual(len((await runtime.shadow_retrieve(self.scope, self.auth, '热茶', use_vectors=False))['results']), 7)

    @unittest.skipUnless(importlib.util.find_spec('tantivy'), 'Optional native shadow dependency')
    async def test_change_during_shadow_query_rejects_old_cognitive_limit(self):
        from server.companion.memory_shadow import YoumiMemoryShadow
        self.fact('我喜欢热茶')
        configure(self.store, self.scope.owner)
        original = YoumiMemoryShadow._score
        def during(*args, **kwargs):
            configure(self.store, self.scope.owner, recall=1)
            return original(*args, **kwargs)
        with patch.object(YoumiMemoryShadow, '_score', staticmethod(during)):
            with self.assertRaisesRegex(EmbeddingUnavailable, 'memory_changed'):
                await YoumiMemoryRuntime(self.index).shadow_retrieve(self.scope, self.auth, '热茶', use_vectors=False)

    async def test_emotional_projection_does_not_duplicate_guidance_or_change_saved_51_state(self):
        from server.companion import emotional_system
        current = emotional_system.status(self.store, self.scope.owner)
        emotional_system.manage(self.store, self.scope.owner, dict(action='configure', version=current['version'],
            enabled=True, dimensions=current['dimensions']))
        configure(self.store, self.scope.owner)
        builder = YoumiCognitiveContextBuilder(YoumiMemoryRuntime(self.index))
        bundle = await builder.build(self.scope, self.auth, 'affect-cog', '你好', limits=replace(self.limits, context_window=65536, input_tokens=32768))
        before = emotional_system.status(self.store, self.scope.owner)
        changed = await builder.with_affect(bundle, self.auth,
            dict(changes=[dict(dimension='joy', delta=2)], system_version=before['version']))
        self.assertEqual(changed.request()['messages'][0]['content'].count('【认知表达策略】'), 1)
        self.assertEqual(emotional_system.status(self.store, self.scope.owner), before)


class NativeCognitiveGenerationTests(unittest.IsolatedAsyncioTestCase):
    setUp = native.ReplyTests.setUp
    tearDown = native.ReplyTests.tearDown
    write_profile = native.ReplyTests.write_profile
    fact = native.ReplyTests.fact

    async def test_actual_native_provider_receives_per_turn_limits_without_global_mutation(self):
        await self.fact()
        configure(self.store, self.scope.owner, expression=.2, creativity=.9)
        self.conn._youmi_context_builder = YoumiCognitiveContextBuilder(YoumiMemoryRuntime(self.index))
        self.conn.loop = asyncio.get_running_loop()
        response = await asyncio.to_thread(lambda: list(reply.response(self.conn, None, 0)))
        self.assertTrue(response)
        request = self.provider.calls[0]
        self.assertEqual(request['temperature'], .9)
        self.assertEqual(request['max_tokens'], int(1024 * .4))
        self.assertEqual(self.provider.max_tokens, 1024)
        self.assertIn('【认知表达策略】', request['messages'][0]['content'])

    async def test_native_function_path_receives_the_same_per_turn_generation(self):
        await self.fact()
        configure(self.store, self.scope.owner, expression=.2, creativity=.9)
        self.conn._youmi_context_builder = YoumiCognitiveContextBuilder(YoumiMemoryRuntime(self.index))
        self.conn.loop = asyncio.get_running_loop()
        await asyncio.to_thread(lambda: list(reply.response(self.conn, self.tools, 0)))
        self.assertEqual(self.provider.calls[0]['temperature'], .9)
        self.assertEqual(self.provider.calls[0]['tools'], self.tools)

    async def test_native_stream_stops_when_cognitive_settings_change(self):
        await self.fact()
        configure(self.store, self.scope.owner)
        self.conn._youmi_context_builder = YoumiCognitiveContextBuilder(YoumiMemoryRuntime(self.index))
        self.conn.loop = asyncio.get_running_loop()
        self.provider.hook = lambda: configure(self.store, self.scope.owner, expression=.1)
        received = []
        def consume():
            with self.assertRaisesRegex(ContextError, 'context_changed'):
                for delta in reply.response(self.conn, None, 0):
                    received.append(delta)
        await asyncio.to_thread(consume)
        self.assertEqual(received, ['😌第一句。'])
        self.assertEqual(self.provider.closed, 1)


class BridgeCognitiveGenerationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = bridge.BridgeHTTPTests.asyncSetUp
    asyncTearDown = bridge.BridgeHTTPTests.asyncTearDown
    post = bridge.BridgeHTTPTests.post
    prepare = bridge.BridgeHTTPTests.prepare

    async def test_qq_merged_native_system_keeps_the_shared_policy_and_existing_tools(self):
        from integrations.astrbot_plugin_youmi.turn_state import merge_system
        configure(self.store, self.scope.owner, expression=.2, creativity=.8)
        self.handler.builder = YoumiCognitiveContextBuilder(YoumiMemoryRuntime(self.builder.index))
        prepared = await self.prepare()
        system = merge_system('原生工具和表情包规则', prepared['system_prompt'])
        self.assertIn('【认知表达策略】', system)
        self.assertIn('原生工具和表情包规则', system)
        self.assertIn('0.8', system)
        self.assertNotIn('generation_options', prepared)  # No unsupported QQ sampling claim.

    async def test_qq_check_rejects_cognitive_settings_changed_after_prepare(self):
        configure(self.store, self.scope.owner)
        self.handler.builder = YoumiCognitiveContextBuilder(YoumiMemoryRuntime(self.builder.index))
        prepared = await self.prepare()
        configure(self.store, self.scope.owner, expression=.1)
        await self.post('check', dict(turn_token=prepared['turn_token'], final_text='收到'), status=409)
