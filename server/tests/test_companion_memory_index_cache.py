"""Native disk cache, canonical isolation and restart/fault recovery."""

import asyncio
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from server.companion import emotional_system, features
from server.companion.authorization import PrivateAuthorizationRequired
from server.companion.embedding import EmbeddingUnavailable
from server.companion.memory import MemoryStore
from server.companion.memory_index_cache import partition
from server.companion.memory_runtime import YoumiMemoryRuntime
from server.companion.memory_shadow import YoumiMemoryShadow
from server.companion.semantic import SemanticMemoryIndex
from server.tests import test_companion_context as fixture


@unittest.skipUnless(importlib.util.find_spec('tantivy'), 'Optional native shadow dependency is not installed')
class IndexCacheTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.ContextBuilderTests.setUp
    tearDown = fixture.ContextBuilderTests.tearDown
    fact = fixture.ContextBuilderTests.fact

    @property
    def root(self):
        return Path(self.temp.name) / 'shadow-cache'

    def directory(self, scope=None, database=None):
        scope = scope or self.scope
        return partition(self.root, database or Path(self.temp.name) / 'memory.sqlite3', scope.owner, scope.persona_id)

    async def retrieve(self, query='热茶', runtime=None, scope=None):
        runtime = runtime or YoumiMemoryRuntime(self.index, shadow_cache_dir=self.root)
        return await runtime.shadow_retrieve(scope or self.scope, self.auth, query, use_vectors=False)

    def manifest(self, scope=None):
        return json.loads((self.directory(scope) / 'manifest.json').read_text())

    async def test_first_build_and_reopen_reuse_index_without_writer_or_canonical_changes(self):
        from server.companion.memory_search import YoumiBM25Search
        identifier = self.fact('我喜欢热茶')
        before = list(self.store.db.iterdump())
        first = await self.retrieve()
        self.assertEqual(first['results'][0]['id'], identifier)
        self.assertEqual(set(self.manifest()), {'format', 'hashes'})
        self.assertNotIn('我喜欢热茶', (self.directory() / 'manifest.json').read_text())
        self.assertEqual(self.directory().stat().st_mode & 0o777, 0o700)
        reopened = MemoryStore(Path(self.temp.name) / 'memory.sqlite3')
        try:
            runtime = YoumiMemoryRuntime(SemanticMemoryIndex(reopened, self.encoder), shadow_cache_dir=self.root)
            with patch.object(YoumiBM25Search, 'rebuild', side_effect=AssertionError('must reuse')), \
                    patch.object(YoumiBM25Search, 'append', side_effect=AssertionError('must not append')):
                self.assertEqual(await self.retrieve(runtime=runtime), first)
        finally:
            reopened.close()
        self.assertEqual(list(self.store.db.iterdump()), before)

    async def test_new_fact_appends_only_new_id_and_evidence_only_change_reuses_index(self):
        from server.companion.memory_search import YoumiBM25Search
        self.fact('我喜欢热茶')
        await self.retrieve()
        coffee = self.fact('我喜欢咖啡')
        calls = []
        original = YoumiBM25Search.append
        def append(engine, rows):
            calls.append([row['id'] for row in rows])
            return original(engine, rows)
        with patch.object(YoumiBM25Search, 'append', append), patch.object(YoumiBM25Search, 'rebuild', side_effect=AssertionError('no rebuild')):
            result = await self.retrieve('咖啡')
        self.assertEqual(calls, [[coffee]])
        self.assertEqual(result['results'][0]['id'], coffee)
        with self.store.db:
            self.store.db.execute('INSERT INTO sources VALUES (?,?,?,?,?)', (coffee, 'corroboration', 'new-proof', '我喜欢咖啡', 999))
        with patch.object(YoumiBM25Search, 'rebuild', side_effect=AssertionError('no rebuild')), \
                patch.object(YoumiBM25Search, 'append', side_effect=AssertionError('no append')):
            updated = (await self.retrieve('咖啡'))['results'][0]
        self.assertGreater(updated['evidence_version'], result['results'][0]['evidence_version'])
        self.assertEqual(updated['evidence_source_count'], 2)

    async def test_correction_and_metadata_change_rebuild_and_remove_historical_segments(self):
        from server.companion.memory_search import YoumiBM25Search
        identifier = self.fact('我喜欢热茶')
        await self.retrieve()
        marker = self.directory() / 'index' / 'old-segment-marker'
        marker.write_text('discard derived data')
        self.store.edit_memory(self.scope.owner, identifier, '我喜欢咖啡')
        result = await self.retrieve('咖啡')
        self.assertEqual(result['results'][0]['id'], identifier)
        self.assertFalse(marker.exists())
        self.assertEqual((await self.retrieve('热茶'))['results'], [])
        with self.store.db:
            self.store.db.execute('UPDATE mia_memory_meta SET keywords=? WHERE memory_id=?', (json.dumps(['登山']), identifier))
        original = YoumiBM25Search.rebuild
        calls = []
        def rebuild(engine, rows):
            calls.append(len(rows)); return original(engine, rows)
        with patch.object(YoumiBM25Search, 'rebuild', rebuild):
            result = await self.retrieve('登山')
        self.assertEqual(calls, [1])
        self.assertEqual(result['results'][0]['id'], identifier)

    async def test_delete_and_pending_records_leave_no_cached_document_after_sync(self):
        first, second = self.fact('我喜欢热茶'), self.fact('我喜欢咖啡')
        await self.retrieve()
        self.store.delete_memory(self.scope.owner, first)
        self.store.edit_memory(self.scope.owner, second, '我喜欢咖啡', confirmed=False)
        result = await self.retrieve()
        self.assertEqual(result['results'], [])
        self.assertEqual(self.manifest()['hashes'], {})
        from server.companion.memory_search import YoumiBM25Search
        self.assertEqual(YoumiBM25Search(self.directory() / 'index')._index.searcher().num_docs, 0)

    async def test_disabled_recall_reconciles_empty_without_vector_or_original_text(self):
        self.fact('我喜欢热茶')
        await self.retrieve()
        features.update(self.store, self.scope.owner, {'memory_recall': False})
        calls = self.encoder.calls
        result = await self.retrieve()
        self.assertEqual(result['memory_status'], 'disabled')
        self.assertEqual(self.manifest()['hashes'], {})
        self.assertEqual(self.encoder.calls, calls)
        features.update(self.store, self.scope.owner, {'memory_recall': True})
        self.assertEqual((await self.retrieve())['memory_status'], 'matched')

    async def test_agents_and_databases_have_distinct_private_partitions(self):
        own = self.fact('我喜欢热茶')
        peer = replace(self.scope, owner='user:1:agent:b', agent_id='b')
        foreign = self.fact('我喜欢热茶', owner=peer.owner)
        for scope, expected in [(self.scope, own), (peer, foreign), (self.scope, own)]:
            self.assertEqual([r['id'] for r in (await self.retrieve(scope=scope))['results']], [expected])
        self.assertEqual(set(self.manifest()['hashes']), {own})
        self.assertEqual(set(self.manifest(peer)['hashes']), {foreign})
        self.assertNotEqual(self.directory(), self.directory(database=Path(self.temp.name) / 'another.sqlite3'))
        self.assertNotIn('user:', str(self.directory()))

    async def test_dirty_missing_corrupt_manifest_and_missing_index_recover_from_current_data(self):
        from server.companion.memory_search import YoumiBM25Search
        identifier = self.fact('我喜欢热茶')
        for damage in ('dirty', 'missing_manifest', 'bad_json', 'bad_format', 'missing_index', 'bad_index', 'wrong_count'):
            await self.retrieve()
            folder = self.directory()
            if damage == 'dirty': (folder / 'dirty.json').write_text('{}')
            elif damage == 'missing_manifest': (folder / 'manifest.json').unlink()
            elif damage == 'bad_json': (folder / 'manifest.json').write_text('{')
            elif damage == 'bad_format': (folder / 'manifest.json').write_text('{"format":999,"hashes":{}}')
            elif damage == 'missing_index': shutil.rmtree(folder / 'index')
            elif damage == 'bad_index': (folder / 'index' / 'meta.json').write_text('{')
            else:
                engine = YoumiBM25Search(folder / 'index'); engine.append([dict(id='unexpected', judgment='热茶', tags=[])])
                del engine
            with self.subTest(damage=damage):
                self.assertEqual([r['id'] for r in (await self.retrieve())['results']], [identifier])
                self.assertFalse((folder / 'dirty.json').exists())

    async def test_failure_after_tantivy_commit_keeps_dirty_until_recovery(self):
        from server.companion import memory_index_cache as cache
        self.fact('我喜欢热茶')
        await self.retrieve()
        identifier = self.fact('我喜欢咖啡')
        original = cache._replace_json
        def fail_manifest(path, value):
            if path.name == 'manifest.json': raise OSError('synthetic disk failure')
            return original(path, value)
        with patch.object(cache, '_replace_json', fail_manifest):
            with self.assertRaisesRegex(EmbeddingUnavailable, 'memory_shadow_search_unavailable'):
                await self.retrieve('咖啡')
        self.assertTrue((self.directory() / 'dirty.json').exists())
        self.assertEqual([r['id'] for r in (await self.retrieve('咖啡'))['results']], [identifier])
        self.assertFalse((self.directory() / 'dirty.json').exists())

    async def test_actual_process_exit_after_commit_is_recovered_without_duplicate_ids(self):
        self.fact('我喜欢热茶')
        await self.retrieve()
        identifier = self.fact('我喜欢咖啡')
        program = '''
import sys, os
from pathlib import Path
from server.companion import memory_index_cache as cache
from server.companion.memory_search import YoumiBM25Search
original = YoumiBM25Search.append
def crash(self, rows):
    original(self, rows)
    os._exit(37)
YoumiBM25Search.append = crash
cache.search(Path(sys.argv[1]), __import__('json').loads(sys.argv[2]), '咖啡', {})
'''
        rows = [dict(id=r['id'], judgment=r['content'], tags=[r['category']]) for r in self.store.list_memories(self.scope.owner)]
        result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', program,
            str(self.directory()), json.dumps(rows)], env=dict(os.environ), capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertTrue((self.directory() / 'dirty.json').exists())
        recovered = await self.retrieve('咖啡')
        self.assertEqual([r['id'] for r in recovered['results']], [identifier])
        self.assertEqual(len(self.manifest()['hashes']), 2)

    async def test_cross_process_file_lock_fails_bounded_then_releases(self):
        self.fact('我喜欢热茶')
        await self.retrieve()
        program = '''
import fcntl,sys
with open(sys.argv[1], 'a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    print('locked', flush=True)
    sys.stdin.readline()
'''
        process = subprocess.Popen([sys.executable, '-c', program, str(self.directory() / 'writer.lock')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(await asyncio.to_thread(process.stdout.readline), 'locked\n')
            with self.assertRaisesRegex(EmbeddingUnavailable, 'memory_shadow_cache_busy'):
                await self.retrieve()
        finally:
            await asyncio.to_thread(process.communicate, '\n', timeout=5)
        self.assertEqual((await self.retrieve())['memory_status'], 'matched')

    async def test_native_cancellation_holds_slot_and_second_runtime_observes_disk_lock(self):
        from server.companion.memory_search import YoumiBM25Search
        self.fact('我喜欢热茶')
        shadow = YoumiMemoryShadow(self.index, cache_dir=self.root)
        started, release, done = threading.Event(), threading.Event(), threading.Event()
        original = YoumiBM25Search.search
        def blocked(engine, *args):
            started.set()
            try:
                if not release.wait(3): raise RuntimeError('release timeout')
                return original(engine, *args)
            finally: done.set()
        with patch.object(YoumiBM25Search, 'search', blocked):
            task = asyncio.create_task(shadow.retrieve(self.scope, self.auth, '热茶', use_vectors=False))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
                with self.assertRaisesRegex(EmbeddingUnavailable, 'memory_shadow_busy'):
                    await shadow.retrieve(self.scope, self.auth, '热茶', use_vectors=False)
                with self.assertRaisesRegex(EmbeddingUnavailable, 'memory_shadow_cache_busy'):
                    await self.retrieve()
            finally:
                release.set()
                self.assertTrue(await asyncio.to_thread(done.wait, 3))
        await asyncio.sleep(0)
        self.assertEqual((await self.retrieve())['memory_status'], 'matched')

    async def test_query_time_deletion_or_revocation_never_returns_cached_private_data(self):
        from server.companion.memory_search import YoumiBM25Search
        identifier = self.fact('我喜欢热茶')
        await self.retrieve()
        original = YoumiBM25Search.search
        def changed(engine, *args):
            result = original(engine, *args)
            self.store.delete_memory(self.scope.owner, identifier)
            return result
        with patch.object(YoumiBM25Search, 'search', changed):
            with self.assertRaisesRegex(EmbeddingUnavailable, 'memory_changed'): await self.retrieve()
        self.fact('我喜欢热茶')
        def revoked(engine, *args):
            result = original(engine, *args)
            self.auth.active = False
            return result
        with patch.object(YoumiBM25Search, 'search', revoked):
            with self.assertRaises(PrivateAuthorizationRequired): await self.retrieve()

    async def test_disk_fault_does_not_change_default_recall_or_51_emotions(self):
        self.fact('我喜欢热茶')
        await self.index.rebuild_batch(self.scope, self.auth)
        state = emotional_system.status(self.store, self.scope.owner)
        emotional_system.manage(self.store, self.scope.owner, dict(action='configure', version=0,
            enabled=True, dimensions=state['dimensions']))
        runtime = YoumiMemoryRuntime(self.index, shadow_cache_dir=self.root)
        default = await runtime.retrieve(self.scope, self.auth, '热茶')
        before = list(self.store.db.iterdump())
        self.root.write_text('not a directory')
        with self.assertRaisesRegex(EmbeddingUnavailable, 'memory_shadow_search_unavailable'):
            await self.retrieve(runtime=runtime)
        self.assertEqual(list(self.store.db.iterdump()), before)
        self.assertEqual(await runtime.retrieve(self.scope, self.auth, '热茶'), default)
        self.assertEqual(len(emotional_system.status(self.store, self.scope.owner)['dimensions']), 51)

    async def test_default_runtime_and_invalid_authorization_never_create_disk_cache(self):
        self.fact('我喜欢热茶')
        await self.retrieve(runtime=YoumiMemoryRuntime(self.index))
        self.assertFalse(self.root.exists())
        self.auth.active = False
        with self.assertRaises(PrivateAuthorizationRequired): await self.retrieve()
        self.assertFalse(self.root.exists())

    async def test_merge_retires_original_id_and_cache_rebuild_keeps_survivor_sources(self):
        from server.companion.memory_state import _merge
        retired, survivor = self.fact('我喜欢热茶'), self.fact('我喜欢红茶')
        await self.retrieve()
        versions = {row['memory_id']: [row['content_version'], row['evidence_version']]
            for row in self.store.db.execute('SELECT * FROM mia_memory_meta')}
        _merge(self.store, self.scope.owner, dict(operation_id='cache-merge', retired_id=retired, survivor_id=survivor,
            revision=self.store.snapshot(self.scope.owner)['revision'], versions={'retired': versions[retired], 'survivor': versions[survivor]}))
        result = await self.retrieve('茶')
        self.assertEqual([r['id'] for r in result['results']], [survivor])
        self.assertEqual(result['results'][0]['source']['excerpt'], '我喜欢红茶')
        self.assertEqual(set(self.manifest()['hashes']), {survivor})
        self.assertEqual(len(self.store.list_memories(self.scope.owner)), 2)

    async def test_clear_reconciles_empty_and_other_database_uses_separate_partition(self):
        identifier = self.fact('我喜欢热茶')
        await self.retrieve()
        self.store.clear(self.scope.owner)
        self.assertEqual((await self.retrieve())['results'], [])
        self.assertEqual(self.manifest()['hashes'], {})
        database = Path(self.temp.name) / 'other.sqlite3'
        other = MemoryStore(database)
        try:
            other.apply_extraction(self.scope.owner, 'other', 0, ['我喜欢咖啡'],
                {'memories': [dict(category='preference', content='我喜欢咖啡', source_index=0)]}, 'other')
            runtime = YoumiMemoryRuntime(SemanticMemoryIndex(other, self.encoder), shadow_cache_dir=self.root)
            result = await self.retrieve('咖啡', runtime=runtime)
            self.assertNotIn(identifier, [r['id'] for r in result['results']])
            self.assertNotEqual(self.directory(), self.directory(database=database))
        finally:
            other.close()
