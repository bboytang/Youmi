"""Bounded persistent cognition and owner-managed retention, real SQLite jobs."""

import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from server.companion import memory_management, memory_state
from server.companion.cognition import effective
from server.companion.jobs import JobStore, MemoryWorker
from server.companion.memory import MemoryError
from server.companion.memory_artifacts import validate_actions
from server.companion.memory_pins import configure as pin
from server.companion.memory_reflection import validate_proposal
from server.companion.memory_reflection_policy import YoumiReflectionPolicy
from server.companion.proposal import ExtractionError
from server.companion.schema import migrate
from server.tests import test_companion_jobs as fixture
from server.tests.companion_schema_fixtures import remove_v17


class CognitivePolicyTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.JobTests.setUp
    tearDown = fixture.JobTests.tearDown
    event = fixture.JobTests.event

    async def enable(self, dynamic=True):
        current = memory_state.cognitive_status(self.store, self.scope.owner)
        for config in current['dimensions'].values():
            config['mode'] = 'dynamic' if dynamic else 'fixed'
        memory_state._configure_cognition(self.store, self.scope.owner,
            dict(action='configure', enabled=True, dimensions=current['dimensions'], version=current['version']))
        await YoumiReflectionPolicy(self.store).manage(self.scope, self.policy,
            dict(action='configure', enabled=True, interval_seconds=60, version=0))
        self.jobs = JobStore(self.store, policy_version=2)
        self.proposal = dict(reflection_version=1, memory_actions=[], cognitive_adjustment=dict(code='1100', quote='我喜欢蓝色'))
        async def resolve(scope):
            def propose(sources, context):
                self.calls.append((sources, context))
                return copy.deepcopy(self.proposal)
            return propose
        self.worker = MemoryWorker(self.jobs, self.policy, resolve, encoder=self.encoder)

    async def test_effective_zero_fixed_bounds_and_direction(self):
        await self.enable()
        current = memory_state.cognitive_status(self.store, self.scope.owner)
        self.assertEqual(current['effective'], dict(recall=7, impression=3, expression=.5, creativity=.7))
        for sign in (-1, 1):
            values = effective(current['dimensions'], {k: sign * 20 for k in current['energy']})
            for key, value in values.items():
                config = current['dimensions'][key]
                self.assertGreaterEqual(value, config['minimum'])
                self.assertLessEqual(value, config['maximum'])
                self.assertEqual(isinstance(value, int), key in {'recall', 'impression'})
                self.assertEqual(value >= config['baseline'], sign > 0)
        for item in current['dimensions'].values():
            item['mode'] = 'fixed'
        self.assertEqual(effective(current['dimensions'], {k: 20 for k in current['energy']}), current['effective'])

    async def test_successful_reflection_updates_once_and_restart_preserves_values(self):
        await self.enable()
        result = await self.worker.submit(self.event())
        self.assertTrue(await self.worker.run_once())
        state = memory_state.cognitive_status(self.store, self.scope.owner)
        self.assertEqual(state['energy'], dict(recall=1, impression=1, expression=-1, creativity=-1))
        self.assertEqual(self.jobs.current(result)['state'], 'committed')
        self.assertFalse(await self.worker.run_once())
        duplicate = await self.worker.submit(self.event())
        self.assertEqual(duplicate['id'], result['id'])
        self.assertEqual(memory_state.cognitive_status(self.store, self.scope.owner), state)
        from server.companion.memory import MemoryStore
        reopened = MemoryStore(self.path)
        try:
            self.assertEqual(memory_state.cognitive_status(reopened, self.scope.owner), state)
        finally:
            reopened.close()
        self.assertEqual(len(self.calls), 1)

    async def test_disabled_fixed_and_foreign_states_are_not_affected(self):
        await self.enable(dynamic=False)
        before = memory_state.cognitive_status(self.store, self.scope.owner)
        await self.worker.submit(self.event())
        await self.worker.run_once()
        self.assertEqual(memory_state.cognitive_status(self.store, self.scope.owner), before)
        self.assertFalse(memory_state.cognitive_status(self.store, self.peer.owner)['enabled'])
        self.assertEqual(memory_state.cognitive_status(self.store, self.peer.owner)['version'], 0)

    async def test_untrusted_code_or_old_history_quote_is_rejected(self):
        await self.enable()
        from server.companion.extraction import context_for
        context = context_for(self.store, self.scope.owner)
        context.reflection = dict(maintenance_due=False)
        for value in [dict(code='1111', quote='历史或人设'), dict(code='11111', quote='我喜欢蓝色'),
                      dict(code='++++', quote='我喜欢蓝色'), dict(code=True, quote='我喜欢蓝色'),
                      dict(code='1111', quote='我喜欢蓝色', version=999)]:
            with self.subTest(value=value), self.assertRaises(ExtractionError):
                validate_proposal(dict(reflection_version=1, memory_actions=[], cognitive_adjustment=value), ['我喜欢蓝色'], context)
        context.cognition['enabled'] = False
        with self.assertRaises(ExtractionError):
            validate_proposal(self.proposal, ['我喜欢蓝色'], context)

    async def test_admin_change_during_generation_skips_old_energy_advice(self):
        await self.enable()
        current = memory_state.cognitive_status(self.store, self.scope.owner)
        async def resolve(scope):
            def propose(sources, context):
                memory_state._configure_cognition(self.store, self.scope.owner,
                    dict(action='adjust', version=current['version'], energy=dict(recall=8)))
                return self.proposal
            return propose
        self.worker.resolve = resolve
        await self.worker.submit(self.event())
        await self.worker.run_once()
        self.assertEqual(memory_state.cognitive_status(self.store, self.scope.owner)['energy']['recall'], 8)

    async def test_failure_after_adjustment_rolls_back_and_retry_applies_only_once(self):
        await self.enable()
        self.proposal['memory_actions'] = [dict(action='remember', memory=self.output['memories'][0])]
        before = memory_state.cognitive_status(self.store, self.scope.owner)
        result = await self.worker.submit(self.event())
        with self.store.db:
            self.store.db.execute("CREATE TRIGGER synthetic_fail_cognitive_terminal BEFORE UPDATE ON mia_jobs "
                "WHEN NEW.state='committed' BEGIN SELECT RAISE(ABORT,'synthetic atomic failure'); END")
        try:
            await self.worker.run_once()
            self.assertEqual(self.jobs.current(result)['state'], 'retry_wait')
            self.assertEqual(memory_state.cognitive_status(self.store, self.scope.owner), before)
            self.assertEqual(self.store.list_memories(self.scope.owner), [])
        finally:
            with self.store.db:
                self.store.db.execute('DROP TRIGGER synthetic_fail_cognitive_terminal')
        self.now[0] += 4
        await self.worker.run_once()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(memory_state.cognitive_status(self.store, self.scope.owner)['energy']['recall'], 1)
        self.assertEqual(len(self.store.list_memories(self.scope.owner)), 1)

    async def test_cognitive_storage_fault_does_not_roll_back_facts_or_emotion(self):
        await self.enable()
        self.proposal['memory_actions'] = [dict(action='remember', memory=self.output['memories'][0])]
        from server.companion import emotional_system
        emotion = emotional_system.status(self.store, self.scope.owner)
        before = memory_state.cognitive_status(self.store, self.scope.owner)
        result = await self.worker.submit(self.event())
        self.store.db.set_authorizer(lambda action, name, *args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_UPDATE and name == 'youmi_cognitive_state' else sqlite3.SQLITE_OK)
        try:
            await self.worker.run_once()
        finally:
            self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(self.jobs.current(result)['state'], 'committed')
        self.assertEqual(len(self.store.list_memories(self.scope.owner)), 1)
        self.assertEqual(memory_state.cognitive_status(self.store, self.scope.owner), before)
        self.assertEqual(emotional_system.status(self.store, self.scope.owner), emotion)


class MemoryPinTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.JobTests.setUp
    tearDown = fixture.JobTests.tearDown

    def fact(self, content='我喜欢蓝色', owner=None, sensitive=False):
        owner = owner or self.scope.owner
        return self.store.apply_extraction(owner, content, self.store.snapshot(owner)['revision'], [content],
            dict(memories=[dict(category='fact', content=content, source_index=0, sensitive=sensitive)]), content)['memories'][0]

    def expected(self, identifier, owner=None):
        row = self.store.db.execute('SELECT o.revision,x.content_version,x.evidence_version FROM memories m '
            'JOIN owners o ON o.owner=m.owner JOIN mia_memory_meta x ON x.memory_id=m.id WHERE m.owner=? AND m.id=?',
            (owner or self.scope.owner, identifier)).fetchone()
        return dict(row)

    def set_pin(self, identifier, value=True, expected=None):
        return pin(self.store, self.scope.owner, dict(action='pin', id=identifier, pinned=value,
            expected=expected or self.expected(identifier)))

    async def test_pin_is_separate_confirmed_cas_flag_and_does_not_copy_fact(self):
        identifier = self.fact()
        old = self.expected(identifier)
        before = self.store.list_memories(self.scope.owner)[0]
        self.set_pin(identifier)
        detail = memory_management.manage(self.store, self.scope.owner, dict(action='detail', id=identifier))
        self.assertIs(detail['memory']['pinned'], True)
        self.assertTrue(detail['memory']['confirmed'])
        self.assertGreater(detail['revision'], old['revision'])
        self.assertEqual(self.store.list_memories(self.scope.owner)[0], before)
        with self.assertRaises(MemoryError):
            self.set_pin(identifier, False, old)
        self.set_pin(identifier, False)
        self.assertFalse(memory_management.manage(self.store, self.scope.owner, dict(action='detail', id=identifier))['memory']['pinned'])

    async def test_foreign_pending_and_missing_versions_cannot_be_pinned(self):
        own = self.fact()
        foreign = self.fact('我喜欢茶', self.peer.owner)
        pending = self.fact('我的私密经历', sensitive=True)
        with self.assertRaises(MemoryError):
            self.set_pin(foreign, expected=self.expected(own))
        with self.assertRaises(MemoryError):
            self.set_pin(pending)
        for bad in [None, {}, dict(revision=True, content_version=1, evidence_version=1)]:
            with self.assertRaises(MemoryError):
                pin(self.store, self.scope.owner, dict(action='pin', id=own, pinned=True, expected=bad))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_pins').fetchone()[0], 0)

    async def test_edit_keeps_pin_unconfirm_hides_and_delete_clear_cascade(self):
        first = self.fact()
        self.set_pin(first)
        self.store.edit_memory(self.scope.owner, first, '我现在喜欢绿色')
        self.assertIsNotNone(self.store.db.execute('SELECT 1 FROM youmi_memory_pins WHERE memory_id=?', (first,)).fetchone())
        self.store.edit_memory(self.scope.owner, first, '我现在喜欢绿色', confirmed=False)
        from server.companion.memory_repository import YoumiMemoryRepository
        self.assertEqual((await YoumiMemoryRepository(self.store).capture(self.scope, self.policy)).records, ())
        self.store.delete_memory(self.scope.owner, first)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_pins').fetchone()[0], 0)
        second = self.fact('我喜欢画画')
        self.set_pin(second)
        self.store.clear(self.scope.owner)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_pins').fetchone()[0], 0)

    async def test_pins_block_maintenance_candidates_and_manual_merge_until_unpinned(self):
        first, second = self.fact(), self.fact('我喜欢画画')
        self.set_pin(first)
        from server.companion.extraction import context_for
        context = context_for(self.store, self.scope.owner)
        with self.assertRaises(ExtractionError):
            validate_actions([dict(action='propose_retirement', key='retire', title='蓝色', evidence=[dict(id=first, quote='我喜欢蓝色')])], context)
        command = dict(operation_id='pin-merge', retired_id=first, survivor_id=second,
            revision=self.store.snapshot(self.scope.owner)['revision'], versions=dict(
                retired=[self.expected(first)['content_version'], self.expected(first)['evidence_version']],
                survivor=[self.expected(second)['content_version'], self.expected(second)['evidence_version']]))
        with self.assertRaises(MemoryError):
            memory_state._merge(self.store, self.scope.owner, command)
        self.set_pin(first, False)
        command['revision'] = self.store.snapshot(self.scope.owner)['revision']
        self.assertFalse(memory_state._merge(self.store, self.scope.owner, command)['duplicate'])

    async def test_pin_rollback_keeps_original_revision_and_flag(self):
        identifier = self.fact()
        before = list(self.store.db.iterdump())
        with self.assertRaises(RuntimeError):
            with self.store.db:
                self.store.db.execute('INSERT INTO youmi_memory_pins VALUES (?,?)', (identifier, 100))
                raise RuntimeError('synthetic rollback')
        self.assertEqual(list(self.store.db.iterdump()), before)

    async def test_pin_invalidates_old_maintenance_views_but_preserves_notes(self):
        identifier = self.fact()
        job = self.jobs.submit(fixture.JobTests.event(self, 'artifact-proof'))
        versions = self.expected(identifier)
        with self.store.db:
            for kind in ['note', 'retirement']:
                self.store.db.execute('INSERT INTO youmi_memory_artifacts VALUES (?,?,?,?,?,?,?,?,?)',
                    (kind, self.scope.owner, kind, kind, '蓝色', 1, job['id'], 100, 100))
                self.store.db.execute('INSERT INTO youmi_memory_artifact_evidence VALUES (?,?,?,?,?)',
                    (kind, identifier, versions['content_version'], versions['evidence_version'], 0))
        self.set_pin(identifier)
        self.assertEqual([r[0] for r in self.store.db.execute('SELECT kind FROM youmi_memory_artifacts')], ['note'])
        self.set_pin(identifier, False)
        self.assertEqual([r[0] for r in self.store.db.execute('SELECT kind FROM youmi_memory_artifacts')], ['note'])

    async def test_pin_change_invalidates_managed_cache_without_altering_source(self):
        import importlib.util
        if not importlib.util.find_spec('tantivy'):
            self.skipTest('Optional native shadow dependency')
        from server.companion.memory_runtime import YoumiMemoryRuntime
        from server.companion.semantic import SemanticMemoryIndex
        from server.companion.memory_cache_lifecycle import YoumiManagedMemoryCache
        identifier = self.fact()
        runtime = YoumiMemoryRuntime(SemanticMemoryIndex(self.store, self.encoder), managed_shadow_cache=True)
        await runtime.shadow_retrieve(self.scope, self.policy, '蓝色', use_vectors=False)
        directory = YoumiManagedMemoryCache(self.path).directory(self.scope.owner)
        self.assertTrue((directory / 'index').exists())
        versions = self.expected(identifier)
        self.set_pin(identifier)
        self.assertFalse((directory / 'index').exists())
        updated = self.expected(identifier)
        self.assertEqual(updated['content_version'], versions['content_version'])
        self.assertEqual(updated['evidence_version'], versions['evidence_version'])


class PinMigrationTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from server.companion.memory import MemoryStore
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'memory.sqlite3'
        self.store = MemoryStore(self.path)
        self.store.snapshot('user:1:agent:a')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def v16(self):
        with self.store.db:
            remove_v17(self.store.db)
            self.store.db.execute('DELETE FROM mia_schema_migrations WHERE version=17')
        namespace = {'__file__': str(Path('server/companion/schema.py').resolve())}
        source = subprocess.check_output(['git', 'show', '56aefbce:server/companion/schema.py'], text=True)
        exec(compile(source, 'frozen-v16.py', 'exec'), namespace)
        namespace['migrate'](self.store.db)
        return namespace['migrate']

    def test_v17_additive_idempotent_and_frozen_v16_loader_rejects_it(self):
        old = self.v16()
        before = [tuple(row) for row in self.store.db.execute('SELECT * FROM owners')]
        migrate(self.store.db)
        self.assertEqual([tuple(row) for row in self.store.db.execute('SELECT * FROM owners')], before)
        dump = list(self.store.db.iterdump())
        migrate(self.store.db)
        self.assertEqual(list(self.store.db.iterdump()), dump)
        with self.assertRaises(ValueError):
            old(self.store.db)

    def test_v17_failure_and_process_exit_leave_v16_reopenable(self):
        old = self.v16()
        before = list(self.store.db.iterdump())
        self.store.db.set_authorizer(lambda action, name, *args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TRIGGER and name == 'youmi_memory_pin_delete' else sqlite3.SQLITE_OK)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                migrate(self.store.db)
        finally:
            self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(list(self.store.db.iterdump()), before)
        code = '''import os,sqlite3,sys
from server.companion.schema import migrate
db=sqlite3.connect(sys.argv[1])
def interrupt(action,name,*args):
    if action==sqlite3.SQLITE_CREATE_TRIGGER and name=='youmi_memory_pin_delete': os._exit(41)
    return sqlite3.SQLITE_OK
db.set_authorizer(interrupt); migrate(db)
'''
        result = subprocess.run([sys.executable, '-c', code, str(self.path)], capture_output=True)
        self.assertEqual(result.returncode, 41, result.stderr)
        self.assertEqual(list(self.store.db.iterdump()), before)
        old(self.store.db)
        migrate(self.store.db)
