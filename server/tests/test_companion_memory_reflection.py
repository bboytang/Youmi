"""Reflection actions exercise the existing real SQLite completion/job boundary."""

import copy
import json
import sqlite3
import unittest

from server.companion import character, emotional_system
from server.companion.extraction import context_for, validate_output
from server.companion.jobs import MemoryWorker
from server.companion.memory import MemoryError, MemoryStore
from server.companion.memory_reflection import validate_proposal
from server.companion.memory_state import YoumiMemoryState, cognitive_status
from server.tests import test_companion_jobs as fixture


class ReflectionTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.JobTests.setUp
    tearDown = fixture.JobTests.tearDown
    event = fixture.JobTests.event
    row = fixture.JobTests.row

    def envelope(self, *memories, action='remember'):
        return {'reflection_version': 1, 'memory_actions': [
            {'action': action, 'memory': copy.deepcopy(memory)} for memory in memories]}

    def facts(self):
        return [dict(row) for row in self.store.db.execute('SELECT * FROM memories ORDER BY rowid')]

    async def seed(self, scope=None):
        admitted = await self.worker.submit(self.event('seed', scope))
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'committed')
        return self.facts()[-1]

    def correction(self, target):
        content = '我喜欢绿色'
        source = '更正：' + target['content'] + '；' + content
        record = dict(self.output['memories'][0], content=content, source_quote=content,
            reconciliation=dict(target_id=target['id'], kind='correction',
                old_quote=target['content'], evidence=source))
        return source, record

    async def test_legacy_dict_json_and_fenced_results_stay_identical(self):
        for output in (self.output, json.dumps(self.output), '```json\n' + json.dumps(self.output) + '\n```'):
            self.assertEqual(validate_proposal(output, ['我喜欢蓝色'], []),
                validate_output(output, ['我喜欢蓝色'], []))

    async def test_remember_commits_once_with_original_source_and_vector(self):
        self.output = self.envelope(self.output['memories'][0])
        admitted = await self.worker.submit(self.event())
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'committed')
        before = self.facts()
        self.assertEqual(len(before), 1)
        self.assertEqual(before[0]['content'], '我喜欢蓝色')
        self.assertEqual(self.store.db.execute('SELECT excerpt FROM sources').fetchone()[0], '我喜欢蓝色')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM mia_vectors').fetchone()[0], 1)
        self.assertEqual((await self.worker.submit(self.event()))['status'], 'duplicate')
        self.assertFalse(await self.worker.run_once())
        self.assertEqual(self.facts(), before)
        self.assertEqual(len(self.calls), 1)

    async def test_json_reflection_uses_same_evidence_validator(self):
        raw = self.envelope(self.output['memories'][0])
        for output in (raw, json.dumps(raw), '```json\n' + json.dumps(raw) + '\n```'):
            self.assertEqual(validate_proposal(output, ['我喜欢蓝色'], []),
                validate_output(self.output, ['我喜欢蓝色'], []))

    async def test_explicit_revision_preserves_id_and_replaces_old_sources(self):
        target = await self.seed()
        source, record = self.correction(target)
        self.output = self.envelope(record, action='revise')
        admitted = await self.worker.submit(self.event('correction', text=source))
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'committed')
        self.assertEqual(len(self.facts()), 1)
        self.assertEqual(self.facts()[0]['id'], target['id'])
        self.assertEqual(self.facts()[0]['content'], record['content'])
        self.assertEqual(self.store.db.execute('SELECT excerpt FROM sources').fetchone()[0], record['source_quote'])

    async def test_invalid_envelopes_rejected_before_any_write(self):
        raw = self.envelope(self.output['memories'][0])
        bad = [dict(raw, reflection_version=version) for version in (True, 0, 2, '1', None)]
        bad += [dict(raw, memories=[]), dict(raw, owner=self.peer.owner), dict(raw, soul_state_code='1111'),
            {'memory_actions': []}, {'reflection_version': 1}, dict(raw, memory_actions={})]
        for action in ('merge', 'forget', 'updata', 'configure', None, []):
            bad.append(dict(raw, memory_actions=[dict(raw['memory_actions'][0], action=action)]))
        before = list(self.store.db.iterdump())
        for value in bad:
            with self.subTest(value=value), self.assertRaises(MemoryError):
                validate_proposal(value, ['我喜欢蓝色'], [])
        self.assertEqual(list(self.store.db.iterdump()), before)

    async def test_model_cannot_supply_identity_versions_snapshots_or_configuration(self):
        for key in ('id', 'owner', 'replaces_id', 'content_version', 'state_snapshot', 'affect', 'cognition', 'persona'):
            value = self.envelope(dict(self.output['memories'][0], **{key: 'forged'}))
            with self.subTest(key=key), self.assertRaises(MemoryError):
                validate_proposal(value, ['我喜欢蓝色'], [])

    async def test_action_and_reconciliation_must_agree(self):
        target = await self.seed()
        source, record = self.correction(target)
        for raw in (self.envelope(record), self.envelope(self.output['memories'][0], action='revise'),
                self.envelope(dict(record, reconciliation=dict(record['reconciliation'], target_version=1)), action='revise')):
            with self.assertRaises(MemoryError):
                validate_proposal(raw, [source], context_for(self.store, self.scope.owner))

    async def test_forged_quote_and_old_fact_as_current_source_are_rejected(self):
        target = await self.seed()
        self.output = self.envelope(self.output['memories'][0])
        before = self.facts()
        admitted = await self.worker.submit(self.event('query', text='你还记得我喜欢什么颜色吗'))
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'retry_wait')
        self.assertEqual(self.facts(), before)
        self.assertEqual(self.facts()[0]['id'], target['id'])

    async def test_cross_owner_revision_rejected(self):
        target = await self.seed(self.peer)
        source, record = self.correction(target)
        self.output = self.envelope(record, action='revise')
        before = self.facts()
        admitted = await self.worker.submit(self.event('foreign', text=source))
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'retry_wait')
        self.assertEqual(self.facts(), before)

    async def test_sensitive_and_inferred_actions_remain_pending_without_vectors(self):
        for marker, record in [('sensitive', dict(self.output['memories'][0], sensitive=True)),
                ('inferred', dict(self.output['memories'][0], kind='inferred', anchor='蓝色'))]:
            self.output = self.envelope(record)
            admitted = await self.worker.submit(self.event(marker))
            await self.worker.run_once()
            self.assertEqual(self.row(admitted)['state'], 'committed')
            self.assertEqual(self.facts()[0]['confirmed'], 0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM mia_vectors').fetchone()[0], 0)

    async def test_whole_batch_validated_before_writes_and_not_truncated(self):
        first = self.output['memories'][0]
        invalid = dict(first, source_quote='我喜欢红色', content='我喜欢红色')
        self.output = self.envelope(first, invalid)
        admitted = await self.worker.submit(self.event())
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'retry_wait')
        self.assertEqual(self.facts(), [])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0], 0)
        for length in (0, 16):
            self.assertEqual(len(validate_proposal(self.envelope(*([first] * length)), ['我喜欢蓝色'], [])['memories']), length)
        with self.assertRaises(MemoryError):
            validate_proposal(self.envelope(*([first] * 17)), ['我喜欢蓝色'], [])

    async def test_impression_one_does_not_truncate_completed_turn_actions(self):
        cognition = cognitive_status(self.store, self.scope.owner)
        cognition['dimensions']['impression']['baseline'] = 1
        await YoumiMemoryState(self.store).cognition(self.scope, self.policy, dict(action='configure',
            version=0, enabled=True, dimensions=cognition['dimensions']))
        quotes = ['我喜欢蓝色', '我喜欢徒步', '我习惯看书', '我喜欢画画']
        self.output = self.envelope(*(dict(self.output['memories'][0], content=quote, source_quote=quote)
            for quote in quotes))
        admitted = await self.worker.submit(self.event(text='\n'.join(quotes)))
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'committed')
        self.assertEqual([fact['content'] for fact in self.facts()], quotes)

    async def test_revocation_after_model_prevents_write(self):
        async def resolve(scope):
            def extract(sources, context):
                self.policy.active = False
                return self.envelope(self.output['memories'][0])
            return extract
        self.worker.resolve = resolve
        admitted = await self.worker.submit(self.event())
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'cancelled')
        self.assertEqual(self.facts(), [])

    async def test_empty_reflection_terminates_without_writes(self):
        self.output = self.envelope()
        admitted = await self.worker.submit(self.event())
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'empty')
        self.assertEqual(self.row(admitted)['source_payload'], '{}')
        self.assertEqual(self.facts(), [])

    async def test_invalid_reflection_keeps_existing_two_attempt_limit(self):
        self.output = dict(self.envelope(), reflection_version=2)
        admitted = await self.worker.submit(self.event())
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'retry_wait')
        self.now[0] += 2
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'failed')
        self.assertEqual(self.row(admitted)['attempts'], 2)
        self.assertEqual(self.row(admitted)['source_payload'], '{}')
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(await self.worker.run_once())
        self.assertEqual(self.facts(), [])

    async def test_revision_change_during_reflection_prevents_write(self):
        target = await self.seed()
        source, record = self.correction(target)
        async def resolve(scope):
            def extract(sources, context):
                self.store.edit_memory(scope.owner, target['id'], '我喜欢黄色')
                return self.envelope(record, action='revise')
            return extract
        self.worker.resolve = resolve
        admitted = await self.worker.submit(self.event('stale', text=source))
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'cancelled')
        self.assertEqual(self.facts()[0]['content'], '我喜欢黄色')

    async def test_database_failure_is_atomic_and_restart_reuses_validated_result(self):
        self.output = self.envelope(self.output['memories'][0])
        admitted = await self.worker.submit(self.event())
        def deny_insert(action, table, *args):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and table == 'sources' else sqlite3.SQLITE_OK
        self.store.db.set_authorizer(deny_insert)
        try:
            await self.worker.run_once()
        finally:
            self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(self.facts(), [])
        self.assertEqual(self.row(admitted)['state'], 'retry_wait')
        saved = json.loads(self.row(admitted)['validated_result'])
        self.assertEqual(saved['_schema'], 1)
        self.assertNotIn('memory_actions', saved)
        self.store.close()
        self.store = MemoryStore(self.path, clock=lambda: self.now[0])
        self.jobs.store = self.store
        self.worker = MemoryWorker(self.jobs, self.policy, self.resolve, encoder=self.encoder)
        self.now[0] += 2
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'committed')
        self.assertEqual(len(self.facts()), 1)
        self.assertEqual(len(self.calls), 1)

    async def test_reflection_does_not_change_all_51_emotions_cognition_or_persona(self):
        persona = '姓名：苏晓筱\n称呼看心情：宝、姐妹、某人。\n' + '人设完整原文，不得改写。\n' * 200
        character.update(self.store, self.scope.owner, dict(enabled=True, version=0,
            values=dict(character.YOUMI_V1, background=persona)))
        status = emotional_system.status(self.store, self.scope.owner)
        emotional_system.manage(self.store, self.scope.owner, dict(action='configure',
            version=status['version'], enabled=True, dimensions=status['dimensions']))
        cognition = cognitive_status(self.store, self.scope.owner)
        await YoumiMemoryState(self.store).cognition(self.scope, self.policy, dict(action='configure',
            version=cognition['version'], enabled=True, dimensions=cognition['dimensions']))
        tables = ('youmi_affect_system', 'youmi_cognitive_state', 'mia_companion_profile')
        before = {table: list(self.store.db.execute('SELECT * FROM ' + table)) for table in tables}
        self.assertTrue(all(before.values()))
        emotions = emotional_system.status(self.store, self.scope.owner)
        self.assertEqual(len(emotions['values']), 51)
        self.output = self.envelope(self.output['memories'][0])
        admitted = await self.worker.submit(self.event())
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['state'], 'committed')
        self.assertEqual(emotional_system.status(self.store, self.scope.owner), emotions)
        self.assertEqual(character.status(self.store, self.scope.owner)['values']['background'], persona)
        self.assertEqual({table: list(self.store.db.execute('SELECT * FROM ' + table)) for table in tables}, before)
