import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from server.companion import emotional_system as emotions, affect, character
from server.companion.memory import MemoryStore, MemoryError
from server.tests import test_companion_context as fixture


class EmotionalSystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.now = 1800000000
        self.store = MemoryStore(Path(self.temp.name) / 'memory.sqlite3', clock=lambda: self.now)
        self.owner = 'user:1:agent:a'

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def enable(self, owner=None, **changes):
        owner = owner or self.owner
        state = emotions.status(self.store, owner)
        for key, values in changes.items():
            state['dimensions'][key].update(values)
        return emotions.manage(self.store, owner, dict(action='configure', version=state['version'],
            enabled=True, dimensions=state['dimensions']))

    def test_complete_catalog_and_defaults_are_independent_per_agent(self):
        self.assertEqual(len(emotions.CATALOG), 51)
        state = self.enable()
        self.assertTrue(state['enabled'])
        self.assertFalse(emotions.status(self.store, 'user:1:agent:b')['enabled'])
        self.assertFalse(emotions.status(self.store, 'user:2:agent:a')['enabled'])

    def test_configuration_conflict_and_invalid_numbers_are_atomic(self):
        state = self.enable()
        invalid = copy.deepcopy(state['dimensions'])
        invalid['joy']['baseline'] = float('nan')
        with self.assertRaises(MemoryError):
            emotions.manage(self.store, self.owner, dict(action='configure', version=state['version'],
                enabled=True, dimensions=invalid))
        with self.assertRaises(MemoryError):
            emotions.manage(self.store, self.owner, dict(action='reset', version=0))
        self.assertEqual(emotions.status(self.store, self.owner)['version'], state['version'])

    def test_all_dimensions_can_be_adjusted_when_dynamic(self):
        state = emotions.status(self.store, self.owner)
        for item in state['dimensions'].values():
            item['mode'] = 'dynamic'
        state = emotions.manage(self.store, self.owner, dict(action='configure', version=0,
            enabled=True, dimensions=state['dimensions']))
        updated = emotions.manage(self.store, self.owner, dict(action='adjust', version=state['version'],
            values={key: 42 for key in emotions.CATALOG}))
        self.assertTrue(all(value == 42 for value in updated['values'].values()))

    def test_decay_lock_fixed_disabled_and_limits(self):
        state = self.enable(joy={'max_change': 5, 'half_life_hours': 1},
                            sadness={'mode': 'locked', 'lock_until': self.now + 3600},
                            anger={'enabled': False})
        proposal = {'changes': [{'dimension': key, 'delta': 100} for key in ['joy', 'sadness', 'anger', 'warmth']]}
        with self.store.lock, self.store.db:
            self.assertTrue(emotions.commit(self.store, self.owner, 'device1', 'turn1', proposal, state['version']))
        state = emotions.status(self.store, self.owner)
        self.assertEqual(state['values']['joy'], 15)
        self.assertEqual(state['values']['sadness'], 10)
        self.assertEqual(state['values']['anger'], 10)
        self.assertEqual(state['values']['warmth'], 85)
        self.now += 3600
        self.assertEqual(emotions.status(self.store, self.owner)['values']['joy'], 12.5)

    def test_duplicate_and_concurrent_device_completion_never_overwrite(self):
        first = self.enable()
        proposal = {'changes': [{'dimension': 'joy', 'delta': 10}]}
        with self.store.lock, self.store.db:
            self.assertTrue(emotions.commit(self.store, self.owner, 'qq', 'turn', proposal, first['version']))
            self.assertFalse(emotions.commit(self.store, self.owner, 'app', 'other', proposal, first['version']))
            current = emotions.status(self.store, self.owner)
            self.assertFalse(emotions.commit(self.store, self.owner, 'qq', 'turn', proposal, current['version']))
        other_store = MemoryStore(Path(self.temp.name) / 'memory.sqlite3', clock=lambda: self.now)
        try:
            self.assertEqual(emotions.status(other_store, self.owner)['values']['joy'], 20)
        finally:
            other_store.close()

    def test_appraisal_requires_original_evidence_and_never_stores_quotes(self):
        proposal = emotions.parse_appraisal({'changes': [{'dimension': 'joy', 'delta': 10, 'quote': '谢谢你'}]}, '谢谢你陪我')
        self.assertNotIn('quote', proposal['changes'][0])
        with self.assertRaises(MemoryError):
            emotions.parse_appraisal({'changes': [{'dimension': 'joy', 'delta': 10, 'quote': '编造内容'}]}, '谢谢你')

    def test_legacy_character_inherits_and_system_disable_does_not_resume_legacy(self):
        character.update(self.store, self.owner, dict(values=dict(character.YOUMI_V1, warmth=.6), enabled=True, version=0))
        state = self.enable()
        self.assertEqual(state['values']['warmth'], 60)
        self.assertIn('affect_system', affect.state(self.store, self.owner))
        emotions.manage(self.store, self.owner, dict(action='configure', version=state['version'],
            enabled=False, dimensions=state['dimensions']))
        self.assertFalse(affect.state(self.store, self.owner)['enabled'])

    def test_history_reset_and_retention_are_agent_scoped(self):
        state = self.enable()
        updated = emotions.manage(self.store, self.owner, dict(action='adjust', version=state['version'], values={'joy': 80}))
        reset = emotions.manage(self.store, self.owner, dict(action='reset', version=updated['version'], dimensions=['joy']))
        self.assertEqual(reset['values']['joy'], 10)
        self.assertEqual(len(emotions.manage(self.store, self.owner, {'action': 'history'})['records']), 3)
        self.assertEqual(emotions.manage(self.store, 'user:1:agent:b', {'action': 'history'})['records'], [])
        self.now += 91 * 86400
        self.assertEqual(emotions.manage(self.store, self.owner, {'action': 'history'})['records'], [])

    def test_v8_to_v9_ddl_failure_rolls_back_and_retries(self):
        import sqlite3
        from server.companion.schema import migrate, _schema_checksum
        with self.store.db:
            for table in ('mia_affect_system_history', 'mia_affect_system_turns', 'mia_affect_system'):
                self.store.db.execute('DROP TABLE ' + table)
            self.store.db.execute('DELETE FROM mia_schema_migrations WHERE version=9')
        before = _schema_checksum(self.store.db)
        self.store.db.set_authorizer(lambda action, name, *rest: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TABLE and name == 'mia_affect_system_history' else sqlite3.SQLITE_OK)
        with self.assertRaises(sqlite3.DatabaseError):
            migrate(self.store.db)
        self.store.db.set_authorizer(None)
        self.assertEqual(_schema_checksum(self.store.db), before)
        migrate(self.store.db)
        self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_legacy_summary_reflects_new_current_state(self):
        state = self.enable()
        emotions.manage(self.store, self.owner, dict(action='adjust', version=state['version'], values={'anger': 90}))
        self.assertEqual(affect.state(self.store, self.owner)['label'], '生气')

    def test_offline_installer_preserves_audited_legacy_namespace_and_rejects_customizations(self):
        import subprocess
        from server.companion import install_emotional
        root = Path(self.temp.name) / 'candidate'
        root.mkdir()
        for name in install_emotional.UPDATED:
            baseline = subprocess.check_output(['git', 'show', install_emotional.BASE + ':server/companion/' + name], text=True)
            (root / name).write_text(install_emotional.runtime_text(name, baseline, True))
        changed = install_emotional.changes(root, legacy=True)
        self.assertIn("inputs['mia_affect']", changed['context.py'])
        self.assertIn('YOUMI_V1', changed['character.py'])
        self.assertIn('from .xiaozhi_adapter import _store', changed['management.py'])
        (root / 'context.py').write_text('custom source')
        with self.assertRaisesRegex(ValueError, 'Unreviewed'):
            install_emotional.changes(root, legacy=True)


class EmotionalContextTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.ContextBuilderTests.setUp
    tearDown = fixture.ContextBuilderTests.tearDown
    build = fixture.ContextBuilderTests.build

    async def test_projection_is_ephemeral_and_successful_completion_commits_once(self):
        state = emotions.status(self.store, self.scope.owner)
        state = emotions.manage(self.store, self.scope.owner, dict(action='configure', version=0,
            enabled=True, dimensions=state['dimensions']))
        before = await self.build()
        self.assertIn('affect_system=', before.request()['messages'][0]['content'])
        proposal = emotions.parse_appraisal({'changes': [{'dimension': 'joy', 'delta': 10, 'quote': '谢谢你'}]}, '谢谢你')
        proposal['system_version'] = state['version']
        projected = await self.builder.with_affect(before, self.auth, proposal)
        import json
        self.assertEqual(json.loads(projected.inputs_json)['youmi_affect']['affect_system']['values']['joy'], 20)
        self.assertEqual(emotions.status(self.store, self.scope.owner)['values']['joy'], 10)
        with self.store.lock, self.store.db:
            self.assertTrue(affect.commit(self.store, self.scope.owner, 'app', 'completed', '谢谢你', proposal, 0))
            self.assertFalse(affect.commit(self.store, self.scope.owner, 'app', 'completed', '谢谢你', proposal, 0))


if __name__ == '__main__':
    unittest.main()
