"""Offline updater failure recovery and real V7-to-V8 SQLite migration, no production writes."""

import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from server.companion import install_astrbot, schema
from server.companion.memory import MemoryStore


class InstalledRuntimeTests(unittest.TestCase):
    def test_candidate_apply_preserves_unrelated_files_and_refuses_drift(self):
        # Synthetic anchors exercise the updater, not a claim about deployed binaries.
        originals = {'core/http_server.py': 'from aiohttp import web\nif True:\n                app = web.Application()\n',
            'mia_companion/xiaozhi_adapter.py': 'async def start(worker):\n    await worker.start()\n',
            'mia_companion/schema.py': '# synthetic V7 fixture\n'}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, text in originals.items():
                (root / name).parent.mkdir(exist_ok=True, parents=True)
                (root / name).write_text(text)
            (root / 'unrelated').write_text('preserved')
            expected = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in originals.items()}
            with patch.object(install_astrbot, 'EXPECTED', expected), patch.object(sys, 'argv', ['updater', str(root), '--apply']):
                install_astrbot.main()
                for name, text in originals.items():
                    self.assertEqual((root / (name + '.astrbot-original')).read_text(), text)
                self.assertIn('attach_runtime(store, worker)', (root / 'mia_companion/xiaozhi_adapter.py').read_text())
                self.assertEqual((root / 'unrelated').read_text(), 'preserved')
                with self.assertRaisesRegex(ValueError, 'Unreviewed'):
                    install_astrbot.changes(root)

    def test_v7_v8_failure_rolls_back_and_old_loader_rejects_new_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = MemoryStore(root / 'memory.sqlite3')
            owner = 'user:1:agent:a'
            store.apply_extraction(owner, 'existing-app', store.snapshot(owner)['revision'], ['喜欢蓝色'],
                {'memories': [{'category': 'preference', 'content': '喜欢蓝色', 'source_index': 0}]}, 'before-upgrade')
            # Remove ONLY the new empty V8 table in this synthetic test DB.
            with store.db:
                from server.tests.companion_schema_fixtures import remove_v11
                remove_v11(store.db)
                for table in ('youmi_affect_system_history', 'youmi_affect_system_turns', 'youmi_affect_system'):
                    store.db.execute('DROP TABLE ' + table)
                store.db.execute('DROP TABLE mia_astrbot_turns')
                store.db.execute('DELETE FROM mia_schema_migrations WHERE version>=8')
            legacy_source = subprocess.check_output(['git', 'show',
                '739c509eb3f4bc231d879367b33a15a8a94c38f5:server/companion/schema.py'], text=True)
            legacy = {'__file__': str(Path(schema.__file__))}
            exec(compile(legacy_source, 'frozen-v7-schema.py', 'exec'), legacy)
            legacy['migrate'](store.db)
            before = list(store.db.iterdump())
            store.db.set_authorizer(lambda action, name, *args: sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_CREATE_INDEX and name == 'mia_astrbot_session' else sqlite3.SQLITE_OK)
            with self.assertRaises(sqlite3.DatabaseError):
                schema.migrate(store.db)
            store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
            self.assertEqual(list(store.db.iterdump()), before)
            schema.migrate(store.db)
            self.assertEqual(store.db.execute('SELECT MAX(version) FROM mia_schema_migrations').fetchone()[0], 17)
            self.assertEqual(store.list_memories(owner)[0]['content'], '喜欢蓝色')
            with self.assertRaisesRegex(ValueError, 'version or checksum'):
                legacy['migrate'](store.db)
            schema.migrate(store.db)  # V8-aware bridge-disabled rollback remains compatible.
            self.assertEqual(store.db.execute('PRAGMA quick_check').fetchone()[0], 'ok')
            store.close()
