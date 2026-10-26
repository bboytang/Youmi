"""V10 to latest: late DDL failures must roll back the whole migration."""

import asyncio
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest

from server.companion import emotional_system
from server.companion.schema import migrate
from server.tests import test_companion_memory_state as migration_fixture


class FullMigrationTests(unittest.IsolatedAsyncioTestCase):
    setUp = migration_fixture.MigrationTests.setUp
    tearDown = migration_fixture.MigrationTests.tearDown
    fact = migration_fixture.MigrationTests.fact
    make_v10 = migration_fixture.MigrationTests.make_v10

    def prepare(self):
        self.store.clock = lambda: 1800000000
        pending = self.fact('我有一条等待确认的合成事实')
        self.store.edit_memory(self.scope.owner, pending, '我有一条等待确认的合成事实', confirmed=False)
        old = self.make_v10()
        state = emotional_system.status(self.store, self.scope.owner)
        for index, item in enumerate(state['dimensions'].values()):
            item.update(mode=('fixed', 'dynamic', 'locked')[index % 3], baseline=index,
                lock_until=1800010000 if index % 3 == 2 else 0)
        emotional_system.manage(self.store, self.scope.owner, dict(action='configure', version=state['version'],
            enabled=True, dimensions=state['dimensions']))
        tables = [row[0] for row in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table' "
            "AND (name LIKE 'mia_%' OR name IN ('owners','memories','sources','traits','checkpoints')) "
            "AND name != 'mia_schema_migrations' ORDER BY name")]
        before = {name: [tuple(row) for row in self.store.db.execute('SELECT * FROM "' + name + '" ORDER BY rowid')]
            for name in tables}
        return old, before

    def verify_upgrade(self, before):
        migrate(self.store.db)
        dump = list(self.store.db.iterdump())
        migrate(self.store.db)
        self.assertEqual(list(self.store.db.iterdump()), dump)
        for name, rows in before.items():
            self.assertEqual([tuple(row) for row in self.store.db.execute('SELECT * FROM "' + name + '" ORDER BY rowid')], rows, name)
        self.assertEqual(len(emotional_system.status(self.store, self.scope.owner)['dimensions']), 51)
        self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(), [])
        for name in ('youmi_memory_source_state', 'youmi_memory_turn_state', 'youmi_memory_pins'):
            self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0], 0)

    async def test_late_trigger_failure_preserves_v10_then_repeat_upgrade_preserves_every_old_row(self):
        old, before = self.prepare()
        dump = list(self.store.db.iterdump())
        self.store.db.set_authorizer(lambda action, name, *args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TABLE and name == 'youmi_memory_engine' else sqlite3.SQLITE_OK)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                migrate(self.store.db)
        finally:
            self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(list(self.store.db.iterdump()), dump)
        old(self.store.db)
        self.verify_upgrade(before)

    async def test_process_exit_at_last_migration_leaves_original_v10_reopenable(self):
        old, before = self.prepare()
        dump = list(self.store.db.iterdump())
        script = '''import os,sqlite3,sys
from server.companion.schema import migrate
db=sqlite3.connect(sys.argv[1]); db.execute('PRAGMA foreign_keys=ON')
def stop(action,name,*args):
 if action==sqlite3.SQLITE_CREATE_TABLE and name=='youmi_memory_engine': os._exit(73)
 return sqlite3.SQLITE_OK
db.set_authorizer(stop); migrate(db)
'''
        result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', script,
            str(Path(self.temp.name) / 'memory.sqlite3')], capture_output=True)
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertEqual(list(self.store.db.iterdump()), dump)
        old(self.store.db)
        self.verify_upgrade(before)
