"""V11 completion snapshots, delayed jobs, cognitive isolation and merge CAS."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from server.companion import emotional_system, reply
from server.companion.authorization import PrivateAuthorizationRequired
from server.companion.jobs import JobStore, TurnCompleted
from server.companion.memory import MemoryStore, MemoryError
from server.companion.memory_repository import YoumiMemoryRepository
from server.companion.memory_state import YoumiMemoryState, capture_turn, cognitive_status, prune_turns, merge_in_transaction
from server.companion.schema import migrate, check_memory_rollback
from server.tests import test_companion_jobs as jobs_fixture, test_companion_context as context_fixture
from server.tests import test_companion_reply as reply_fixture
with patch.object(sys, 'path', [str(Path(__file__).parent), *sys.path]):
    from server.tests import test_astrbot_bridge as bridge_fixture
from server.tests.companion_schema_fixtures import remove_v11


class MemoryStateTests(unittest.IsolatedAsyncioTestCase):
    setUp = jobs_fixture.JobTests.setUp
    tearDown = jobs_fixture.JobTests.tearDown
    event = jobs_fixture.JobTests.event
    row = jobs_fixture.JobTests.row

    def enable(self):
        current = emotional_system.status(self.store, self.scope.owner)
        return emotional_system.manage(self.store, self.scope.owner, dict(action='configure',
            version=current['version'], enabled=True, dimensions=current['dimensions']))

    def capture(self, event):
        with self.store.lock, self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            return capture_turn(self.store, event.scope.owner, event.session_id, event.turn_id, event.text)

    async def records(self):
        repository = YoumiMemoryRepository(self.store)
        corpus = await repository.capture(self.scope, self.policy)
        return await repository.get_by_ids(self.scope, self.policy, corpus, [r['id'] for r in corpus.records])

    async def test_delayed_job_uses_completed_turn_state_not_workers_new_emotion(self):
        state = self.enable()
        event = self.event()
        self.assertTrue(self.capture(event))
        admitted = await self.worker.submit(event)
        self.now[0] += 2
        with self.store.lock, self.store.db:
            self.assertTrue(emotional_system.commit(self.store, self.scope.owner, 'another-device', 'newer',
                {'changes': [{'dimension': 'joy', 'delta': 10}]}, state['version']))
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'committed')
        snapshot = (await self.records())[0]['state_snapshot']
        self.assertEqual(snapshot['affect']['values']['joy'], 10)
        self.assertEqual(len(snapshot['affect']['values']), 51)
        self.assertEqual(snapshot['captured_at'], 100)
        self.assertGreater(emotional_system.status(self.store, self.scope.owner)['values']['joy'], 10)
        self.assertEqual((await self.worker.submit(event))['status'], 'duplicate')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_source_state').fetchone()[0], 1)

    async def test_old_job_has_unknown_snapshot_even_if_emotion_is_currently_enabled(self):
        self.enable()
        await self.worker.submit(self.event())
        await self.worker.run_once()
        self.assertIsNone((await self.records())[0]['state_snapshot'])

    async def test_repeated_legacy_source_cannot_backfill_a_new_emotion_into_old_evidence(self):
        await self.worker.submit(self.event('historical'))
        await self.worker.run_once()
        self.enable()
        repeated = self.event('repeated')
        self.capture(repeated)
        await self.worker.submit(repeated)
        await self.worker.run_once()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM sources').fetchone()[0], 1)
        self.assertIsNone((await self.records())[0]['state_snapshot'])

    async def test_disabled_state_does_not_create_snapshot_or_owner(self):
        event = self.event()
        before = list(self.store.db.iterdump())
        self.assertFalse(self.capture(event))
        self.assertEqual(list(self.store.db.iterdump()), before)

    async def test_duplicate_snapshot_is_immutable_and_changed_source_conflicts(self):
        self.enable()
        event = self.event()
        self.assertTrue(self.capture(event))
        before = list(self.store.db.execute('SELECT * FROM youmi_memory_turn_state'))
        self.now[0] += 20
        self.assertFalse(self.capture(event))
        self.assertEqual(list(self.store.db.execute('SELECT * FROM youmi_memory_turn_state')), before)
        with self.assertRaises(MemoryError):
            self.capture(replace(event, text='changed source'))
        with self.assertRaises(ValueError):
            capture_turn(self.store, self.scope.owner, 's', 't', 'no transaction')

    async def test_snapshot_and_emotion_roll_back_together_on_completion_failure(self):
        state = self.enable()
        event = self.event()
        before = list(self.store.db.iterdump())
        with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
            with self.store.lock, self.store.db:
                self.store.db.execute('BEGIN IMMEDIATE')
                emotional_system.commit(self.store, self.scope.owner, event.session_id, event.turn_id,
                    {'changes': [{'dimension': 'joy', 'delta': 10}]}, state['version'])
                capture_turn(self.store, self.scope.owner, event.session_id, event.turn_id, event.text)
                raise RuntimeError('synthetic failure')
        self.assertEqual(list(self.store.db.iterdump()), before)

    async def test_snapshot_link_failure_rolls_back_fact_and_job_commit(self):
        self.enable()
        event = self.event()
        self.capture(event)
        admitted = await self.worker.submit(event)
        self.store.db.set_authorizer(lambda action, name, *args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_INSERT and name == 'youmi_memory_source_state' else sqlite3.SQLITE_OK)
        try:
            await self.worker.run_once()
        finally:
            self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0], 0)
        self.assertEqual(self.row(admitted)['state'], 'retry_wait')
        self.now[0] += 1
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'committed')
        self.assertIsNotNone((await self.records())[0]['state_snapshot'])

    async def test_forgetting_source_removes_link_and_unreferenced_snapshot(self):
        self.enable()
        event = self.event()
        self.capture(event)
        await self.worker.submit(event)
        await self.worker.run_once()
        memory = (await self.records())[0]['id']
        self.store.delete_memory(self.scope.owner, memory)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_source_state').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_turn_state').fetchone()[0], 0)

    async def test_pruning_preserves_linked_evidence_and_clear_preserves_configuration(self):
        self.enable()
        event = self.event()
        self.capture(event)
        await self.worker.submit(event)
        await self.worker.run_once()
        self.capture(self.event('unlinked'))
        service = YoumiMemoryState(self.store)
        cognition = await service.cognition(self.scope, self.policy, {'action': 'status'})
        configured = await service.cognition(self.scope, self.policy, dict(action='configure', version=0,
            enabled=True, dimensions=cognition['dimensions']))
        self.now[0] += 91 * 86400
        with self.store.lock, self.store.db:
            prune_turns(self.store, self.scope.owner)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_turn_state').fetchone()[0], 1)
        self.store.clear(self.scope.owner)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_turn_state').fetchone()[0], 0)
        self.assertEqual(cognitive_status(self.store, self.scope.owner)['dimensions'], configured['dimensions'])

    async def test_cognition_restart_versions_bounds_and_emotional_independence(self):
        self.enable()
        emotion_before = emotional_system.status(self.store, self.scope.owner)
        service = YoumiMemoryState(self.store)
        state = await service.cognition(self.scope, self.policy, {'action': 'status'})
        state['dimensions']['recall']['mode'] = 'dynamic'
        state = await service.cognition(self.scope, self.policy, dict(action='configure', version=0,
            enabled=True, dimensions=state['dimensions']))
        state = await service.cognition(self.scope, self.policy, dict(action='adjust', version=state['version'],
            energy={'recall': 3.5, 'expression': 5}))
        self.assertEqual(state['energy']['recall'], 3.5)
        self.assertEqual(state['energy']['expression'], 0)
        self.assertFalse((await service.cognition(self.peer, self.policy, {'action': 'status'}))['enabled'])
        with self.assertRaises(MemoryError):
            await service.cognition(self.scope, self.policy, dict(action='adjust', version=0, energy={'recall': 1}))
        for value in [float('nan'), 21, True]:
            with self.assertRaises(MemoryError):
                await service.cognition(self.scope, self.policy, dict(action='adjust', version=state['version'], energy={'recall': value}))
        self.assertEqual(emotional_system.status(self.store, self.scope.owner), emotion_before)
        reopened = MemoryStore(self.path, clock=lambda: self.now[0])
        try:
            self.assertEqual(cognitive_status(reopened, self.scope.owner), state)
        finally:
            reopened.close()

    async def test_cross_agent_snapshot_link_is_rejected_by_database(self):
        self.enable()
        event = self.event()
        self.capture(event)
        memory = self.store.apply_extraction(self.peer.owner, event.session_id,
            self.store.snapshot(self.peer.owner)['revision'], [event.text],
            {'memories': [{'category': 'fact', 'content': event.text, 'source_index': 0}]}, 'foreign')['memories'][0]
        fingerprint = self.store.db.execute('SELECT fingerprint FROM sources WHERE memory_id=?', (memory,)).fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError), self.store.db:
            self.store.db.execute('INSERT INTO youmi_memory_source_state VALUES (?,?,?,?,?,?)',
                (memory, event.session_id, fingerprint, self.scope.owner, event.turn_id, 1))
        row = self.store.db.execute('SELECT * FROM youmi_memory_turn_state').fetchone()
        self.assertNotIn(event.text, str(tuple(row)))

    async def test_cognitive_snapshot_is_not_replaced_by_later_configuration(self):
        service = YoumiMemoryState(self.store)
        state = await service.cognition(self.scope, self.policy, {'action': 'status'})
        state['dimensions']['recall']['mode'] = 'dynamic'
        state = await service.cognition(self.scope, self.policy, dict(action='configure', version=0,
            enabled=True, dimensions=state['dimensions']))
        state = await service.cognition(self.scope, self.policy, dict(action='adjust', version=state['version'], energy={'recall': 3}))
        event = self.event()
        self.capture(event)
        await service.cognition(self.scope, self.policy, dict(action='adjust', version=state['version'], energy={'recall': 8}))
        await self.worker.submit(event)
        await self.worker.run_once()
        snapshot = (await self.records())[0]['state_snapshot']
        self.assertEqual(snapshot['cognition']['energy']['recall'], 3)
        self.assertIsNone(snapshot['affect'])

    async def test_rollback_gate_requires_all_jobs_and_bridge_turns_to_be_drained(self):
        admitted = await self.worker.submit(self.event())
        with self.assertRaisesRegex(ValueError, 'terminal jobs'):
            check_memory_rollback(self.store.db)
        self.jobs.update(admitted, 'cancelled')
        from server.companion.astrbot_turns import TurnLedger
        TurnLedger(self.store, None, None, None).create('prepared', {'attempt_id': 'ci'}, self.scope,
            {'session': 'session', 'suppressed': False})
        with self.assertRaisesRegex(ValueError, 'drained turns'):
            check_memory_rollback(self.store.db)
        with self.store.db:
            self.store.db.execute("UPDATE mia_astrbot_turns SET status='failed'")
        before = list(self.store.db.iterdump())
        self.assertTrue(check_memory_rollback(self.store.db)['ledger_preserved'])
        self.assertEqual(list(self.store.db.iterdump()), before)


class MergeTests(unittest.IsolatedAsyncioTestCase):
    setUp = context_fixture.ContextBuilderTests.setUp
    tearDown = context_fixture.ContextBuilderTests.tearDown
    fact = context_fixture.ContextBuilderTests.fact

    def command(self, retired, survivor):
        versions = {key: list(self.store.db.execute('SELECT content_version,evidence_version '
            'FROM mia_memory_meta WHERE memory_id=?', (value,)).fetchone()) for key, value in
            [('retired', retired), ('survivor', survivor)]}
        return dict(operation_id='merge-one', retired_id=retired, survivor_id=survivor,
                    versions=versions, revision=self.store.snapshot(self.scope.owner)['revision'])

    async def test_merge_keeps_ids_and_original_evidence_and_is_idempotent(self):
        retired, survivor = self.fact('喜欢蓝色'), self.fact('偏爱蓝色')
        await self.index.rebuild_batch(self.scope, self.auth)
        command = self.command(retired, survivor)
        sources = list(self.store.db.execute('SELECT * FROM sources ORDER BY memory_id'))
        service = YoumiMemoryState(self.store)
        self.assertFalse((await service.merge(self.scope, self.auth, command))['duplicate'])
        self.assertTrue((await service.merge(self.scope, self.auth, command))['duplicate'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0], 2)
        self.assertEqual(list(self.store.db.execute('SELECT * FROM sources ORDER BY memory_id')), sources)
        result = await self.index.retrieve(self.scope, self.auth, '喜欢蓝色')
        self.assertEqual([r['id'] for r in result['results']], [survivor])
        self.assertEqual(self.store.retrieve(self.scope.owner, '蓝色')[0]['id'], survivor)
        with self.assertRaises(MemoryError):
            self.store.edit_memory(self.scope.owner, retired, '重新激活旧记录', confirmed=True)
        repeated = self.store.apply_extraction(self.scope.owner, 'repeated-session',
            self.store.snapshot(self.scope.owner)['revision'], ['喜欢蓝色'],
            {'memories': [{'category': 'fact', 'content': '喜欢蓝色', 'source_index': 0}]}, 'repeated')
        self.assertEqual(repeated['memories'], [])
        self.assertEqual(self.store.db.execute('SELECT state FROM mia_memory_meta WHERE memory_id=?',
                                             (retired,)).fetchone()[0], 'superseded')
        with self.assertRaises(MemoryError):
            await service.merge(self.scope, self.auth, dict(command, operation_id='merge-one', survivor_id=retired))

    async def test_merge_rejects_foreign_targets_stale_versions_and_revocation(self):
        own = self.fact('喜欢蓝色')
        other = self.fact('偏爱蓝色', owner='user:1:agent:b')
        command = self.command(own, other)
        before = list(self.store.db.iterdump())
        service = YoumiMemoryState(self.store)
        with self.assertRaises(MemoryError):
            await service.merge(self.scope, self.auth, command)
        self.assertEqual(list(self.store.db.iterdump()), before)
        second = self.fact('偏爱蓝色')
        command = self.command(own, second)
        command['versions']['retired'][1] += 1
        with self.assertRaises(MemoryError):
            await service.merge(self.scope, self.auth, command)
        self.auth.active = False
        with self.assertRaises(PrivateAuthorizationRequired):
            await service.merge(self.scope, self.auth, command)

    async def test_merge_failure_rolls_back_retirement_sources_and_derived_indexes(self):
        retired, survivor = self.fact('喜欢蓝色'), self.fact('偏爱蓝色')
        command = self.command(retired, survivor)
        before = list(self.store.db.iterdump())
        self.store.db.set_authorizer(lambda action, name, *args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_INSERT and name == 'youmi_memory_replacements' else sqlite3.SQLITE_OK)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                await YoumiMemoryState(self.store).merge(self.scope, self.auth, command)
        finally:
            self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(list(self.store.db.iterdump()), before)

    async def test_merge_primitive_joins_caller_transaction_and_preserves_its_job(self):
        retired, survivor = self.fact('喜欢蓝色'), self.fact('偏爱蓝色')
        command = self.command(retired, survivor)
        job = JobStore(self.store).submit(TurnCompleted(self.scope, 'session', 'turn', 'message',
            '我喜欢蓝色', command['revision']))
        before = list(self.store.db.iterdump())
        with self.assertRaisesRegex(RuntimeError, 'caller rollback'):
            with self.store.lock, self.store.db:
                self.store.db.execute('BEGIN IMMEDIATE')
                merge_in_transaction(self.store, self.scope.owner, command, job_id=job['id'])
                self.assertEqual(self.store.db.execute('SELECT state FROM mia_jobs WHERE id=?', (job['id'],)).fetchone()[0], 'pending')
                raise RuntimeError('caller rollback')
        self.assertEqual(list(self.store.db.iterdump()), before)


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    setUp = context_fixture.ContextBuilderTests.setUp
    tearDown = context_fixture.ContextBuilderTests.tearDown
    fact = context_fixture.ContextBuilderTests.fact

    def make_v10(self):
        self.fact('喜欢蓝色')
        from server.companion import character
        character.update(self.store, self.scope.owner, dict(action='set_character', enabled=True, version=0,
            values=dict(character.YOUMI_V1, background='完整保留的合成人设\n' * 600)))
        state = emotional_system.status(self.store, self.scope.owner)
        emotional_system.manage(self.store, self.scope.owner, dict(action='configure', version=0,
            enabled=True, dimensions=state['dimensions']))
        with self.store.db:
            remove_v11(self.store.db)
            self.store.db.execute('DELETE FROM mia_schema_migrations WHERE version>=11')
        old = {'__file__': str(Path('server/companion/schema.py').resolve())}
        source = subprocess.check_output(['git', 'show', 'dc4a996f:server/companion/schema.py'], text=True)
        exec(compile(source, 'frozen-v10-schema.py', 'exec'), old)
        old['migrate'](self.store.db)
        return old['migrate']

    async def test_v10_upgrade_failure_is_atomic_and_preserves_original_rows(self):
        old_loader = self.make_v10()
        before = list(self.store.db.iterdump())
        self.store.db.set_authorizer(lambda action, name, *args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TABLE and name == 'youmi_cognitive_state' else sqlite3.SQLITE_OK)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                migrate(self.store.db)
        finally:
            self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(list(self.store.db.iterdump()), before)
        emotion = emotional_system.status(self.store, self.scope.owner)
        profile = self.store.db.execute('SELECT * FROM mia_companion_profile').fetchone()
        migrate(self.store.db)
        migrate(self.store.db)
        self.assertEqual(emotional_system.status(self.store, self.scope.owner), emotion)
        self.assertEqual(tuple(self.store.db.execute('SELECT * FROM mia_companion_profile').fetchone()), tuple(profile))
        self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(), [])
        with self.assertRaisesRegex(ValueError, 'version or checksum'):
            old_loader(self.store.db)
        self.assertTrue(check_memory_rollback(self.store.db)['compatible_build_required'])

    async def test_process_exit_during_v11_ddl_leaves_v10_reopenable(self):
        old_loader = self.make_v10()
        code = '''import os,sqlite3,sys
from server.companion.schema import migrate
db=sqlite3.connect(sys.argv[1])
db.execute('PRAGMA foreign_keys=ON')
def interrupt(action,name,*args):
    if action==sqlite3.SQLITE_CREATE_TABLE and name=='youmi_cognitive_state': os._exit(77)
    return sqlite3.SQLITE_OK
db.set_authorizer(interrupt)
migrate(db)
'''
        result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', code,
            str(Path(self.temp.name) / 'memory.sqlite3')], capture_output=True)
        self.assertEqual(result.returncode, 77)
        old_loader(self.store.db)
        migrate(self.store.db)
        self.assertEqual(self.store.db.execute('SELECT MAX(version) FROM mia_schema_migrations').fetchone()[0], 18)

    async def test_schema_aware_legacy_engine_keeps_post_upgrade_data_and_latest_emotion(self):
        self.store.clock = lambda: 1800000000
        self.make_v10()
        migrate(self.store.db)
        newer = self.fact('升级后新增：我喜欢热茶')
        state = emotional_system.status(self.store, self.scope.owner)
        emotional_system.manage(self.store, self.scope.owner, dict(action='adjust', version=state['version'], values={'joy': 42}))
        self.assertTrue(check_memory_rollback(self.store.db)['compatible_build_required'])
        self.store.close()
        self.store = MemoryStore(Path(self.temp.name) / 'memory.sqlite3', clock=lambda: 1800000000)
        from server.companion.semantic import SemanticMemoryIndex
        index = SemanticMemoryIndex(self.store, self.encoder)
        await index.rebuild_batch(self.scope, self.auth)
        result = await index.retrieve(self.scope, self.auth, '我喜欢热茶吗')
        self.assertIn(newer, [row['id'] for row in result['results']])
        self.assertEqual(emotional_system.status(self.store, self.scope.owner)['values']['joy'], 42)


class NativeCompletionTests(unittest.IsolatedAsyncioTestCase):
    setUp = reply_fixture.ReplyTests.setUp
    tearDown = reply_fixture.ReplyTests.tearDown
    write_profile = reply_fixture.ReplyTests.write_profile

    async def prepare_proposal(self):
        self.conn.dialogue.dialogue = [reply_fixture.dto('user', '谢谢你', marker='user-1')]
        state = emotional_system.status(self.store, self.scope.owner)
        state = emotional_system.manage(self.store, self.scope.owner, dict(action='configure', version=0,
            enabled=True, dimensions=state['dimensions']))
        turn = await reply.prepare(self.conn, [], 0)
        proposal = dict(changes=[dict(dimension='joy', delta=10)], system_version=state['version'])
        bundle = await turn.builder.with_affect(turn.bundle, self.auth, proposal)
        turn = replace(turn, bundle=bundle)
        self.conn._youmi_context_turn = turn
        self.conn._youmi_affect_pending = (turn, proposal)
        return TurnCompleted(self.scope, self.conn.session_id, self.conn.sentence_id,
            'message', '谢谢你', turn.bundle.snapshot[0])

    async def test_affect_and_snapshot_commit_atomically_once(self):
        event = await self.prepare_proposal()
        self.assertTrue(await reply.finish_affect(self.conn, event))
        self.assertFalse(await reply.finish_memory_state(self.conn, event))
        value = json.loads(self.store.db.execute('SELECT affect_json FROM youmi_memory_turn_state').fetchone()[0])
        self.assertEqual(value['values']['joy'], 20)
        self.assertFalse(await reply.finish_affect(self.conn, event))

    async def test_snapshot_storage_failure_does_not_block_existing_affect(self):
        event = await self.prepare_proposal()
        self.store.db.set_authorizer(lambda action, name, *args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_INSERT and name == 'youmi_memory_turn_state' else sqlite3.SQLITE_OK)
        try:
            self.assertTrue(await reply.finish_affect(self.conn, event))
        finally:
            self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(emotional_system.status(self.store, self.scope.owner)['values']['joy'], 20)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_affect_system_turns').fetchone()[0], 1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_turn_state').fetchone()[0], 0)
        self.assertFalse(await reply.finish_memory_state(self.conn, event))

    async def test_no_appraisal_completion_captures_actual_state_and_failed_preparation_does_not(self):
        state = emotional_system.status(self.store, self.scope.owner)
        emotional_system.manage(self.store, self.scope.owner, dict(action='configure', version=0,
            enabled=True, dimensions=state['dimensions']))
        turn = await reply.prepare(self.conn, [], 0)
        event = TurnCompleted(self.scope, self.conn.session_id, self.conn.sentence_id,
            'message', '我喜欢什么？', turn.bundle.snapshot[0])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_turn_state').fetchone()[0], 0)
        self.assertTrue(await reply.finish_memory_state(self.conn, event))
        self.assertFalse(await reply.finish_memory_state(self.conn, event))
        value = json.loads(self.store.db.execute('SELECT affect_json FROM youmi_memory_turn_state').fetchone()[0])
        self.assertEqual(len(value['values']), 51)
        self.auth.active = False
        with self.assertRaises(PrivateAuthorizationRequired):
            await reply.finish_memory_state(self.conn, replace(event, turn_id='foreign'))


class BridgeCompletionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = bridge_fixture.BridgeHTTPTests.asyncSetUp
    asyncTearDown = bridge_fixture.BridgeHTTPTests.asyncTearDown
    post = bridge_fixture.BridgeHTTPTests.post
    prepare = bridge_fixture.BridgeHTTPTests.prepare
    completion = bridge_fixture.BridgeHTTPTests.completion
    claim = bridge_fixture.BridgeHTTPTests.claim

    def enable(self):
        state = emotional_system.status(self.store, self.scope.owner)
        return emotional_system.manage(self.store, self.scope.owner, dict(action='configure', version=0,
            enabled=True, dimensions=state['dimensions']))

    async def test_successful_qq_completion_captures_state_once_but_prepare_does_not(self):
        self.enable()
        prepared = await self.prepare()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_turn_state').fetchone()[0], 0)
        completion = self.completion(prepared)
        await self.claim(completion)
        await self.post('complete', completion)
        await self.post('complete', completion)
        row = self.store.db.execute('SELECT * FROM youmi_memory_turn_state').fetchone()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_turn_state').fetchone()[0], 1)
        self.assertEqual(row['owner'], self.scope.owner)
        self.assertEqual(len(json.loads(row['affect_json'])['values']), 51)

    async def test_failed_qq_send_does_not_capture_state_or_submit_job(self):
        self.enable()
        prepared = await self.prepare()
        completion = self.completion(prepared, result='failed')
        await self.claim(completion)
        await self.post('complete', completion)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_turn_state').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM mia_jobs').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
