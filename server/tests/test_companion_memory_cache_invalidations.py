"""Durable invalidations with real SQLite writers and process interruption."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from server.companion import features
from server.companion.memory import MemoryError, MemoryStore
from server.companion.memory_cache_invalidations import YoumiMemoryCacheInvalidations
from server.companion.schema import migrate
from server.tests.companion_schema_fixtures import remove_v15


class CacheInvalidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'memory.sqlite3'
        self.store = MemoryStore(self.path, clock=lambda: 100)
        self.owner = 'user:1:agent:a'
        self.other = 'user:1:agent:b'
        self.store.snapshot(self.owner)
        self.queue = YoumiMemoryCacheInvalidations(self.store)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def fact(self, content='我喜欢热茶', owner=None, checkpoint='first'):
        owner = owner or self.owner
        return self.store.apply_extraction(owner, checkpoint, self.store.snapshot(owner)['revision'], [content],
            {'memories': [{'category': 'preference', 'content': content, 'source_index': 0, 'sensitive': False}]},
            checkpoint)['memories'][0]

    def generation(self, owner=None):
        return self.store.db.execute('SELECT generation FROM youmi_memory_cache_invalidations WHERE owner=?',
                                     (owner or self.owner,)).fetchone()[0]

    def clean(self, owner=None):
        # Simulate an internal cleaner's successful physical cleanup, not a public operation.
        owner = owner or self.owner
        self.assertTrue(self.queue.acknowledge(owner, self.generation(owner)))

    def test_new_owner_and_repeated_writes_coalesce_without_fact_text(self):
        self.assertEqual(self.queue.pending(), [{'owner': self.owner, 'generation': 1}])
        identifier = self.fact()
        generation = self.generation()
        self.assertGreater(generation, 1)
        for i in range(20):
            self.store.edit_memory(self.owner, identifier, f'我喜欢热茶{i}')
        self.assertGreater(self.generation(), generation)
        self.assertEqual(len(self.queue.pending()), 1)
        self.assertEqual([row[1] for row in self.store.db.execute('PRAGMA table_info(youmi_memory_cache_invalidations)')],
                         ['owner', 'generation', 'cleaned_generation'])
        self.assertNotIn('热茶', json.dumps(self.queue.pending(), ensure_ascii=False))
        self.clean()
        self.assertEqual(self.queue.pending(), [])
        self.assertFalse(self.queue.acknowledge(self.owner, self.generation()))

    def test_canonical_and_invalidation_commit_or_rollback_together(self):
        identifier = self.fact()
        self.clean()
        before = list(self.store.db.iterdump())
        with self.assertRaisesRegex(RuntimeError, 'abort'):
            with self.store.db:
                self.store.db.execute('DELETE FROM memories WHERE id=?', (identifier,))
                self.assertTrue(self.queue.pending())  # Visible to this transaction only.
                raise RuntimeError('abort')
        self.assertEqual(list(self.store.db.iterdump()), before)
        self.store.delete_memory(self.owner, identifier)
        self.assertTrue(self.queue.pending())
        self.assertIsNone(self.store.db.execute('SELECT id FROM memories WHERE id=?', (identifier,)).fetchone())

    def test_sources_metadata_pending_and_retirement_invalidate(self):
        identifier = self.fact()
        updates = [
            ("UPDATE mia_memory_meta SET keywords='茶' WHERE memory_id=?", (identifier,)),
            ("UPDATE mia_memory_meta SET state='superseded' WHERE memory_id=?", (identifier,)),
            ('UPDATE memories SET confirmed=0 WHERE id=?', (identifier,)),
            ('INSERT INTO sources VALUES (?,?,?,?,?)', (identifier, 'second', 'proof', '我喜欢茶', 101)),
            ('DELETE FROM sources WHERE memory_id=?', (identifier,)),
        ]
        for sql, args in updates:
            with self.subTest(sql=sql):
                self.clean()
                previous = self.generation()
                with self.store.db:
                    self.store.db.execute(sql, args)
                self.assertGreater(self.generation(), previous)
                self.assertEqual(self.queue.pending()[0]['owner'], self.owner)

    def test_noop_and_read_only_work_do_not_generate_cleanup(self):
        identifier = self.fact()
        self.clean()
        before = self.generation()
        self.store.snapshot(self.owner)
        self.store.list_memories(self.owner)
        with self.store.db:
            self.store.db.execute('UPDATE mia_scope_versions SET content_epoch=content_epoch WHERE owner=?', (self.owner,))
            self.store.db.execute('UPDATE memories SET content=content WHERE id=?', (identifier,))
            self.store.db.execute('UPDATE owners SET revision=revision,paused=paused WHERE owner=?', (self.owner,))
        self.assertEqual(self.generation(), before)
        self.assertEqual(self.queue.pending(), [])

    def test_clear_empty_owner_pause_and_recall_switch_are_tracked(self):
        self.clean()
        self.store.clear(self.owner)
        self.assertTrue(self.queue.pending())
        self.clean()
        self.store.set_paused(self.owner, True)
        self.assertTrue(self.queue.pending())
        self.clean()
        features.update(self.store, self.owner, {'memory_recall': False})
        self.assertTrue(self.queue.pending())
        self.clean()
        features.update(self.store, self.owner, {'memory_recall': True})
        self.assertTrue(self.queue.pending())

    def test_agent_isolation_and_cas_cannot_clear_newer_generation(self):
        identifier = self.fact()
        self.store.snapshot(self.other)
        self.clean(self.other)
        old = self.generation()
        second = MemoryStore(self.path)
        try:
            second.edit_memory(self.owner, identifier, '我喜欢咖啡')
            self.assertFalse(self.queue.acknowledge(self.owner, old))
            self.assertEqual(self.queue.pending(), [{'owner': self.owner, 'generation': self.generation()}])
            self.assertFalse(self.queue.acknowledge('user:unknown:agent:a', self.generation()))
            self.assertEqual(self.generation(self.other), 1)
        finally:
            second.close()
        self.clean()
        self.assertEqual(self.queue.pending(), [])

    def test_acknowledgement_refuses_outer_transaction_and_invalid_arguments(self):
        before = self.generation()
        with self.store.db:
            self.store.db.execute('UPDATE owners SET revision=revision+1 WHERE owner=?', (self.owner,))
            with self.assertRaises(MemoryError):
                self.queue.acknowledge(self.owner, self.generation())
        self.assertGreater(self.generation(), before)
        for invalid in [True, 0, -1, 2**63, '1']:
            with self.assertRaises(MemoryError):
                self.queue.acknowledge(self.owner, invalid)
        for invalid in [0, 101, True, '1']:
            with self.assertRaises(MemoryError):
                self.queue.pending(limit=invalid)
        self.assertTrue(self.queue.pending())

    def test_bounded_pending_pages_and_row_constraints(self):
        for i in range(102):
            self.store.snapshot(f'user:2:agent:{i:03}')
        self.assertEqual(len(self.queue.pending()), 100)
        self.assertEqual(len(self.queue.pending(limit=2)), 2)
        for sql in ["UPDATE youmi_memory_cache_invalidations SET generation=0",
                    "UPDATE youmi_memory_cache_invalidations SET generation=1.5",
                    "UPDATE youmi_memory_cache_invalidations SET cleaned_generation=generation+1"]:
            with self.assertRaises(sqlite3.IntegrityError), self.store.db:
                self.store.db.execute(sql)
        self.assertEqual(len(self.queue.pending()), 100)

    def test_other_process_commit_survives_exit_and_uncommitted_delete_rolls_back(self):
        identifier = self.fact()
        self.clean()
        code = '''import os,sqlite3,sys
from server.companion.memory import MemoryStore
store=MemoryStore(sys.argv[1])
store.db.execute('BEGIN IMMEDIATE')
store.db.execute('DELETE FROM memories WHERE id=?',(sys.argv[2],))
if sys.argv[3]=='commit': store.db.commit()
os._exit(37)
'''
        for mode in ['rollback', 'commit']:
            result = subprocess.run([sys.executable, '-c', code, str(self.path), identifier, mode], capture_output=True)
            self.assertEqual(result.returncode, 37, result.stderr)
            reopened = MemoryStore(self.path)
            try:
                pending = YoumiMemoryCacheInvalidations(reopened).pending()
                present = reopened.db.execute('SELECT id FROM memories WHERE id=?', (identifier,)).fetchone()
                self.assertEqual(bool(pending), mode == 'commit')
                self.assertEqual(present is None, mode == 'commit')
            finally:
                reopened.close()

    def v14(self):
        with self.store.db:
            remove_v15(self.store.db)
            self.store.db.execute('DELETE FROM mia_schema_migrations WHERE version>=15')
        namespace = {'__file__': str(Path('server/companion/schema.py').resolve())}
        source = subprocess.check_output(['git', 'show', '1a4e7277:server/companion/schema.py'], text=True)
        exec(compile(source, 'frozen-v14.py', 'exec'), namespace)
        namespace['migrate'](self.store.db)
        return namespace['migrate']

    def test_v14_upgrade_preserves_existing_values_seeds_pending_and_is_idempotent(self):
        self.fact()
        old = self.v14()
        before = {table: [tuple(row) for row in self.store.db.execute('SELECT * FROM ' + table)]
                  for table in ['owners', 'memories', 'sources', 'mia_memory_meta', 'mia_scope_versions']}
        migrate(self.store.db)
        self.assertEqual(self.queue.pending(), [{'owner': self.owner, 'generation': 1}])
        after = {table: [tuple(row) for row in self.store.db.execute('SELECT * FROM ' + table)] for table in before}
        self.assertEqual(before, after)
        dump = list(self.store.db.iterdump())
        migrate(self.store.db)
        self.assertEqual(list(self.store.db.iterdump()), dump)
        with self.assertRaisesRegex(ValueError, 'schema version or checksum'):
            old(self.store.db)
        self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_failed_v15_migration_rolls_back_before_retry(self):
        old = self.v14()
        before = list(self.store.db.iterdump())
        self.store.db.set_authorizer(lambda action, name, *unused: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TRIGGER and name == 'youmi_memory_cache_owner_update' else sqlite3.SQLITE_OK)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                migrate(self.store.db)
        finally:
            self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(list(self.store.db.iterdump()), before)
        old(self.store.db)
        migrate(self.store.db)
        self.assertEqual(self.queue.pending(), [{'owner': self.owner, 'generation': 1}])

    def test_process_exit_during_v15_leaves_frozen_v14_reopenable(self):
        old = self.v14()
        before = list(self.store.db.iterdump())
        code = '''import os,sqlite3,sys
from server.companion.schema import migrate
db=sqlite3.connect(sys.argv[1])
db.execute('PRAGMA foreign_keys=ON')
def interrupt(action,name,*args):
    if action==sqlite3.SQLITE_CREATE_TRIGGER and name=='youmi_memory_cache_owner_update': os._exit(38)
    return sqlite3.SQLITE_OK
db.set_authorizer(interrupt)
migrate(db)
'''
        result = subprocess.run([sys.executable, '-c', code, str(self.path)], capture_output=True)
        self.assertEqual(result.returncode, 38, result.stderr)
        self.assertEqual(list(self.store.db.iterdump()), before)
        old(self.store.db)
        migrate(self.store.db)
        self.assertEqual(self.queue.pending(), [{'owner': self.owner, 'generation': 1}])
