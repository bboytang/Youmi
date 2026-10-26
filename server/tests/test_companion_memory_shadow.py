"""Real canonical SQLite and native Tantivy; no AstrBot, model or production IO."""

import asyncio
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from server.companion import emotional_system, features
from server.companion.authorization import PrivateAuthorizationRequired
from server.companion.context import ContextBuilder
from server.companion.embedding import EmbeddingUnavailable
from server.companion.install_youmi import install
from server.companion.memory import MemoryStore
from server.companion.memory_repository import YoumiMemoryRepository
from server.companion.memory_runtime import YoumiMemoryRuntime
from server.companion.memory_shadow import YoumiMemoryShadow
from server.companion.semantic import SemanticMemoryIndex
from server.tests import test_companion_context as fixture


class RepositoryTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.ContextBuilderTests.setUp
    tearDown = fixture.ContextBuilderTests.tearDown
    fact = fixture.ContextBuilderTests.fact

    async def test_ids_sources_and_versions_survive_read_only_projection(self):
        memory = self.fact('喜欢旅行时带热茶')
        before = list(self.store.db.iterdump())
        repository = YoumiMemoryRepository(self.store)
        corpus = await repository.capture(self.scope, self.auth)
        rows = await repository.get_by_ids(self.scope, self.auth, corpus, [memory])
        self.assertEqual(rows[0]['id'], memory)
        self.assertEqual(rows[0]['content_version'], 1)
        self.assertGreater(rows[0]['evidence_version'], 1)
        self.assertEqual(rows[0]['source']['excerpt'], '喜欢旅行时带热茶')
        self.assertEqual(rows[0]['source']['session_id'], 'session-1')
        self.assertTrue(rows[0]['source']['fingerprint'])
        self.assertEqual(rows[0]['evidence_source_count'], 1)
        self.assertEqual(list(self.store.db.iterdump()), before)

    async def test_same_text_different_agents_and_foreign_ids_never_mix(self):
        own = self.fact('喜欢热茶')
        foreign = self.fact('喜欢热茶', owner='user:1:agent:b')
        public = self.fact('公共合成资料', owner='public')
        repository = YoumiMemoryRepository(self.store)
        corpus = await repository.capture(self.scope, self.auth)
        self.assertEqual([row['id'] for row in corpus.records], [own])
        for memory in [foreign, public, 'missing-id']:
            with self.subTest(memory=memory), self.assertRaisesRegex(EmbeddingUnavailable, 'memory_changed'):
                await repository.get_by_ids(self.scope, self.auth, corpus, [memory])
        other = replace(self.scope, owner='user:1:agent:b', agent_id='b')
        with self.assertRaises(PrivateAuthorizationRequired):
            await repository.get_by_ids(other, self.auth, corpus, [foreign])
        other_corpus = await repository.capture(other, self.auth)
        self.assertEqual([row['id'] for row in other_corpus.records], [foreign])

    async def test_pending_superseded_and_invalidated_memories_are_excluded(self):
        active = self.fact('喜欢热茶')
        for state in ['pending', 'superseded', 'invalidated']:
            memory = self.fact('合成状态：' + state)
            with self.store.db:
                self.store.db.execute('UPDATE mia_memory_meta SET state=? WHERE memory_id=?', (state, memory))
        corpus = await YoumiMemoryRepository(self.store).capture(self.scope, self.auth)
        self.assertEqual([row['id'] for row in corpus.records], [active])

    async def test_corrections_and_source_deletion_invalidate_old_projection(self):
        memory = self.fact('喜欢咖啡')
        repository = YoumiMemoryRepository(self.store)
        corpus = await repository.capture(self.scope, self.auth)
        self.store.edit_memory(self.scope.owner, memory, '喜欢热茶')
        with self.assertRaisesRegex(EmbeddingUnavailable, 'memory_changed'):
            await repository.get_by_ids(self.scope, self.auth, corpus, [memory])
        changed = await repository.capture(self.scope, self.auth)
        self.assertEqual(changed.records[0]['id'], memory)
        self.assertEqual(changed.records[0]['content'], '喜欢热茶')
        self.assertGreater(changed.records[0]['content_version'], corpus.records[0]['content_version'])
        # Use a new, source-backed record to exercise evidence-only invalidation.
        second = self.fact('喜欢旅行')
        changed = await repository.capture(self.scope, self.auth)
        with self.store.db:
            self.store.db.execute('DELETE FROM sources WHERE memory_id=?', (second,))
        with self.assertRaisesRegex(EmbeddingUnavailable, 'memory_changed'):
            await repository.validate(self.scope, self.auth, changed)

    async def test_empty_owner_capture_never_creates_rows_and_forged_owner_is_rejected(self):
        repository = YoumiMemoryRepository(self.store)
        before = list(self.store.db.iterdump())
        corpus = await repository.capture(self.scope, self.auth)
        self.assertEqual(corpus.records, ())
        self.assertEqual(list(self.store.db.iterdump()), before)
        with self.assertRaises(PrivateAuthorizationRequired):
            await repository.capture(replace(self.scope, owner='public'), self.auth)

    async def test_bounds_fail_explicitly_instead_of_truncating_owner_corpus(self):
        self.fact('喜欢热茶')
        self.fact('喜欢旅行')
        repository = YoumiMemoryRepository(self.store)
        for name, value in [('MAX_RECORDS', 1), ('MAX_CORPUS_BYTES', 1)]:
            with patch('server.companion.memory_repository.' + name, value):
                with self.assertRaisesRegex(EmbeddingUnavailable, 'memory_shadow_budget_exceeded'):
                    await repository.capture(self.scope, self.auth)

    async def test_revocation_after_capture_and_invalid_id_requests_are_rejected(self):
        memory = self.fact('喜欢旅行')
        repository = YoumiMemoryRepository(self.store)
        corpus = await repository.capture(self.scope, self.auth)
        for ids in [[memory, memory], [''] , 'arbitrary-query']:
            with self.assertRaises(ValueError):
                await repository.get_by_ids(self.scope, self.auth, corpus, ids)
        self.auth.active = False
        with self.assertRaises(PrivateAuthorizationRequired):
            await repository.get_by_ids(self.scope, self.auth, corpus, [memory])


@unittest.skipUnless(importlib.util.find_spec('tantivy'), 'Optional native shadow dependency is not installed')
class ShadowRetrievalTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.ContextBuilderTests.setUp
    tearDown = fixture.ContextBuilderTests.tearDown
    fact = fixture.ContextBuilderTests.fact

    async def test_chinese_width_and_punctuation_queries_use_actual_bm25_and_sources(self):
        tea = self.fact('喜欢旅行时带热茶，装备型号ＡＢＣ１２３')
        self.fact('喜欢咖啡')
        runtime = YoumiMemoryRuntime(self.index)
        before = list(self.store.db.iterdump())
        for query in ['旅行热茶', 'ABC123', '+旅行:(热茶)']:
            result = await runtime.shadow_retrieve(self.scope, self.auth, query, use_vectors=False)
            self.assertTrue(result['shadow_only'])
            self.assertEqual(result['results'][0]['id'], tea)
            self.assertEqual(result['results'][0]['source']['excerpt'], '喜欢旅行时带热茶，装备型号ＡＢＣ１２３')
            self.assertGreater(result['results'][0]['retrieval']['bm25_score'], 0)
        self.assertEqual(list(self.store.db.iterdump()), before)

    async def test_vector_only_candidate_is_not_lost_when_bm25_has_no_hit(self):
        mountain = self.fact('登山')
        await self.index.rebuild_batch(self.scope, self.auth)
        result = await YoumiMemoryRuntime(self.index).shadow_retrieve(self.scope, self.auth, '徒步')
        self.assertEqual(result['vector_status'], 'ready')
        self.assertEqual(result['results'][0]['id'], mountain)
        self.assertEqual(result['results'][0]['retrieval']['bm25_score'], 0)
        self.assertEqual(result['results'][0]['retrieval']['score_kind'], 'rrf')

    async def test_ready_empty_vector_candidates_do_not_turn_common_characters_into_evidence(self):
        self.fact('我的朋友喜欢绿色的植物')
        async def no_candidates(scope, authorization, query, **kwargs):
            return dict(snapshot=dict(zip(('revision', 'content_epoch'), self.index._snapshot(scope))), candidates=[])
        with patch.object(self.index, 'candidates', side_effect=no_candidates):
            result = await YoumiMemoryRuntime(self.index).shadow_retrieve(self.scope, self.auth, '光的传播速度是什么？')
        self.assertEqual(result['results'], [])
        self.assertEqual(result['memory_status'], 'no_match')
        self.assertEqual(result['vector_status'], 'ready')

    async def test_rrf_cannot_add_a_lexical_match_without_semantic_qualification(self):
        from server.companion.memory_search import YoumiBM25Search
        engine = YoumiBM25Search()
        engine.rebuild([dict(id='lexical-only', judgment='我的热茶', tags=[]),
            dict(id='semantic', judgment='登山', tags=[])])
        results = engine.search('热茶', {'semantic': .6})
        self.assertEqual([row['id'] for row in results], ['semantic'])

    async def test_equal_rrf_uses_lexical_evidence_before_arbitrary_ids(self):
        from server.companion.memory_search import YoumiBM25Search
        for stronger, weaker in [('a', 'z'), ('z', 'a')]:
            rows = [dict(item_id=stronger, bm25_score=20, bm25_normalized_score=1),
                dict(item_id=weaker, bm25_score=11, bm25_normalized_score=.55)]
            results = YoumiBM25Search._rrf_fuse(rows, {weaker: .50, stronger: .47}, threshold=.5, limit=4)
            self.assertEqual(results[0]['final_score'], results[1]['final_score'])
            self.assertEqual(results[0]['id'], stronger)

    async def test_missing_vectors_are_unavailable_and_explicit_lexical_evaluation_remains_separate(self):
        tea = self.fact('喜欢热茶')  # Intentionally no vector index yet.
        runtime = YoumiMemoryRuntime(self.index)
        with self.assertRaises(EmbeddingUnavailable):
            await runtime.shadow_retrieve(self.scope, self.auth, '热茶')
        result = await runtime.shadow_retrieve(self.scope, self.auth, '热茶', use_vectors=False)
        self.assertEqual(result['vector_status'], 'not_requested')
        self.assertEqual(result['results'][0]['id'], tea)
        result = await runtime.shadow_retrieve(self.scope, self.auth, '滑雪', use_vectors=False)
        self.assertEqual(result['memory_status'], 'no_match')
        self.assertEqual(result['results'], [])

    async def test_canonical_restart_and_owner_switch_do_not_reuse_foreign_index(self):
        own = self.fact('喜欢热茶')
        foreign = self.fact('喜欢热茶', owner='user:1:agent:b')
        other = replace(self.scope, owner='user:1:agent:b', agent_id='b')
        runtime = YoumiMemoryRuntime(self.index)
        for scope, expected in [(self.scope, own), (other, foreign), (self.scope, own)]:
            result = await runtime.shadow_retrieve(scope, self.auth, '热茶', use_vectors=False)
            self.assertEqual([row['id'] for row in result['results']], [expected])
        reopened = MemoryStore(Path(self.temp.name) / 'memory.sqlite3')
        try:
            runtime = YoumiMemoryRuntime(SemanticMemoryIndex(reopened, self.encoder))
            result = await runtime.shadow_retrieve(self.scope, self.auth, '热茶', use_vectors=False)
            self.assertEqual([row['id'] for row in result['results']], [own])
        finally:
            reopened.close()

    async def test_delete_during_native_search_cannot_return_old_fact(self):
        memory = self.fact('喜欢热茶')
        shadow = YoumiMemoryShadow(self.index)
        original = shadow._score
        def delete_after_search(*args):
            result = original(*args)
            self.store.delete_memory(self.scope.owner, memory)
            return result
        with patch.object(shadow, '_score', delete_after_search):
            with self.assertRaisesRegex(EmbeddingUnavailable, 'memory_changed'):
                await shadow.retrieve(self.scope, self.auth, '热茶', use_vectors=False)

    async def test_revocation_during_native_search_never_returns_private_results(self):
        self.fact('喜欢热茶')
        shadow = YoumiMemoryShadow(self.index)
        original = shadow._score
        def revoke_after_search(*args):
            result = original(*args)
            self.auth.active = False
            return result
        with patch.object(shadow, '_score', revoke_after_search):
            with self.assertRaises(PrivateAuthorizationRequired):
                await shadow.retrieve(self.scope, self.auth, '热茶', use_vectors=False)

    async def test_shadow_does_not_change_51_emotions_or_default_reply_context(self):
        self.fact('喜欢热茶')
        await self.index.rebuild_batch(self.scope, self.auth)
        state = emotional_system.status(self.store, self.scope.owner)
        emotional_system.manage(self.store, self.scope.owner, dict(action='configure',
            version=0, enabled=True, dimensions=state['dimensions']))
        runtime = YoumiMemoryRuntime(self.index)
        builder = ContextBuilder(runtime)
        before = await builder.build(self.scope, self.auth, 'before', '热茶', limits=self.limits)
        database = list(self.store.db.iterdump())
        await runtime.shadow_retrieve(self.scope, self.auth, '热茶')
        after = await builder.build(self.scope, self.auth, 'after', '热茶', limits=self.limits)
        self.assertEqual(before.request(), after.request())
        self.assertEqual(list(self.store.db.iterdump()), database)
        state = json.loads(after.inputs_json)['youmi_affect']['affect_system']
        self.assertEqual(len(state['values']), 51)

    async def test_disabled_recall_and_search_failure_preserve_emotions(self):
        self.fact('喜欢热茶')
        runtime = YoumiMemoryRuntime(self.index)
        features.update(self.store, self.scope.owner, {'memory_recall': False})
        calls = self.encoder.calls
        with patch.object(YoumiMemoryShadow, '_score', side_effect=AssertionError('must not run')):
            result = await runtime.shadow_retrieve(self.scope, self.auth, '热茶')
        self.assertEqual(result['memory_status'], 'disabled')
        self.assertEqual(self.encoder.calls, calls)
        features.update(self.store, self.scope.owner, {'memory_recall': True})
        from server.companion.memory_search import YoumiBM25Search
        with patch.object(YoumiBM25Search, 'search', side_effect=RuntimeError('synthetic index failure')):
            with self.assertRaisesRegex(EmbeddingUnavailable, 'memory_shadow_search_unavailable'):
                await runtime.shadow_retrieve(self.scope, self.auth, '热茶', use_vectors=False)

    async def test_cancelled_native_search_keeps_its_slot_until_it_really_finishes(self):
        self.fact('喜欢热茶')
        shadow = YoumiMemoryShadow(self.index)
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        original = shadow._score
        def blocked(*args):
            started.set()
            try:
                if not release.wait(3):
                    raise RuntimeError('Synthetic release timed out')
                return original(*args)
            finally:
                finished.set()
        with patch.object(shadow, '_score', blocked):
            task = asyncio.create_task(shadow.retrieve(self.scope, self.auth, '热茶', use_vectors=False))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                with self.assertRaisesRegex(EmbeddingUnavailable, 'memory_shadow_busy'):
                    await shadow.retrieve(self.scope, self.auth, '热茶', use_vectors=False)
            finally:
                release.set()
                self.assertTrue(await asyncio.to_thread(finished.wait, 3))
        await asyncio.sleep(0)
        result = await shadow.retrieve(self.scope, self.auth, '热茶', use_vectors=False)
        self.assertEqual(result['memory_status'], 'matched')

    async def test_installed_shadow_includes_source_license_and_imports_independently(self):
        root = Path(self.temp.name) / 'candidate'
        installed = root / 'main/xiaozhi-server'
        installed.mkdir(parents=True)
        install(root, {})
        package = installed / 'youmi_companion'
        for name in ['memory_repository.py', 'memory_shadow.py', 'memory_search.py', 'memory_index_cache.py', 'memory_cache_invalidations.py', 'schema-v15.sql', 'schema-v16.sql', 'schema-v17.sql', 'cognition.py', 'cognitive_context.py', 'memory_pins.py', 'memory_cache_lifecycle.py', 'memory_cache_cleanup.py',
                     'YOUMI_MEMORY_LICENSE', 'YOUMI_MEMORY_NOTICE.md', 'requirements-memory-shadow.txt']:
            self.assertTrue((package / name).is_file(), name)
        self.assertIn('GNU GENERAL PUBLIC LICENSE', (package / 'YOUMI_MEMORY_LICENSE').read_text())
        self.assertIn('17454a12669c9a7f0f2e2fbe6a424f6f8f4fffe5', (package / 'YOUMI_MEMORY_NOTICE.md').read_text())
        result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c',
            'from youmi_companion.memory_search import YoumiBM25Search; '
            's=YoumiBM25Search(); s.rebuild([dict(id="kept-id",judgment="热茶",tags=[])]); '
            'print(s.search("热茶",{})[0]["id"])'], cwd=root,
            env=dict(os.environ, PYTHONPATH=str(installed) + os.pathsep + os.environ.get('PYTHONPATH', '')),
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'kept-id')


if __name__ == '__main__':
    unittest.main()
