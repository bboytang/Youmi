"""Managed private index cleanup with actual SQLite/Tantivy and process faults."""

import asyncio
from dataclasses import replace
import importlib.util
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from server.companion import memory_management
from server.companion.authorization import PrivateAuthorizationRequired
from server.companion.embedding import EmbeddingUnavailable
from server.companion.memory import MemoryStore
from server.companion.memory_cache_lifecycle import YoumiManagedMemoryCache
from server.companion.memory_index_cache import _locked
from server.companion.memory_runtime import YoumiMemoryRuntime
from server.companion.schema import migrate
from server.tests import test_companion_context as fixture
from server.tests.companion_schema_fixtures import remove_v16


@unittest.skipUnless(importlib.util.find_spec('tantivy'), 'Optional native shadow dependency is not installed')
class ManagedCacheTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.ContextBuilderTests.setUp
    tearDown = fixture.ContextBuilderTests.tearDown
    fact = fixture.ContextBuilderTests.fact

    @property
    def database(self):
        return Path(self.temp.name) / 'memory.sqlite3'

    @property
    def cache(self):
        return YoumiManagedMemoryCache(self.database)

    @property
    def path(self):
        return self.cache.directory(self.scope.owner)

    async def retrieve(self, scope=None, query='热茶'):
        return await YoumiMemoryRuntime(self.index, managed_shadow_cache=True).shadow_retrieve(
            scope or self.scope, self.auth, query, use_vectors=False)

    def status(self, owner=None):
        return self.store.cache_cleanup_status(owner or self.scope.owner)

    def assert_purged(self):
        self.assertEqual([child.name for child in self.path.iterdir()], ['writer.lock'])
        self.assertEqual(self.status(), dict(state='complete', error=None))

    async def test_registration_is_authorized_internal_and_default_mode_has_no_directory(self):
        self.fact('我喜欢热茶')
        await self.index.rebuild_batch(self.scope, self.auth)
        normal = await YoumiMemoryRuntime(self.index).retrieve(self.scope, self.auth, '热茶')
        self.assertFalse(self.cache.root.exists())
        self.auth.active = False
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.retrieve()
        self.assertFalse(self.cache.root.exists())
        self.assertEqual(self.status()['state'], 'not_registered')
        self.auth.active = True
        result = await self.retrieve()
        self.assertEqual(result['results'][0]['content'], '我喜欢热茶')
        self.assertEqual(self.cache.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.status(), dict(state='complete', error=None))
        self.assertEqual(await YoumiMemoryRuntime(self.index).retrieve(self.scope, self.auth, '热茶'), normal)
        with self.assertRaises(ValueError):
            YoumiMemoryRuntime(self.index, shadow_cache_dir='arbitrary', managed_shadow_cache=True)

    async def test_delete_purges_idle_private_files_and_keeps_lock_inode(self):
        identifier = self.fact('我喜欢热茶')
        await self.retrieve()
        inode = (self.path / 'writer.lock').stat().st_ino
        (self.path / 'old-untracked-private-data').write_text('旧的私人片段')
        self.store.delete_memory(self.scope.owner, identifier)
        self.assert_purged()
        self.assertEqual((self.path / 'writer.lock').stat().st_ino, inode)
        self.assertEqual((await self.retrieve())['results'], [])

    async def test_edit_pending_metadata_source_retraction_and_empty_clear_purge_without_query(self):
        identifier = self.fact('我喜欢热茶')
        for action in ['edit', 'pending', 'metadata', 'source']:
            with self.subTest(action=action):
                if action == 'source':
                    with self.store.db:
                        self.store.db.execute('INSERT INTO sources VALUES (?,?,?,?,?)',
                            (identifier, 'retraction-proof', 'proof', '我喜欢暖茶', 100))
                await self.retrieve()
                self.assertTrue((self.path / 'index').is_dir())
                if action == 'edit':
                    self.store.edit_memory(self.scope.owner, identifier, '我喜欢暖茶')
                elif action == 'pending':
                    self.store.edit_memory(self.scope.owner, identifier, '我喜欢暖茶', confirmed=False)
                elif action == 'metadata':
                    with self.store.db:
                        self.store.db.execute("UPDATE mia_memory_meta SET keywords='茶' WHERE memory_id=?", (identifier,))
                else:
                    with self.store.db:
                        self.store.db.execute('DELETE FROM sources WHERE memory_id=?', (identifier,))
                self.assert_purged()
        self.store.clear(self.scope.owner)
        await self.retrieve()
        self.store.clear(self.scope.owner)  # Even an empty clear is observable.
        self.assert_purged()

    async def test_rollback_never_cleans_a_valid_partition(self):
        identifier = self.fact('我喜欢热茶')
        await self.retrieve()
        with patch.object(YoumiManagedMemoryCache, '_purge', side_effect=AssertionError('rollback must not purge')):
            with self.assertRaisesRegex(RuntimeError, 'rollback'):
                with self.store.db:
                    self.store.db.execute('DELETE FROM memories WHERE id=?', (identifier,))
                    raise RuntimeError('rollback')
        self.assertTrue((self.path / 'index').exists())
        self.assertEqual(self.status()['state'], 'complete')
        self.assertEqual((await self.retrieve())['results'][0]['id'], identifier)

    async def test_busy_receipt_distinguishes_committed_fact_from_pending_cleanup(self):
        identifier = self.fact('我喜欢热茶')
        await self.retrieve()
        expected = self.store.db.execute('SELECT o.revision,x.content_version,x.evidence_version FROM owners o '
            'JOIN memories m ON m.owner=o.owner JOIN mia_memory_meta x ON x.memory_id=m.id WHERE m.id=?', (identifier,)).fetchone()
        with _locked(self.path):
            result = memory_management.manage(self.store, self.scope.owner,
                dict(action='delete', id=identifier, expected=dict(expected)))
            self.assertEqual(result, dict(saved=True, cache_cleanup=dict(state='pending', error='busy')))
            self.assertIsNone(self.store.db.execute('SELECT id FROM memories WHERE id=?', (identifier,)).fetchone())
            self.assertTrue((self.path / 'index').exists())
        self.assertEqual(self.cache.recover()['complete'], 1)
        self.assert_purged()

    async def test_another_memory_store_process_cleans_on_successful_commit(self):
        identifier = self.fact('我喜欢热茶')
        await self.retrieve()
        code = '''import sys
from server.companion.memory import MemoryStore
store=MemoryStore(sys.argv[1])
store.delete_memory(sys.argv[2],sys.argv[3])
store.close()
'''
        result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', code,
            str(self.database), self.scope.owner, identifier], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_purged()

    async def test_raw_commit_exit_leaves_work_for_memory_store_startup(self):
        identifier = self.fact('我喜欢热茶')
        await self.retrieve()
        code = '''import os,sqlite3,sys
db=sqlite3.connect(sys.argv[1]); db.execute('PRAGMA foreign_keys=ON')
db.execute('DELETE FROM memories WHERE id=?',(sys.argv[2],)); db.commit(); os._exit(37)
'''
        result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', code,
            str(self.database), identifier], capture_output=True)
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertTrue((self.path / 'index').exists())
        self.assertEqual(self.status()['state'], 'pending')
        reopened = MemoryStore(self.database)
        reopened.close()
        self.assert_purged()

    async def test_native_process_exit_after_commit_recovers_writing_flag_without_fact_change(self):
        self.fact('我喜欢热茶')
        await self.retrieve()
        code = '''import os,sys
from types import SimpleNamespace
from server.companion.memory import MemoryStore
from server.companion.memory_repository import YoumiMemoryRepository
from server.companion.memory_cache_lifecycle import YoumiManagedMemoryCache
from server.companion import memory_cache_lifecycle as lifecycle
store=MemoryStore(sys.argv[1]); scope=SimpleNamespace(owner=sys.argv[2],persona_id='mia')
corpus=YoumiMemoryRepository(store)._capture(scope)
original=lifecycle._search_locked
def die(*args):
    original(*args)
    os._exit(38)
lifecycle._search_locked=die
YoumiManagedMemoryCache(sys.argv[1]).search(corpus,
    [dict(id=row['id'],judgment=row['content'],tags=[]) for row in corpus.records], '热茶', {})
'''
        result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', code,
            str(self.database), self.scope.owner], capture_output=True)
        self.assertEqual(result.returncode, 38, result.stderr)
        self.assertEqual(self.status()['state'], 'pending')
        self.assertTrue((self.path / 'index').exists())
        reopened = MemoryStore(self.database)
        reopened.close()
        self.assert_purged()

    async def test_stale_authorized_snapshot_cannot_recreate_after_completed_delete(self):
        from server.companion.memory_repository import YoumiMemoryRepository
        identifier = self.fact('我喜欢热茶')
        await self.retrieve()
        corpus = await YoumiMemoryRepository(self.store).capture(self.scope, self.auth)
        self.store.delete_memory(self.scope.owner, identifier)
        self.assert_purged()
        with self.assertRaisesRegex(EmbeddingUnavailable, 'memory_changed'):
            await asyncio.to_thread(self.cache.search, corpus,
                [dict(id=identifier, judgment='我喜欢热茶', tags=[])], '热茶', {})
        self.assert_purged()

    async def test_delete_during_native_write_purges_old_snapshot_before_releasing_lock(self):
        from server.companion import memory_cache_lifecycle as lifecycle
        identifier = self.fact('我喜欢热茶')
        await self.retrieve()
        original = lifecycle._search_locked
        def during(*args):
            second = MemoryStore(self.database)
            try:
                second.delete_memory(self.scope.owner, identifier)
                self.assertEqual(second.cache_cleanup_status(self.scope.owner)['state'], 'pending')
            finally:
                second.close()
            return original(*args)
        with patch.object(lifecycle, '_search_locked', during):
            with self.assertRaisesRegex(EmbeddingUnavailable, 'memory_changed'):
                await self.retrieve()
        self.assert_purged()

    async def test_native_failure_stays_pending_and_startup_purges_partial_segments(self):
        from server.companion import memory_cache_lifecycle as lifecycle
        self.fact('我喜欢热茶')
        await self.retrieve()
        with patch.object(lifecycle, '_search_locked', side_effect=RuntimeError('synthetic native failure')):
            with self.assertRaises(EmbeddingUnavailable):
                await self.retrieve()
        self.assertEqual(self.status(), dict(state='pending', error='io'))
        reopened = MemoryStore(self.database)
        reopened.close()
        self.assert_purged()

    async def test_disk_failure_keeps_deletion_and_existing_emotion_then_retries(self):
        from server.companion import emotional_system
        identifier = self.fact('我喜欢热茶')
        await self.retrieve()
        self.store.clock = lambda: 1800000000
        configured = emotional_system.status(self.store, self.scope.owner)
        configured['dimensions']['joy'].update(mode='fixed', baseline=73)
        emotional_system.manage(self.store, self.scope.owner, dict(action='configure', version=configured['version'],
            enabled=True, dimensions=configured['dimensions']))
        emotion = emotional_system.status(self.store, self.scope.owner)
        self.assertEqual(len(emotion['dimensions']), 51)
        with patch.object(YoumiManagedMemoryCache, '_purge', side_effect=OSError('synthetic disk failure')):
            self.store.delete_memory(self.scope.owner, identifier)
            self.assertEqual(self.status(), dict(state='pending', error='io'))
            self.assertEqual(emotional_system.status(self.store, self.scope.owner), emotion)
        self.assertIsNone(self.store.db.execute('SELECT id FROM memories WHERE id=?', (identifier,)).fetchone())
        reopened = MemoryStore(self.database)
        reopened.close()
        self.assert_purged()

    async def test_fsync_failure_never_acknowledges_cleanup(self):
        from server.companion import memory_cache_lifecycle as lifecycle
        identifier = self.fact('我喜欢热茶')
        await self.retrieve()
        with patch.object(lifecycle, '_sync_directory', side_effect=OSError('synthetic sync failure')):
            self.store.delete_memory(self.scope.owner, identifier)
            self.assertEqual(self.status()['state'], 'pending')
            row = self.store.db.execute('SELECT generation,cleaned_generation FROM youmi_memory_cache_invalidations '
                'WHERE owner=?', (self.scope.owner,)).fetchone()
            self.assertGreater(row[0], row[1])
        self.cache.recover()
        self.assert_purged()

    async def test_concurrent_write_during_purge_cannot_be_acknowledged_as_old_generation(self):
        self.fact('我喜欢热茶')
        await self.retrieve()
        original = YoumiManagedMemoryCache._purge
        def during(path):
            original(path)
            with sqlite3.connect(self.database) as db:
                db.execute('UPDATE owners SET revision=revision+1 WHERE owner=?', (self.scope.owner,))
        with patch.object(YoumiManagedMemoryCache, '_purge', staticmethod(during)):
            self.assertFalse(self.cache.cleanup(self.scope.owner))
        self.assertEqual(self.status()['state'], 'pending')
        self.cache.recover()
        self.assert_purged()

    async def test_orphan_registration_outlives_owner_and_startup_removes_files(self):
        identifier = self.fact('我喜欢热茶')
        await self.retrieve()
        with sqlite3.connect(self.database) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('DELETE FROM memories WHERE id=?', (identifier,))
            db.execute('DELETE FROM checkpoints WHERE owner=?', (self.scope.owner,))
            db.execute('DELETE FROM owners WHERE owner=?', (self.scope.owner,))
        self.assertEqual(self.status()['state'], 'pending')
        reopened = MemoryStore(self.database)
        reopened.close()
        self.assertEqual([child.name for child in self.path.iterdir()], ['writer.lock'])
        self.assertEqual(self.status()['state'], 'not_registered')

    async def test_symlink_root_and_lock_are_rejected_without_touching_target(self):
        identifier = self.fact('我喜欢热茶')
        await self.retrieve()
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        marker = outside / 'keep'
        marker.write_text('foreign data')
        self.cache.root.rename(Path(self.temp.name) / 'saved-cache')
        self.cache.root.symlink_to(outside, target_is_directory=True)
        self.store.delete_memory(self.scope.owner, identifier)
        self.assertEqual(self.status()['state'], 'pending')
        self.assertEqual(marker.read_text(), 'foreign data')
        self.assertEqual(list(outside.iterdir()), [marker])
        self.cache.root.unlink()
        (Path(self.temp.name) / 'saved-cache').rename(self.cache.root)
        lock = self.path / 'writer.lock'
        lock.unlink()
        lock.symlink_to(marker)
        self.assertFalse(self.cache.cleanup(self.scope.owner))
        self.assertEqual(marker.read_text(), 'foreign data')
        lock.unlink()
        self.cache.recover()
        self.assert_purged()

    async def test_index_symlink_is_unlinked_without_deleting_external_data(self):
        self.fact('我喜欢热茶')
        await self.retrieve()
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        marker = outside / 'keep'
        marker.write_text('external')
        (self.path / 'untrusted-link').symlink_to(outside, target_is_directory=True)
        self.assertTrue(self.cache.cleanup(self.scope.owner))
        self.assert_purged()
        self.assertEqual(marker.read_text(), 'external')

    async def test_agents_databases_and_failed_batch_cursor_are_isolated(self):
        self.fact('我喜欢热茶')
        other = replace(self.scope, owner='user:1:agent:b', agent_id='b')
        self.fact('我喜欢咖啡', owner=other.owner)
        await self.retrieve()
        await self.retrieve(scope=other, query='咖啡')
        different = YoumiManagedMemoryCache(Path(self.temp.name) / 'other.sqlite3')
        self.assertNotEqual(different.root, self.cache.root)
        self.assertNotEqual(self.cache.directory(other.owner), self.path)
        with sqlite3.connect(self.database) as db:
            db.execute('UPDATE owners SET revision=revision+1')
        original = YoumiManagedMemoryCache._purge
        def fail_first(path):
            if path == self.path:
                raise OSError('first partition unavailable')
            original(path)
        with patch.object(YoumiManagedMemoryCache, '_purge', staticmethod(fail_first)):
            first = self.cache.recover(limit=1)
            second = self.cache.recover(cursor=first['cursor'], limit=1)
        self.assertEqual(first['complete'], 0)
        self.assertEqual(second['complete'], 1)
        self.assertEqual(self.status()['state'], 'pending')
        self.assertEqual(self.status(other.owner)['state'], 'complete')
        self.cache.recover()
        self.assert_purged()


    async def test_real_other_process_lock_busy_leaves_work_until_release(self):
        identifier = self.fact('我喜欢热茶')
        await self.retrieve()
        code = """import sys
from pathlib import Path
from server.companion.memory_index_cache import _locked
with _locked(Path(sys.argv[1])):
    print('locked', flush=True)
    sys.stdin.readline()
"""
        process = subprocess.Popen([sys.executable, '-c', code, str(self.path)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(await asyncio.to_thread(process.stdout.readline), 'locked\n')
            self.store.delete_memory(self.scope.owner, identifier)
            self.assertEqual(self.status(), dict(state='pending', error='busy'))
            self.assertTrue((self.path / 'index').exists())
        finally:
            output, error = await asyncio.to_thread(process.communicate, '\n', timeout=10)
        self.assertEqual(process.returncode, 0, error)
        self.cache.recover()
        self.assert_purged()

    async def test_index_symlink_cannot_receive_native_reads_or_writes(self):
        self.fact('我喜欢热茶')
        await self.retrieve()
        (self.path / 'index').rename(Path(self.temp.name) / 'saved-index')
        outside = Path(self.temp.name) / 'outside-index'
        outside.mkdir()
        marker = outside / 'keep'
        marker.write_text('foreign')
        (self.path / 'index').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(EmbeddingUnavailable):
            await self.retrieve()
        self.assertEqual(marker.read_text(), 'foreign')
        self.assertEqual(list(outside.iterdir()), [marker])
        self.cache.recover()
        self.assert_purged()

    async def test_database_ack_failure_keeps_durable_pending_state(self):
        from server.companion import memory_cache_lifecycle as lifecycle
        identifier = self.fact('我喜欢热茶')
        await self.retrieve()
        original = sqlite3.connect
        def deny_ack(*args, **kwargs):
            db = original(*args, **kwargs)
            db.set_authorizer(lambda action, name, *rest: sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_UPDATE and name == 'youmi_memory_cache_invalidations' else sqlite3.SQLITE_OK)
            return db
        with patch.object(lifecycle.sqlite3, 'connect', deny_ack):
            self.store.delete_memory(self.scope.owner, identifier)
            self.assertEqual(self.status(), dict(state='pending', error='database'))
        self.cache.recover()
        self.assert_purged()


    async def test_foreground_delete_cleans_target_beyond_background_batch_limit(self):
        target = replace(self.scope, owner='user:9:agent:z', user_id=9, agent_id='z')
        identifier = self.fact('我喜欢热茶', owner=target.owner)
        await self.retrieve(scope=target)
        target_path = self.cache.directory(target.owner)
        self.store.db.on_commit = None
        try:
            with self.store.db:
                for i in range(101):
                    owner = f'user:1:agent:queued-{i:03}'
                    self.store._owner(owner)
                    self.store.db.execute('INSERT INTO youmi_memory_cache_partitions(owner) VALUES (?)', (owner,))
        finally:
            self.store.db.on_commit = self.store._cleanup_cache
        self.store.delete_memory(target.owner, identifier)
        self.assertEqual(self.status(target.owner)['state'], 'complete')
        self.assertEqual([p.name for p in target_path.iterdir()], ['writer.lock'])


class ManagedCacheMigrationTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'memory.sqlite3'
        self.store = MemoryStore(self.path)
        self.store.snapshot('user:1:agent:a')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def v15(self):
        with self.store.db:
            remove_v16(self.store.db)
            self.store.db.execute('DELETE FROM mia_schema_migrations WHERE version>=16')
        namespace = {'__file__': str(Path('server/companion/schema.py').resolve())}
        source = subprocess.check_output(['git', 'show', '879e62e2:server/companion/schema.py'], text=True)
        exec(compile(source, 'frozen-v15.py', 'exec'), namespace)
        namespace['migrate'](self.store.db)
        return namespace['migrate']

    def test_v15_upgrade_preserves_all_rows_and_rejects_frozen_loader(self):
        old = self.v15()
        before = [tuple(row) for row in self.store.db.execute('SELECT * FROM youmi_memory_cache_invalidations')]
        migrate(self.store.db)
        self.assertEqual([tuple(row) for row in self.store.db.execute('SELECT * FROM youmi_memory_cache_invalidations')], before)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_cache_partitions').fetchone()[0], 0)
        dump = list(self.store.db.iterdump())
        migrate(self.store.db)
        self.assertEqual(list(self.store.db.iterdump()), dump)
        with self.assertRaises(ValueError):
            old(self.store.db)

    def test_v16_failure_is_atomic_and_retryable(self):
        old = self.v15()
        before = list(self.store.db.iterdump())
        self.store.db.set_authorizer(lambda action, name, *args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TABLE and name == 'youmi_memory_cache_partitions' else sqlite3.SQLITE_OK)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                migrate(self.store.db)
        finally:
            self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(list(self.store.db.iterdump()), before)
        old(self.store.db)
        migrate(self.store.db)

    def test_process_exit_in_v16_leaves_frozen_v15_reopenable(self):
        old = self.v15()
        code = '''import os,sqlite3,sys
from server.companion.schema import migrate
db=sqlite3.connect(sys.argv[1])
def interrupt(action,name,*args):
    if action==sqlite3.SQLITE_CREATE_TABLE and name=='youmi_memory_cache_partitions': os._exit(39)
    return sqlite3.SQLITE_OK
db.set_authorizer(interrupt); migrate(db)
'''
        result = subprocess.run([sys.executable, '-c', code, str(self.path)], capture_output=True)
        self.assertEqual(result.returncode, 39, result.stderr)
        old(self.store.db)
        migrate(self.store.db)
