"""Real-store evidence lifecycle, completion transactions and owner isolation."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest

from server.companion import character, emotional_system, features
from server.companion.authorization import PrivateAuthorizationRequired
from server.companion.extraction import context_for
from server.companion.jobs import MemoryWorker
from server.companion.memory import MemoryError, MemoryStore
from server.companion.memory_artifacts import PROFILE_KEYS, YoumiMemoryArtifacts
from server.companion.memory_reflection import validate_proposal
from server.companion.memory_runtime import YoumiMemoryRuntime
from server.companion.schema import check_memory_rollback, migrate
from server.companion.semantic import SemanticMemoryIndex
from server.tests import test_companion_jobs as fixture
from server.tests.companion_schema_fixtures import remove_v12


class ArtifactTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.JobTests.setUp
    tearDown = fixture.JobTests.tearDown
    event = fixture.JobTests.event
    row = fixture.JobTests.row

    async def seed(self, marker='seed', text='我喜欢蓝色', scope=None, **fields):
        self.output = {'memories': [dict(category='preference', content=text, source_quote=text,
            source_index=0, kind='explicit', subject='user', value='durable', **fields)]}
        admitted = await self.worker.submit(self.event(marker, scope, text=text))
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'committed')
        owner = (scope or self.scope).owner
        return dict(self.store.db.execute('SELECT * FROM memories WHERE owner=? AND content=?', (owner, text)).fetchone())

    def action(self, *facts, action='note', key='colors', title='蓝色'):
        return dict(action=action, key=key, title=title, evidence=[dict(id=f['id'], quote=f['content']) for f in facts])

    async def apply(self, *actions, marker='derive'):
        self.output = dict(reflection_version=1, memory_actions=list(actions))
        admitted = await self.worker.submit(self.event(marker, text='今天继续聊聊'))
        await self.worker.run_once()
        return self.row(admitted)

    async def items(self, scope=None):
        return await YoumiMemoryRuntime(SemanticMemoryIndex(self.store, self.encoder)).list_artifacts(scope or self.scope, self.policy)

    async def test_profiles_notes_and_maintenance_are_separate_evidence_views(self):
        first = await self.seed()
        second = await self.seed('green', '我喜欢绿色')
        before = [tuple(r) for r in self.store.db.execute('SELECT * FROM memories')]
        actions = [self.action(first, action='profile', key=key) for key in sorted(PROFILE_KEYS)]
        actions += [self.action(first, second), self.action(first, action='propose_retirement', key='old'),
            self.action(first, second, action='propose_merge', key='pair')]
        self.assertEqual((await self.apply(*actions))['state'], 'committed')
        items = await self.items()
        self.assertEqual(len(items), 8)
        self.assertEqual({i['key'] for i in items if i['kind'] == 'profile'}, PROFILE_KEYS)
        note = next(i for i in items if i['kind'] == 'note')
        self.assertEqual(note['body'], first['content'] + '\n' + second['content'])
        self.assertEqual([p['id'] for p in note['evidence']], [first['id'], second['id']])
        self.assertTrue(all(i['preview_only'] for i in items if i['kind'] in {'merge', 'retirement'}))
        self.assertEqual([tuple(r) for r in self.store.db.execute('SELECT * FROM memories')], before)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_replacements').fetchone()[0], 0)

    async def test_updates_keep_identity_and_increment_version(self):
        first = await self.seed()
        second = await self.seed('green', '我喜欢绿色')
        await self.apply(self.action(first))
        initial = (await self.items())[0]
        self.now[0] += 1
        await self.apply(self.action(second, title='绿色'), marker='update')
        updated = (await self.items())[0]
        self.assertEqual(updated['id'], initial['id'])
        self.assertEqual(updated['version'], 2)
        self.assertEqual(updated['body'], second['content'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_artifact_evidence').fetchone()[0], 1)

    async def test_retry_and_reopen_do_not_duplicate_a_view(self):
        fact = await self.seed()
        job = await self.apply(self.action(fact))
        initial = await self.items()
        self.assertEqual((await self.worker.submit(self.event('derive', text='今天继续聊聊')))['status'], 'duplicate')
        self.assertFalse(await self.worker.run_once())
        self.store.close()
        self.store = MemoryStore(self.path, clock=lambda: self.now[0])
        self.assertEqual(await self.items(), initial)
        self.assertEqual(self.store.db.execute('SELECT state FROM mia_jobs WHERE id=?', (job['id'],)).fetchone()[0], 'committed')

    async def test_schema_aware_legacy_recall_preserves_new_views_and_latest_emotion(self):
        fact = await self.seed()
        await self.apply(self.action(fact))
        state = emotional_system.status(self.store, self.scope.owner)
        emotional_system.manage(self.store, self.scope.owner, dict(action='configure', version=0,
            enabled=True, dimensions=state['dimensions']))
        state = emotional_system.status(self.store, self.scope.owner)
        emotional_system.manage(self.store, self.scope.owner, dict(action='adjust', version=state['version'], values={'joy': 42}))
        items = await self.items()
        self.assertTrue(check_memory_rollback(self.store.db)['compatible_build_required'])
        self.store.close()
        self.store = MemoryStore(self.path, clock=lambda: self.now[0])
        index = SemanticMemoryIndex(self.store, self.encoder)
        await index.rebuild_batch(self.scope, self.policy)
        recalled = await index.retrieve(self.scope, self.policy, '我喜欢蓝色吗')
        self.assertIn(fact['id'], [item['id'] for item in recalled['results']])
        self.assertEqual(await self.items(), items)
        self.assertEqual(emotional_system.status(self.store, self.scope.owner)['values']['joy'], 42)

    async def test_devices_share_views_but_other_agents_cannot_read_or_delete(self):
        fact = await self.seed()
        await self.apply(self.action(fact))
        initial = await self.items()
        phone = replace(self.scope, device_id='phone', client_id='ios')
        # The fixture authorizer recognizes its exact verified session.
        self.policy.scopes[phone.owner] = phone
        self.assertEqual(await self.items(phone), initial)
        self.assertEqual(await self.items(self.peer), [])
        with self.assertRaises(MemoryError):
            await YoumiMemoryArtifacts(self.store).remove(self.peer, self.policy, initial[0]['id'], 1)
        self.policy.scopes[self.scope.owner] = self.scope
        self.assertEqual(await self.items(), initial)

    async def test_foreign_pending_missing_and_partial_quotes_rejected(self):
        own = await self.seed()
        foreign = await self.seed('foreign', scope=self.peer)
        pending = await self.seed('pending', text='我喜欢红色', sensitive=True)
        context = context_for(self.store, self.scope.owner)
        for action in [self.action(foreign), self.action(pending, title='红色'),
                self.action(dict(own, content='喜欢蓝色')), self.action(dict(own, id='missing'))]:
            with self.subTest(action=action), self.assertRaises(MemoryError):
                validate_proposal(dict(reflection_version=1, memory_actions=[action]), ['今天继续聊聊'], context)
        self.assertEqual(await self.items(), [])

    async def test_titles_keys_controls_and_counts_are_validated(self):
        fact = await self.seed()
        base = self.action(fact)
        bad = [dict(base, key='../file'), dict(base, key=''), dict(base, title='用户是律师'),
            dict(base, owner=self.peer.owner), dict(base, version=0), dict(base, body='无来源的推测'),
            dict(base, evidence=[]), dict(base, evidence=base['evidence'] * 2),
            dict(base, action='profile', key='temperature'), dict(base, action='propose_merge'),
            self.action(fact, fact, action='propose_retirement')]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(MemoryError):
                validate_proposal(dict(reflection_version=1, memory_actions=[value]), ['你好'], context_for(self.store, self.scope.owner))
        with self.assertRaises(MemoryError):
            validate_proposal(dict(reflection_version=1, memory_actions=[base, base]), ['你好'], context_for(self.store, self.scope.owner))

    async def test_fact_edit_delete_confirmation_source_removal_and_merge_invalidate_views(self):
        for operation in ('edit', 'delete', 'pending', 'source', 'merge'):
            fact = await self.seed('seed-' + operation, text='我喜欢蓝色' + operation)
            other = await self.seed('other-' + operation, text='我喜欢绿色' + operation)
            await self.apply(self.action(fact, other, key=operation), marker='derive-' + operation)
            self.assertEqual(len(await self.items()), 1)
            if operation == 'edit':
                self.store.edit_memory(self.scope.owner, fact['id'], '我喜欢黄色')
            elif operation == 'delete':
                self.store.delete_memory(self.scope.owner, fact['id'])
            elif operation == 'pending':
                self.store.edit_memory(self.scope.owner, fact['id'], fact['content'], confirmed=False)
            elif operation == 'source':
                with self.store.db:
                    self.store.db.execute('DELETE FROM sources WHERE memory_id=?', (fact['id'],))
            else:
                from server.companion.memory_state import YoumiMemoryState
                versions = [list(self.store.db.execute('SELECT content_version,evidence_version FROM mia_memory_meta WHERE memory_id=?',
                    (f['id'],)).fetchone()) for f in (fact, other)]
                await YoumiMemoryState(self.store).merge(self.scope, self.policy, dict(operation_id=operation,
                    retired_id=fact['id'], survivor_id=other['id'], revision=self.store.snapshot(self.scope.owner)['revision'],
                    versions=dict(zip(('retired', 'survivor'), versions))))
            self.assertEqual(await self.items(), [])
            self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_artifact_evidence').fetchone()[0], 0)
            self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    async def test_new_corroborating_source_conservatively_invalidates_view(self):
        fact = await self.seed()
        await self.apply(self.action(fact))
        # Use another session; a repeated identical source in one session is not new evidence.
        self.output = {'memories': [dict(category='preference', content=fact['content'], source_quote=fact['content'],
            source_index=0, kind='explicit', subject='user', value='durable')]}
        admitted = await self.worker.submit(replace(self.event('corroborate'), session_id='another-session'))
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'committed')
        self.assertEqual(await self.items(), [])

    async def test_clear_removes_titles_and_links_while_other_owner_survives(self):
        fact = await self.seed()
        await self.apply(self.action(fact))
        foreign = await self.seed('peer', scope=self.peer)
        self.output = dict(reflection_version=1, memory_actions=[self.action(foreign)])
        admitted = await self.worker.submit(self.event('peer-artifact', self.peer, text='今天继续聊聊'))
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'committed')
        foreign_items = await self.items(self.peer)
        before = dict(self.store.db.execute('SELECT * FROM memories WHERE id=?', (foreign['id'],)).fetchone())
        self.store.clear(self.scope.owner)
        self.assertEqual(await self.items(), [])
        self.assertEqual(await self.items(self.peer), foreign_items)
        self.assertEqual(len(foreign_items), 1)
        self.assertEqual(dict(self.store.db.execute('SELECT * FROM memories WHERE id=?', (foreign['id'],)).fetchone()), before)

    async def test_delete_requires_manage_and_current_version_and_preserves_facts(self):
        fact = await self.seed()
        await self.apply(self.action(fact))
        item = (await self.items())[0]
        service = YoumiMemoryArtifacts(self.store)
        with self.assertRaises(MemoryError):
            await service.remove(self.scope, self.policy, item['id'], 2)
        limited = replace(self.scope, permissions=frozenset({'memory:read', 'memory:write'}))
        self.policy.scopes[limited.owner] = limited
        with self.assertRaises(PrivateAuthorizationRequired):
            await service.remove(limited, self.policy, item['id'], 1)
        self.policy.scopes[self.scope.owner] = self.scope
        await service.remove(self.scope, self.policy, item['id'], 1)
        self.assertEqual(await self.items(), [])
        self.assertEqual(self.store.db.execute('SELECT content FROM memories WHERE id=?', (fact['id'],)).fetchone()[0], fact['content'])

    async def test_disabled_recall_hides_views_without_erasing_them(self):
        fact = await self.seed()
        await self.apply(self.action(fact))
        initial = await self.items()
        features.update(self.store, self.scope.owner, dict(memory_recall=False))
        self.assertEqual(await self.items(), [])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_artifacts').fetchone()[0], 1)
        features.update(self.store, self.scope.owner, dict(memory_recall=True))
        self.assertEqual(await self.items(), initial)

    async def test_read_checks_revocation_and_concurrent_evidence_change_after_snapshot(self):
        fact = await self.seed()
        await self.apply(self.action(fact))
        require = self.policy.require
        calls = []
        async def change(scope, permission):
            calls.append(permission)
            if len(calls) == 2:
                self.store.edit_memory(scope.owner, fact['id'], '我喜欢黄色')
            await require(scope, permission)
        self.policy.require = change
        with self.assertRaisesRegex(MemoryError, 'changed during read'):
            await self.items()
        self.policy.active = False
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.items()

    async def test_revocation_after_model_cancels_artifact_job(self):
        fact = await self.seed()
        async def resolve(scope):
            def extract(sources, context):
                self.policy.active = False
                return dict(reflection_version=1, memory_actions=[self.action(fact)])
            return extract
        self.worker.resolve = resolve
        row = await self.apply(self.action(fact))
        self.assertEqual(row['state'], 'cancelled')
        self.policy.active = True
        self.assertEqual(await self.items(), [])

    async def test_atomic_failure_rolls_back_fact_and_view_then_restart_reuses_result(self):
        fact = await self.seed()
        record = dict(category='preference', content='我喜欢画画', source_quote='我喜欢画画',
            source_index=0, kind='explicit', subject='user', value='durable')
        self.output = dict(reflection_version=1, memory_actions=[dict(action='remember', memory=record), self.action(fact)])
        admitted = await self.worker.submit(self.event('mixed', text=record['content']))
        calls = len(self.calls)
        self.store.db.set_authorizer(lambda action, table, *args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_INSERT and table == 'youmi_memory_artifact_evidence' else sqlite3.SQLITE_OK)
        try:
            await self.worker.run_once()
        finally:
            self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(self.row(admitted)['state'], 'retry_wait')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0], 1)
        self.assertEqual(await self.items(), [])
        self.store.close()
        self.store = MemoryStore(self.path, clock=lambda: self.now[0])
        self.jobs.store = self.store
        self.worker = MemoryWorker(self.jobs, self.policy, self.resolve, encoder=self.encoder)
        self.now[0] += 2
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'committed')
        self.assertEqual(len(await self.items()), 1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0], 2)
        self.assertEqual(len(self.calls), calls + 1)

    async def test_stale_document_and_evidence_versions_cannot_overwrite_newer_data(self):
        fact = await self.seed()
        await self.apply(self.action(fact))
        raw = dict(reflection_version=1, memory_actions=[self.action(fact)])
        stale = validate_proposal(raw, ['你好'], context_for(self.store, self.scope.owner))
        await self.apply(self.action(fact, title='喜欢'), marker='updated')
        # A durable task cannot use an old validated per-item CAS even with a fresh owner revision.
        admitted = await self.worker.submit(self.event('stale'))
        self.jobs.update(self.row(admitted), 'validated', result=json.dumps(stale))
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'cancelled')
        self.assertEqual((await self.items())[0]['version'], 2)

    async def test_batch_cannot_revise_its_own_artifact_evidence(self):
        fact = await self.seed()
        record = dict(category='preference', content='我喜欢绿色', source_quote='我喜欢绿色', source_index=0,
            kind='explicit', subject='user', value='durable', reconciliation=dict(target_id=fact['id'],
                kind='correction', old_quote=fact['content'], evidence='更正：我喜欢蓝色；我喜欢绿色'))
        with self.assertRaises(MemoryError):
            validate_proposal(dict(reflection_version=1, memory_actions=[dict(action='revise', memory=record), self.action(fact)]),
                ['更正：我喜欢蓝色；我喜欢绿色'], context_for(self.store, self.scope.owner))

    async def test_source_version_change_after_validation_cancels_without_backfilling(self):
        fact = await self.seed()
        validated = validate_proposal(dict(reflection_version=1, memory_actions=[self.action(fact)]),
            ['今天继续聊聊'], context_for(self.store, self.scope.owner))
        admitted = await self.worker.submit(self.event('stale-source'))
        self.jobs.update(self.row(admitted), 'validated', result=json.dumps(validated))
        # Evidence changes independently of owner revision (e.g. corroboration).
        with self.store.db:
            self.store.db.execute('INSERT INTO sources VALUES (?,?,?,?,?)',
                (fact['id'], 'other-session', 'synthetic-fingerprint', fact['content'], 101))
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'cancelled')
        self.assertEqual(await self.items(), [])

    async def test_capacity_is_bounded_and_existing_item_can_still_be_updated(self):
        fact = await self.seed()
        for start in range(0, 128, 16):
            row = await self.apply(*(self.action(fact, key='note-' + str(i)) for i in range(start, start + 16)), marker=str(start))
            self.assertEqual(row['state'], 'committed')
        self.assertEqual(len(await self.items()), 128)
        row = await self.apply(self.action(fact, key='overflow'), marker='overflow')
        self.assertEqual(row['state'], 'retry_wait')
        self.assertEqual(len(await self.items()), 128)
        row = await self.apply(self.action(fact, key='note-0', title='喜欢'), marker='at-capacity')
        self.assertEqual(row['state'], 'committed')
        self.assertEqual(len(await self.items()), 128)

    async def test_removing_one_link_removes_the_whole_title_and_other_links(self):
        first = await self.seed()
        second = await self.seed('green', '我喜欢绿色')
        await self.apply(self.action(first, second))
        with self.store.db:
            self.store.db.execute('DELETE FROM youmi_memory_artifact_evidence WHERE memory_id=?', (first['id'],))
        self.assertEqual(await self.items(), [])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_artifact_evidence').fetchone()[0], 0)

    async def test_database_rejects_cross_owner_and_wrong_version_links(self):
        fact = await self.seed()
        foreign = await self.seed('foreign', scope=self.peer)
        await self.apply(self.action(fact))
        item = (await self.items())[0]
        for identifier, cv, ev in [(foreign['id'], 1, 2), (fact['id'], 999, 999)]:
            with self.assertRaises(sqlite3.IntegrityError), self.store.db:
                self.store.db.execute('INSERT INTO youmi_memory_artifact_evidence VALUES (?,?,?,?,?)',
                    (item['id'], identifier, cv, ev, 1))

    async def test_all_51_emotions_cognition_and_long_persona_unchanged(self):
        fact = await self.seed()
        persona = '苏晓筱完整合成人设\n' * 600
        character.update(self.store, self.scope.owner, dict(enabled=True, version=0,
            values=dict(character.YOUMI_V1, background=persona)))
        state = emotional_system.status(self.store, self.scope.owner)
        emotional_system.manage(self.store, self.scope.owner, dict(action='configure', version=0,
            enabled=True, dimensions=state['dimensions']))
        from server.companion.memory_state import YoumiMemoryState, cognitive_status
        cognition = cognitive_status(self.store, self.scope.owner)
        await YoumiMemoryState(self.store).cognition(self.scope, self.policy, dict(action='configure', version=0,
            enabled=True, dimensions=cognition['dimensions']))
        tables = ('youmi_affect_system', 'youmi_cognitive_state', 'mia_companion_profile')
        before = {t: [tuple(r) for r in self.store.db.execute('SELECT * FROM ' + t)] for t in tables}
        self.assertEqual(len(emotional_system.status(self.store, self.scope.owner)['values']), 51)
        self.assertEqual((await self.apply(self.action(fact), self.action(fact, action='profile', key='attribute')))['state'], 'committed')
        self.assertEqual({t: [tuple(r) for r in self.store.db.execute('SELECT * FROM ' + t)] for t in tables}, before)


class ArtifactMigrationTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.JobTests.setUp
    tearDown = fixture.JobTests.tearDown

    def v11(self):
        with self.store.db:
            remove_v12(self.store.db)
            self.store.db.execute('DELETE FROM mia_schema_migrations WHERE version>=12')
        namespace = {'__file__': str(Path('server/companion/schema.py').resolve())}
        source = subprocess.check_output(['git', 'show', 'a0df6332:server/companion/schema.py'], text=True)
        exec(compile(source, 'frozen-v11-schema.py', 'exec'), namespace)
        namespace['migrate'](self.store.db)
        return namespace['migrate']

    async def test_v11_upgrade_failure_is_atomic_and_legacy_loader_rejects_v12(self):
        old = self.v11()
        before = list(self.store.db.iterdump())
        self.store.db.set_authorizer(lambda action, name, *args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TABLE and name == 'youmi_memory_artifact_evidence' else sqlite3.SQLITE_OK)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                migrate(self.store.db)
        finally:
            self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(list(self.store.db.iterdump()), before)
        old(self.store.db)
        migrate(self.store.db)
        migrate(self.store.db)
        self.assertEqual(check_memory_rollback(self.store.db)['schema_version'], 14)
        with self.assertRaises(ValueError):
            old(self.store.db)
        self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    async def test_process_exit_during_v12_leaves_v11_reopenable(self):
        old = self.v11()
        code = '''import os,sqlite3,sys
from server.companion.schema import migrate
db=sqlite3.connect(sys.argv[1])
db.execute('PRAGMA foreign_keys=ON')
def interrupt(action,name,*args):
    if action==sqlite3.SQLITE_CREATE_TABLE and name=='youmi_memory_artifact_evidence': os._exit(77)
    return sqlite3.SQLITE_OK
db.set_authorizer(interrupt)
migrate(db)
'''
        result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', code, str(self.path)], capture_output=True)
        self.assertEqual(result.returncode, 77)
        old(self.store.db)
        migrate(self.store.db)
        self.assertEqual(check_memory_rollback(self.store.db)['schema_version'], 14)
