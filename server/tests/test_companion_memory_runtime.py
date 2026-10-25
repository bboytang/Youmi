"""Real-store compatibility gate before replacing the existing memory engine."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from server.companion import affect, character, emotional_system, features, reply
from server.companion.astrbot_api import AstrBotHandler
from server.companion.authorization import PrivateAuthorizationRequired
from server.companion.context import ContextBuilder, ContextError
from server.companion.embedding import EmbeddingUnavailable
from server.companion.install_youmi import install
from server.companion.memory import MemoryStore
from server.companion.memory_runtime import YoumiMemoryRuntime
from server.companion.semantic import SemanticMemoryIndex
from server.tests import test_companion_context as fixture


class MemoryRuntimeCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    fact = fixture.ContextBuilderTests.fact
    build = fixture.ContextBuilderTests.build
    data = fixture.ContextBuilderTests.data

    def setUp(self):
        fixture.ContextBuilderTests.setUp(self)
        self.now = 1800000000
        self.store.clock = lambda: self.now
        self.runtime = YoumiMemoryRuntime(self.index)
        self.builder = ContextBuilder(self.runtime)

    def tearDown(self):
        reply._builders.pop(self.store, None)
        fixture.ContextBuilderTests.tearDown(self)

    def enable_emotions(self):
        state = emotional_system.status(self.store, self.scope.owner)
        return emotional_system.manage(self.store, self.scope.owner, dict(action='configure',
            version=state['version'], enabled=True, dimensions=state['dimensions']))

    async def test_recall_keeps_existing_ranking_sources_and_database_unchanged(self):
        memory = self.fact('我喜欢旅行时带热茶')
        await self.index.rebuild_batch(self.scope, self.auth)
        before = list(self.store.db.iterdump())
        query = '我旅行喜欢带什么？'
        expected = await self.index.retrieve(self.scope, self.auth, query)
        actual = await self.runtime.retrieve(self.scope, self.auth, query)
        self.assertEqual(actual, expected)
        self.assertIn(memory, [row['id'] for row in actual['results']])
        bundle = await self.build(query=query)
        self.assertIn('我喜欢旅行时带热茶', json.dumps(self.data(bundle)['facts'], ensure_ascii=False))
        self.assertEqual(list(self.store.db.iterdump()), before)

    async def test_device_app_and_qq_factory_share_runtime_without_sharing_agents(self):
        own = self.fact('我喜欢热茶')
        foreign = self.fact('我喜欢咖啡', owner='user:1:agent:b')
        other = replace(self.scope, owner='user:1:agent:b', agent_id='b')
        await self.index.rebuild_batch(self.scope, self.auth)
        await self.index.rebuild_batch(other, self.auth)
        with patch.object(reply, '_encoder', self.encoder):
            app = reply.builder_for(self.store)
            device = reply.builder_for(self.store)
            qq = AstrBotHandler({}, {}, store=self.store, authorization=self.auth)
            qq._ledger(self.auth)
        self.assertIs(app, device)
        self.assertIs(app, qq.builder)
        self.assertIsInstance(app.index, YoumiMemoryRuntime)
        for scope in [self.scope, replace(self.scope, device_id='phone'),
                      replace(self.scope, device_id='qq-device', client_id='qq-client')]:
            result = await app.index.retrieve(scope, self.auth, '我喜欢什么？')
            self.assertEqual({row['id'] for row in result['results']}, {own})
        result = await app.index.retrieve(other, self.auth, '我喜欢什么？')
        self.assertEqual({row['id'] for row in result['results']}, {foreign})

    async def test_all_51_emotions_and_verbatim_persona_remain_in_model_context(self):
        persona = '姓名：合成人物\n规则：短句、多分段。\n' * 150
        character.update(self.store, self.scope.owner, dict(enabled=True, version=0,
            values=dict(character.YOUMI_V1, background=persona)))
        state = self.enable_emotions()
        before = emotional_system.status(self.store, self.scope.owner)
        bundle = await self.build(limits=replace(self.limits, context_window=65536, input_tokens=32768))
        projected = json.loads(bundle.inputs_json)['youmi_affect']['affect_system']
        self.assertEqual(set(projected['values']), set(emotional_system.CATALOG))
        self.assertEqual(len(projected['values']), 51)
        self.assertEqual(projected['values'], state['values'])
        self.assertEqual(set(self.data(bundle)['youmi_affect']['affect_system']), set(emotional_system.CATALOG))
        system = bundle.request()['messages'][0]['content']
        self.assertIn(persona, system)
        self.assertIn('affect_system=', system)
        self.assertEqual(emotional_system.status(self.store, self.scope.owner), before)

    async def test_recall_failure_preserves_emotional_state_and_expression(self):
        before = self.enable_emotions()
        self.fact('我喜欢热茶')
        def fail():
            raise EmbeddingUnavailable('synthetic_encoder_failure')
        self.encoder.hook = fail
        bundle = await self.build()
        self.assertEqual(self.data(bundle)['memory_status'], 'unavailable')
        self.assertEqual(json.loads(bundle.inputs_json)['youmi_affect']['affect_system']['values'], before['values'])
        self.assertIn('affect_system=', bundle.request()['messages'][0]['content'])
        self.assertEqual(emotional_system.status(self.store, self.scope.owner)['version'], before['version'])

    async def test_read_disable_and_revocation_are_not_bypassed(self):
        self.fact('我喜欢热茶')
        self.enable_emotions()
        features.update(self.store, self.scope.owner, {'memory_recall': False})
        calls = self.encoder.calls
        bundle = await self.build()
        self.assertEqual(self.encoder.calls, calls)
        self.assertEqual(self.data(bundle)['facts'], [])
        self.assertIn('affect_system=', bundle.request()['messages'][0]['content'])
        features.update(self.store, self.scope.owner, {'memory_recall': True})
        await self.index.rebuild_batch(self.scope, self.auth)
        self.encoder.hook = lambda: setattr(self.auth, 'active', False)
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.runtime.retrieve(self.scope, self.auth, '我喜欢什么？')

    async def test_deletion_and_configuration_changes_invalidate_prepared_context(self):
        memory = self.fact('我喜欢热茶')
        await self.index.rebuild_batch(self.scope, self.auth)
        self.enable_emotions()
        bundle = await self.build()
        self.store.delete_memory(self.scope.owner, memory)
        with self.assertRaisesRegex(ContextError, 'context_changed'):
            await self.builder.validate(bundle, self.auth)
        bundle = await self.build()
        state = emotional_system.status(self.store, self.scope.owner)
        emotional_system.manage(self.store, self.scope.owner, dict(action='adjust', version=state['version'], values={'joy': 40}))
        with self.assertRaisesRegex(ContextError, 'context_changed'):
            await self.builder.validate(bundle, self.auth)

    async def test_projection_stays_ephemeral_and_completion_preserves_modes_and_idempotency(self):
        state = emotional_system.status(self.store, self.scope.owner)
        state['dimensions']['sadness'].update(mode='locked', lock_until=self.now + 3600)
        state['dimensions']['joy'].update(max_change=5, half_life_hours=1)
        state = emotional_system.manage(self.store, self.scope.owner, dict(action='configure',
            version=0, enabled=True, dimensions=state['dimensions']))
        bundle = await self.build(query='谢谢你陪我')
        proposal = emotional_system.parse_appraisal({'changes': [dict(dimension=key, delta=50, quote='谢谢你')
            for key in ['joy', 'sadness', 'warmth']]}, '谢谢你陪我')
        proposal['system_version'] = state['version']
        projected = await self.builder.with_affect(bundle, self.auth, proposal)
        values = json.loads(projected.inputs_json)['youmi_affect']['affect_system']['values']
        self.assertEqual(values['joy'], 15)
        self.assertEqual(values['sadness'], state['values']['sadness'])
        self.assertEqual(values['warmth'], state['values']['warmth'])
        self.assertEqual(emotional_system.status(self.store, self.scope.owner)['values'], state['values'])
        with self.store.lock, self.store.db:
            self.assertTrue(affect.commit(self.store, self.scope.owner, 'qq', 'complete', '谢谢你陪我', proposal, 0))
            self.assertFalse(affect.commit(self.store, self.scope.owner, 'qq', 'complete', '谢谢你陪我', proposal, 0))
        self.now += 3600
        self.assertEqual(emotional_system.status(self.store, self.scope.owner)['values']['joy'], 12.5)

    async def test_reopen_preserves_shared_emotions_and_foreign_agents_stay_disabled(self):
        state = self.enable_emotions()
        emotional_system.manage(self.store, self.scope.owner, dict(action='adjust', version=state['version'], values={'joy': 42}))
        reopened = MemoryStore(Path(self.temp.name) / 'memory.sqlite3', clock=lambda: self.now)
        try:
            runtime = YoumiMemoryRuntime(SemanticMemoryIndex(reopened, self.encoder))
            builder = ContextBuilder(runtime)
            bundle = await builder.build(self.scope, self.auth, 'after-restart', '你好', limits=self.limits)
            self.assertEqual(json.loads(bundle.inputs_json)['youmi_affect']['affect_system']['values']['joy'], 42)
            self.assertFalse(emotional_system.status(reopened, 'user:1:agent:b')['enabled'])
        finally:
            reopened.close()

    async def test_fresh_installer_contains_runtime_and_imports_without_checkout_fallback(self):
        root = Path(self.temp.name) / 'candidate'
        (root / 'main/xiaozhi-server').mkdir(parents=True)
        install(root, {})
        installed = root / 'main/xiaozhi-server'
        result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c',
            'from youmi_companion.reply import builder_for; '
            'from youmi_companion.memory_runtime import YoumiMemoryRuntime; '
            'print(YoumiMemoryRuntime.__module__)'], cwd=root,
            env=dict(os.environ, PYTHONPATH=str(installed)), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'youmi_companion.memory_runtime')


if __name__ == '__main__':
    unittest.main()
