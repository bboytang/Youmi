"""Real SQLite upgrade/rollback tests, including execution of the frozen old source."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from server.companion.memory import MemoryStore
from server.companion.schema import LEGACY_COLUMNS, _schema_checksum, _statements, migrate


class SchemaCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(os.environ['MIA_LEGACY_MEMORY_SOURCE'])
        if hashlib.sha256(source.read_bytes()).hexdigest() != 'e7b24984ad682f767d76498b9b249e6dac5362ffad8ac453c793d2c6fde47ef5':
            raise AssertionError('The real frozen legacy source is required')
        spec = importlib.util.spec_from_file_location('mia_frozen_legacy_memory', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.legacy = module.MemoryStore

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'memory.sqlite3'

    def tearDown(self):
        self.temp.cleanup()

    def remember(self, store, content='喜欢中文小说', owner='user:1:agent:a', session='legacy-session', sensitive=False):
        result = store.apply_extraction(owner, session, store.snapshot(owner)['revision'], [content],
            {'memories': [{'category': 'preference', 'content': content, 'source_index': 0, 'sensitive': sensitive}]},
            content + session)
        return result['memories'][0]

    def original_rows(self, store):
        return {table: [tuple(row) for row in store.db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                for table in LEGACY_COLUMNS}

    def test_upgrade_preserves_real_legacy_values_and_unknown_provenance(self):
        old = self.legacy(self.path, clock=lambda: 1800000000)
        active = self.remember(old)
        pending = self.remember(old, '这是一段私密经历', sensitive=True)
        before = self.original_rows(old)
        old.close()
        new = MemoryStore(self.path)
        try:
            self.assertEqual(before, self.original_rows(new))
            for table, columns in LEGACY_COLUMNS.items():
                self.assertEqual([row[1] for row in new.db.execute(f'PRAGMA table_info({table})')], columns)
            rows = {r['memory_id']: dict(r) for r in new.db.execute('SELECT * FROM mia_memory_meta')}
            self.assertEqual(rows[active]['state'], 'active')
            self.assertEqual(rows[pending]['state'], 'pending')
            self.assertEqual({r['confirmation_origin'] for r in rows.values()}, {'legacy_unknown'})
            self.assertTrue(all(rows[active][field] is None for field in ('entities','keywords','trigger_terms','emotional_weight')))
            source = new.db.execute('SELECT * FROM mia_source_meta LIMIT 1').fetchone()
            self.assertTrue(all(source[field] is None for field in ('turn_id','message_id','observed_at','source_type')))
            self.assertEqual(new.db.execute('SELECT COUNT(*) FROM mia_vectors').fetchone()[0], 0)
            self.assertEqual(new.db.execute('SELECT COUNT(*) FROM mia_emotion_consent').fetchone()[0], 0)
        finally:
            new.close()

    def test_actual_old_code_can_write_edit_retrieve_and_delete_after_upgrade(self):
        old = self.legacy(self.path)
        memory = self.remember(old)
        old.close()
        new = MemoryStore(self.path)
        with new.db:
            new.db.execute('INSERT INTO mia_vectors(memory_id,model_id,model_revision,dimension,content_version,normalized_vector,indexed_at) '
                           'VALUES (?,?,?,768,1,?,1)', (memory, 'synthetic-not-an-embedding', 'fixture', bytes(768 * 4)))
        new.close()
        old = self.legacy(self.path)
        try:
            old.edit_memory('user:1:agent:a', memory, '现在偏爱历史小说')
            self.assertEqual(old.retrieve('user:1:agent:a', '历史小说')[0]['id'], memory)
            self.assertEqual(old.db.execute('SELECT COUNT(*) FROM mia_vectors').fetchone()[0], 0)
            self.assertEqual(old.db.execute('SELECT content_version FROM mia_memory_meta WHERE memory_id=?', (memory,)).fetchone()[0], 2)
            self.remember(old, '喜欢天文科普', session='rollback-new-session')
            peer = self.remember(old, '喜欢音乐', owner='user:2:agent:b')
            old.delete_memory('user:1:agent:a', memory)
            self.assertIsNone(old.db.execute('SELECT 1 FROM mia_memory_meta WHERE memory_id=?', (memory,)).fetchone())
            self.assertIsNone(old.db.execute('SELECT 1 FROM mia_source_meta WHERE memory_id=?', (memory,)).fetchone())
            old.clear('user:1:agent:a')
            self.assertEqual(old.list_memories('user:1:agent:a'), [])
            self.assertEqual(old.list_memories('user:2:agent:b')[0]['id'], peer)
        finally:
            old.close()
        new = MemoryStore(self.path)
        try:
            self.assertEqual(new.list_memories('user:2:agent:b')[0]['id'], peer)
            self.assertEqual(new.db.execute('PRAGMA foreign_key_check').fetchall(), [])
        finally:
            new.close()

    def test_repeated_upgrade_changes_neither_legacy_data_nor_metadata(self):
        new = MemoryStore(self.path)
        self.remember(new)
        before = self.original_rows(new)
        ledger = tuple(new.db.execute('SELECT * FROM mia_schema_migrations').fetchone())
        meta = [tuple(r) for r in new.db.execute('SELECT * FROM mia_memory_meta')]
        migrate(new.db)
        self.assertEqual(self.original_rows(new), before)
        self.assertEqual(tuple(new.db.execute('SELECT * FROM mia_schema_migrations').fetchone()), ledger)
        self.assertEqual([tuple(r) for r in new.db.execute('SELECT * FROM mia_memory_meta')], meta)
        new.close()

    def test_p10_v1_upgrade_is_atomic_preserves_ledger_and_invalidates_scoring_snapshots(self):
        # Build the exact immutable P10 SQL on the real old fact tables.
        old = self.legacy(self.path)
        memory_id = self.remember(old)
        before = self.original_rows(old)
        sql = Path('server/companion/schema.sql').read_text(encoding='utf-8')
        with old.db:
            old.db.execute('CREATE TABLE mia_schema_migrations(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,schema_checksum TEXT NOT NULL,applied_at INTEGER NOT NULL)')
            for statement in _statements(sql):
                old.db.execute(statement)
            old.db.execute('INSERT INTO mia_schema_migrations VALUES (1,?,?,1)',
                           (hashlib.sha256(sql.encode()).hexdigest(), _schema_checksum(old.db)))
        ledger = tuple(old.db.execute('SELECT * FROM mia_schema_migrations').fetchone())
        def reject_v2(action, name, *unused):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_TRIGGER and name == 'mia_retrieval_source_delete' else sqlite3.SQLITE_OK
        old.db.set_authorizer(reject_v2)
        with self.assertRaises(sqlite3.DatabaseError):
            migrate(old.db)
        old.db.set_authorizer(None)
        self.assertIsNone(old.db.execute("SELECT 1 FROM sqlite_master WHERE name='mia_retrieval_meta_update'").fetchone())
        self.assertEqual([tuple(row) for row in old.db.execute('SELECT * FROM mia_schema_migrations')], [ledger])
        migrate(old.db)
        self.assertEqual(self.original_rows(old), before)
        self.assertEqual(tuple(old.db.execute('SELECT * FROM mia_schema_migrations WHERE version=1').fetchone()), ledger)
        epoch = old.db.execute('SELECT content_epoch FROM mia_scope_versions').fetchone()[0]
        with old.db:
            old.db.execute('UPDATE mia_memory_meta SET keywords=? WHERE memory_id=?', (json.dumps(['小说']), memory_id))
        self.assertGreater(old.db.execute('SELECT content_epoch FROM mia_scope_versions').fetchone()[0], epoch)
        # The actual pre-migration writer still edits and invalidates metadata.
        old.edit_memory('user:1:agent:a', memory_id, '现在偏爱历史小说')
        self.assertIsNone(old.db.execute('SELECT keywords FROM mia_memory_meta WHERE memory_id=?', (memory_id,)).fetchone()[0])
        migrate(old.db)
        self.assertEqual(old.db.execute('SELECT COUNT(*) FROM mia_schema_migrations').fetchone()[0], 12)
        old.close()

    def test_p18_v2_to_v3_failure_rolls_back_then_reopens_without_fabricated_provenance(self):
        old = self.legacy(self.path)
        self.remember(old)
        before = self.original_rows(old)
        with old.db:
            old.db.execute('CREATE TABLE mia_schema_migrations(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,schema_checksum TEXT NOT NULL,applied_at INTEGER NOT NULL)')
            for version, name in enumerate(('schema.sql', 'schema-v2.sql'), 1):
                sql = Path('server/companion', name).read_text()
                for statement in _statements(sql):
                    old.db.execute(statement)
                old.db.execute('INSERT INTO mia_schema_migrations VALUES (?,?,?,1)',
                    (version, hashlib.sha256(sql.encode()).hexdigest(), _schema_checksum(old.db)))
        ledger = [tuple(row) for row in old.db.execute('SELECT * FROM mia_schema_migrations')]
        old.db.set_authorizer(lambda action, name, *args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TABLE and name == 'mia_observation_meta' else sqlite3.SQLITE_OK)
        with self.assertRaises(sqlite3.DatabaseError):
            migrate(old.db)
        old.db.set_authorizer(None)
        self.assertEqual([tuple(row) for row in old.db.execute('SELECT * FROM mia_schema_migrations')], ledger)
        self.assertIsNone(old.db.execute("SELECT 1 FROM sqlite_master WHERE name='mia_event_interactions'").fetchone())
        migrate(old.db)
        self.assertEqual(self.original_rows(old), before)
        self.assertEqual(old.db.execute('SELECT COUNT(*) FROM mia_observation_meta').fetchone()[0], 0)
        self.assertEqual(old.db.execute('SELECT COUNT(*) FROM mia_event_interactions').fetchone()[0], 0)
        old.close()
        reopened = MemoryStore(self.path)
        self.assertEqual(self.original_rows(reopened), before)
        reopened.close()

    def test_process_exit_mid_ddl_rolls_back_without_partial_migration(self):
        old = self.legacy(self.path)
        self.remember(old)
        before = self.original_rows(old)
        old.close()
        code = '''import os, sqlite3, sys
from server.companion.schema import migrate
db=sqlite3.connect(sys.argv[1])
db.execute('PRAGMA foreign_keys=ON')
def interrupt(action, name, *unused):
    if action==sqlite3.SQLITE_CREATE_TABLE and name=='mia_vectors': os._exit(77)
    return sqlite3.SQLITE_OK
db.set_authorizer(interrupt)
migrate(db)
'''
        result = subprocess.run([sys.executable, '-c', code, str(self.path)], check=False, timeout=15)
        self.assertEqual(result.returncode, 77)
        old = self.legacy(self.path)
        try:
            self.assertEqual(before, self.original_rows(old))
            self.assertEqual(old.db.execute("SELECT name FROM sqlite_master WHERE name GLOB 'mia_*'").fetchall(), [])
        finally:
            old.close()
        recovered = MemoryStore(self.path)
        self.assertEqual(before, self.original_rows(recovered))
        recovered.close()

    def test_unknown_schema_and_checksum_are_refused_without_rewriting_facts(self):
        old = self.legacy(self.path)
        self.remember(old)
        before = self.original_rows(old)
        with old.db:
            old.db.execute('CREATE TABLE mia_unreviewed(value TEXT)')
        with self.assertRaisesRegex(ValueError, 'Unversioned'):
            migrate(old.db)
        self.assertEqual(before, self.original_rows(old))
        with old.db:
            old.db.execute('DROP TABLE mia_unreviewed')
        migrate(old.db)
        with old.db:
            old.db.execute("UPDATE mia_schema_migrations SET checksum='unreviewed'")
        with self.assertRaisesRegex(ValueError, 'checksum'):
            migrate(old.db)
        self.assertEqual(before, self.original_rows(old))
        old.close()

    def test_cross_owner_associations_and_event_evidence_cannot_be_inserted(self):
        new = MemoryStore(self.path)
        first = self.remember(new)
        peer = self.remember(new, '喜欢音乐', owner='user:2:agent:b')
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'scope_mismatch'), new.db:
                new.db.execute('INSERT INTO mia_associations(memory_id,related_id,relation,evidence,content_version,related_version) '
                               'VALUES (?,?,?, ?,1,1)', (first, peer, 'fixture', 'synthetic'))
            with new.db:
                new.db.execute("INSERT INTO mia_events(id,owner,event_type,idempotency_key,state,session_id,turn_id,created_at,updated_at) "
                               "VALUES ('event','user:1:agent:a','continuity','fixture','active','one','one',1,1)")
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'scope_mismatch'), new.db:
                new.db.execute('INSERT INTO mia_event_evidence(event_id,memory_id,content_version,evidence_version) VALUES (?,?,1,1)',
                               ('event', peer))
        finally:
            new.close()


if __name__ == '__main__':
    unittest.main()
