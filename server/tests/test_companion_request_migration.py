"""V2 additive upgrade and fail-closed rollback; no Provider traffic."""
import hashlib
from pathlib import Path
import sqlite3
import unittest

from server.companion.jobs import JobStore
from server.companion.schema import _schema_checksum, _statements, check_worker_rollback, migrate
from server.tests import test_companion_jobs as fixtures


class RequestMigrationTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.JobTests.setUp
    tearDown = fixtures.JobTests.tearDown
    event = fixtures.JobTests.event

    def test_old_and_new_admission_are_explicit_and_idempotent(self):
        old = JobStore(self.store, policy_version=1).submit(self.event('old'))
        new = JobStore(self.store, policy_version=2).submit(self.event('new'))
        self.assertEqual(self.jobs.policy(old), {'version': 1})
        self.assertEqual(self.jobs.policy(new)['version'], 2)
        self.assertEqual(self.jobs.policy(new)['phase'], 'admitted')
        duplicate = JobStore(self.store, policy_version=2).submit(self.event('old'))
        self.assertEqual(duplicate['id'], old['id'])
        self.assertEqual(self.jobs.policy(old), {'version': 1})
        before = [tuple(r) for r in self.store.db.execute('SELECT * FROM mia_schema_migrations')]
        migrate(self.store.db)
        self.assertEqual(before, [tuple(r) for r in self.store.db.execute('SELECT * FROM mia_schema_migrations')])

    def test_terminal_trigger_clears_feedback_in_same_transaction(self):
        job = JobStore(self.store, policy_version=2).submit(self.event())
        with self.store.db:
            self.store.db.execute("UPDATE mia_job_policy SET feedback='diagnostic' WHERE job_id=?", (job['id'],))
        self.jobs.update(job, 'cancelled', error='memory_changed')
        self.assertEqual(self.jobs.policy(job)['phase'], 'terminal')
        self.assertIsNone(self.jobs.policy(job)['feedback'])
        self.assertTrue(check_worker_rollback(self.store.db)['ledger_preserved'])

    def test_rollback_refuses_active_v2_and_unknown_native_completion(self):
        job = JobStore(self.store, policy_version=2).submit(self.event())
        with self.assertRaisesRegex(ValueError, 'terminal tasks'):
            check_worker_rollback(self.store.db)
        with self.store.db:
            self.store.db.execute("INSERT INTO mia_model_requests(job_id,ordinal,slot_acquired_at,rechecked_at,intent_at,outcome) "
                "VALUES (?,1,100,100,100,'intent')", (job['id'],))
        self.jobs.update(job, 'cancelled')
        with self.assertRaises(ValueError):
            check_worker_rollback(self.store.db)
        with self.store.db:
            self.store.db.execute("UPDATE mia_model_requests SET native_finished_at=101,outcome='process_lost'")
        self.assertTrue(check_worker_rollback(self.store.db)['compatible_worker_required'])

    def test_v3_upgrade_failure_rolls_back_all_new_objects_then_retries(self):
        # An isolated V3 schema uses the unchanged historical SQL/digests.
        db = sqlite3.connect(':memory:')
        try:
            for table, sql in self.store.db.execute("SELECT name,sql FROM sqlite_master WHERE name IN ('owners','memories','sources','traits','checkpoints')"):
                db.execute(sql)
            db.execute('CREATE TABLE mia_schema_migrations(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,schema_checksum TEXT NOT NULL,applied_at INTEGER NOT NULL)')
            for version, name in enumerate(('schema.sql', 'schema-v2.sql', 'schema-v3.sql'), 1):
                sql = Path('server/companion', name).read_text()
                for statement in _statements(sql):
                    db.execute(statement)
                db.execute('INSERT INTO mia_schema_migrations VALUES (?,?,?,1)',
                    (version, hashlib.sha256(sql.encode()).hexdigest(), _schema_checksum(db)))
            db.commit()
            digest = _schema_checksum(db)
            def reject(action, name, *unused):
                return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_TABLE and name == 'mia_model_requests' else sqlite3.SQLITE_OK
            db.set_authorizer(reject)
            with self.assertRaises(sqlite3.DatabaseError):
                migrate(db)
            db.set_authorizer(None)
            self.assertEqual(_schema_checksum(db), digest)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mia_schema_migrations').fetchone()[0], 3)
            migrate(db)
            migrate(db)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mia_schema_migrations').fetchone()[0], 11)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        finally:
            db.close()

    def test_old_migration_contract_rejects_v4_before_processing_jobs(self):
        rows = self.store.db.execute('SELECT version FROM mia_schema_migrations').fetchall()
        self.assertEqual([row[0] for row in rows], [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11])
        # The immutable V3 loader requires len(rows)<=3 and the exact checksum.
        self.assertFalse(1 <= len(rows) <= 3)
        self.assertNotEqual(self.store.db.execute('SELECT schema_checksum FROM mia_schema_migrations WHERE version=3').fetchone()[0],
            _schema_checksum(self.store.db))


if __name__ == '__main__':
    unittest.main()
