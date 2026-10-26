"""Evidence-scoped linkage with real SQLite, existing projection and completions."""

import asyncio
import copy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from server.companion import affect, emotional_system as emotion, features, memory_emotion as linkage
from server.companion.authorization import PrivateAuthorizationRequired
from server.companion.memory import MemoryError, MemoryStore
from server.companion.memory_emotion_context import YoumiEmotionContextBuilder
from server.companion.memory_runtime import YoumiMemoryRuntime
from server.companion.schema import migrate
from server.tests import test_companion_context as fixture
from server.tests import test_companion_reply as reply_fixture
from server.tests.companion_schema_fixtures import remove_v14
with patch.object(sys, 'path', [str(Path(__file__).parent), *sys.path]):
    from server.tests import test_astrbot_bridge as bridge_fixture


class MemoryEmotionTests(unittest.IsolatedAsyncioTestCase):
    tearDown = fixture.ContextBuilderTests.tearDown
    fact = fixture.ContextBuilderTests.fact

    def setUp(self):
        fixture.ContextBuilderTests.setUp(self)
        self.now = 1800000000
        self.store.clock = lambda: self.now
        self.builder = YoumiEmotionContextBuilder(YoumiMemoryRuntime(self.index))

    def enable_emotion(self, **overrides):
        current = emotion.status(self.store, self.scope.owner)
        for key, values in overrides.items():
            current['dimensions'][key].update(values)
        return emotion.manage(self.store, self.scope.owner, dict(action='configure', version=current['version'],
            enabled=True, dimensions=current['dimensions']))

    async def enable(self, **overrides):
        cfg = linkage.settings(self.store, self.scope.owner)
        return await linkage.YoumiMemoryEmotion(self.store).manage(self.scope, self.auth,
            dict(cfg, action='configure', enabled=True, **overrides))

    def evidence(self, identifier):
        return dict(self.store.db.execute('SELECT m.id,m.content,x.content_version,x.evidence_version FROM memories m '
            'JOIN mia_memory_meta x ON x.memory_id=m.id WHERE m.id=?', (identifier,)).fetchone())

    def raw(self, identifier, *, dimension='joy', delta=40, base=True, **overrides):
        fact = self.evidence(identifier)
        item = dict(dimension=dimension, delta=delta, memory_id=identifier, quote=fact['content'],
            content_version=fact['content_version'], evidence_version=fact['evidence_version'])
        item.update(overrides)
        return dict(version=2, changes=[dict(dimension='joy', delta=2, quote='谢谢你')] if base else [], memory_changes=[item])

    def prepared(self, raw, facts):
        proposal = affect.parse_appraisal(json.dumps(raw, ensure_ascii=False), '谢谢你')
        proposal['system_version'] = emotion.status(self.store, self.scope.owner)['version']
        return linkage.prepare(self.store, self.scope.owner, proposal,
            linkage.evidence_context(self.store, self.scope.owner, facts))

    def commit(self, proposal, marker='turn'):
        with self.store.lock, self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            return affect.commit(self.store, self.scope.owner, 'session', marker, '谢谢你', proposal, 0)

    async def test_defaults_master_switches_permissions_and_config_cas(self):
        cfg = linkage.settings(self.store, self.scope.owner)
        self.assertFalse(cfg['enabled'])
        self.assertTrue(cfg['memory_to_emotion'] and cfg['emotion_to_memory'])
        self.assertEqual((cfg['stimulus_weight'], cfg['ranking_limit'], cfg['cooldown_seconds']), (.25, .1, 86400))
        cfg = await self.enable()
        with self.assertRaises(MemoryError):
            await linkage.YoumiMemoryEmotion(self.store).manage(self.scope, self.auth, dict(cfg, version=0, action='configure'))
        for name, value in [('ranking_limit', .11), ('stimulus_weight', float('nan')), ('cooldown_seconds', True), ('enabled', 1)]:
            with self.assertRaises(MemoryError):
                await linkage.YoumiMemoryEmotion(self.store).manage(self.scope, self.auth, dict(cfg, action='configure', **{name: value}))
        foreign = replace(self.scope, owner='user:1:agent:other', agent_id='other')
        self.assertFalse((await linkage.YoumiMemoryEmotion(self.store).manage(foreign, self.auth, {'action': 'status'}))['enabled'])
        self.auth.active = False
        with self.assertRaises(PrivateAuthorizationRequired):
            await linkage.YoumiMemoryEmotion(self.store).manage(self.scope, self.auth, {'action': 'status'})

    async def test_v2_current_and_memory_quotes_are_not_interchangeable(self):
        identifier = self.fact('我不喜欢蓝色')
        self.enable_emotion()
        await self.enable()
        raw = self.raw(identifier, quote='喜欢蓝色')
        proposal = self.prepared(raw, [self.evidence(identifier)])
        self.assertEqual(proposal['changes'], [{'dimension': 'joy', 'delta': 2}])
        self.assertEqual(proposal['memory_link']['stimuli'], [])
        raw['changes'][0]['quote'] = '我不喜欢蓝色'
        with self.assertRaises(MemoryError):
            affect.parse_appraisal(json.dumps(raw), '谢谢你')

    async def test_combined_delta_uses_sensitivity_and_limit_once(self):
        identifier = self.fact('我喜欢蓝色')
        self.enable_emotion(joy={'sensitivity': 2, 'max_change': 5})
        await self.enable()
        proposal = self.prepared(self.raw(identifier), [self.evidence(identifier)])
        self.assertEqual(proposal['changes'], [{'dimension': 'joy', 'delta': 12}])
        self.assertTrue(self.commit(proposal))
        self.assertEqual(emotion.status(self.store, self.scope.owner)['values']['joy'], 15)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_affect_system_turns').fetchone()[0], 1)
        self.assertNotIn('quote', json.dumps(proposal))
        audit = json.loads(self.store.db.execute("SELECT changes_json FROM youmi_affect_system_history WHERE kind='memory_link'").fetchone()[0])
        self.assertEqual(audit['turn_id'], 'turn')
        self.assertEqual(self.store.db.execute('SELECT kind FROM youmi_affect_system_history WHERE id=?',
            (audit['interaction_history_id'],)).fetchone()[0], 'interaction')

    async def test_all_51_dimensions_remain_available_when_configured_dynamic(self):
        identifier = self.fact('我喜欢蓝色')
        current = emotion.status(self.store, self.scope.owner)
        for item in current['dimensions'].values():
            item.update(mode='dynamic', baseline=30, half_life_hours=0)
        emotion.manage(self.store, self.scope.owner, dict(action='configure', enabled=True, version=0, dimensions=current['dimensions']))
        current = emotion.status(self.store, self.scope.owner)
        emotion.manage(self.store, self.scope.owner, dict(action='reset', version=current['version']))
        await self.enable(cooldown_seconds=0)
        dimensions = list(emotion.CATALOG)
        for offset in range(0, 51, 8):
            raw = self.raw(identifier, base=False, delta=10)
            raw['memory_changes'] = [dict(raw['memory_changes'][0], dimension=key) for key in dimensions[offset:offset + 8]]
            self.assertTrue(self.commit(self.prepared(raw, [self.evidence(identifier)]), str(offset)))
        values = emotion.status(self.store, self.scope.owner)['values']
        self.assertEqual(len(values), 51)
        self.assertTrue(all(value == 32.5 for value in values.values()))

    async def test_fixed_locked_disabled_and_range_are_hard_constraints(self):
        identifier = self.fact('我喜欢蓝色')
        state = self.enable_emotion(sadness={'mode': 'locked', 'lock_until': self.now + 100}, anger={'enabled': False}, fear={'maximum': 12})
        await self.enable()
        raw = self.raw(identifier, base=False)
        raw['memory_changes'] = [dict(raw['memory_changes'][0], dimension=key) for key in ['warmth', 'sadness', 'anger', 'fear']]
        self.commit(self.prepared(raw, [self.evidence(identifier)]))
        after = emotion.status(self.store, self.scope.owner)
        self.assertEqual(after['dimensions'], state['dimensions'])
        self.assertEqual([after['values'][k] for k in ['warmth', 'sadness', 'anger', 'fear']], [85, 10, 10, 12])

    async def test_cooldown_is_cross_device_persistent_and_preserves_new_current_stimuli(self):
        identifier = self.fact('我喜欢蓝色')
        self.enable_emotion()
        await self.enable()
        self.commit(self.prepared(self.raw(identifier), [self.evidence(identifier)]))
        first = emotion.status(self.store, self.scope.owner)['values']['joy']
        repeated = self.prepared(self.raw(identifier), [self.evidence(identifier)])
        self.commit(repeated, 'another-device')
        self.assertEqual(emotion.status(self.store, self.scope.owner)['values']['joy'], first + 2)
        self.store.close()
        self.store = MemoryStore(Path(self.temp.name) / 'memory.sqlite3', clock=lambda: self.now)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_emotion_cooldown').fetchone()[0], 1)
        self.now += 86400
        self.assertEqual(linkage.evidence_context(self.store, self.scope.owner, [self.evidence(identifier)])['evidence'][0]['id'], identifier)

    async def test_duplicate_stale_and_failed_outer_transaction_do_not_consume_again(self):
        identifier = self.fact('我喜欢蓝色')
        self.enable_emotion()
        await self.enable()
        proposal = self.prepared(self.raw(identifier), [self.evidence(identifier)])
        with self.assertRaisesRegex(RuntimeError, 'failed send'):
            with self.store.lock, self.store.db:
                self.store.db.execute('BEGIN IMMEDIATE')
                affect.commit(self.store, self.scope.owner, 's', 'failed', '谢谢你', proposal, 0)
                raise RuntimeError('failed send')
        self.assertEqual(emotion.status(self.store, self.scope.owner)['values']['joy'], 10)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_emotion_cooldown').fetchone()[0], 0)
        self.assertTrue(self.commit(proposal))
        self.assertFalse(self.commit(proposal))
        self.assertFalse(self.commit(proposal, 'stale-other-device'))

    async def test_foreign_unselected_changed_and_pending_memory_cannot_stimulate(self):
        own = self.fact('我喜欢蓝色')
        foreign = self.fact('我喜欢绿色', owner='user:2:agent:b')
        self.enable_emotion()
        await self.enable(cooldown_seconds=0)
        for index, identifier in enumerate((foreign, own)):
            proposal = self.prepared(self.raw(identifier), [])
            self.commit(proposal, str(index))
        pending = self.prepared(self.raw(own), [self.evidence(own)])
        self.store.edit_memory(self.scope.owner, own, '我喜欢蓝色', confirmed=False)
        self.commit(pending, 'pending')
        self.assertEqual(emotion.status(self.store, self.scope.owner)['values']['joy'], 16)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_emotion_cooldown').fetchone()[0], 0)

    async def test_new_corroboration_does_not_reset_same_content_cooldown(self):
        identifier = self.fact('我喜欢蓝色')
        self.enable_emotion()
        await self.enable()
        self.commit(self.prepared(self.raw(identifier), [self.evidence(identifier)]))
        with self.store.db:
            self.store.db.execute('INSERT INTO sources VALUES (?,?,?,?,?)', (identifier, 'new', 'new', '我喜欢蓝色', self.now))
        self.assertEqual(linkage.evidence_context(self.store, self.scope.owner, [self.evidence(identifier)])['evidence'], [])

    async def test_configuration_and_evidence_changes_at_completion_drop_only_memory_delta(self):
        identifier = self.fact('我喜欢蓝色')
        self.enable_emotion()
        await self.enable(cooldown_seconds=0)
        proposal = self.prepared(self.raw(identifier), [self.evidence(identifier)])
        await self.enable(stimulus_weight=.5, cooldown_seconds=0)
        self.commit(proposal, 'config-changed')
        self.assertEqual(emotion.status(self.store, self.scope.owner)['values']['joy'], 12)
        proposal = self.prepared(self.raw(identifier), [self.evidence(identifier)])
        with self.store.db:
            self.store.db.execute('INSERT INTO sources VALUES (?,?,?,?,?)', (identifier, 'new', 'new', '我喜欢蓝色', self.now))
        self.commit(proposal, 'evidence-changed')
        self.assertEqual(emotion.status(self.store, self.scope.owner)['values']['joy'], 14)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_emotion_cooldown').fetchone()[0], 0)

    async def test_coupling_read_failure_keeps_existing_emotional_context(self):
        self.fact('我喜欢蓝色')
        self.enable_emotion()
        await self.enable()
        await self.index.rebuild_batch(self.scope, self.auth)
        with patch('server.companion.memory_emotion.evidence_context', side_effect=sqlite3.OperationalError('fixture')):
            bundle = await self.builder.build(self.scope, self.auth, 'turn-ci', '我喜欢蓝色，谢谢你', limits=self.limits)
        current = json.loads(bundle.inputs_json)['youmi_affect']
        self.assertEqual(len(current['affect_system']['values']), 51)
        self.assertNotIn('_memory_link_context', current)
        proposal = dict(changes=[dict(dimension='joy', delta=2)], system_version=current['affect_system']['version'])
        self.commit(proposal)
        self.assertEqual(emotion.status(self.store, self.scope.owner)['values']['joy'], 12)

    async def test_cooldown_write_failure_rolls_back_only_memory_effect(self):
        identifier = self.fact('我喜欢蓝色')
        self.enable_emotion()
        await self.enable()
        proposal = self.prepared(self.raw(identifier), [self.evidence(identifier)])
        self.store.db.set_authorizer(lambda action, table, *args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_INSERT and table == 'youmi_memory_emotion_cooldown' else sqlite3.SQLITE_OK)
        try:
            self.assertTrue(self.commit(proposal))
        finally:
            self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(emotion.status(self.store, self.scope.owner)['values']['joy'], 12)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_affect_system_turns').fetchone()[0], 1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_emotion_cooldown').fetchone()[0], 0)

    async def test_master_and_direction_switches_preserve_current_appraisal(self):
        identifier = self.fact('我喜欢蓝色')
        self.enable_emotion()
        await self.enable(memory_to_emotion=False)
        proposal = self.prepared(self.raw(identifier), [self.evidence(identifier)])
        self.commit(proposal)
        self.assertEqual(emotion.status(self.store, self.scope.owner)['values']['joy'], 12)
        features.update(self.store, self.scope.owner, {'memory_recall': False})
        self.assertIsNone(linkage.evidence_context(self.store, self.scope.owner, [self.evidence(identifier)]))

    async def test_v2_budget_and_version_validation(self):
        identifier = self.fact('我喜欢蓝色')
        raw = self.raw(identifier)
        bad = [dict(raw, version=True), dict(raw, version=3), dict(raw, owner=self.scope.owner)]
        for key, value in [('delta', float('nan')), ('content_version', True), ('quote', 'api_key=secret-token')]:
            bad.append(dict(raw, memory_changes=[dict(raw['memory_changes'][0], **{key: value})]))
        bad.append(dict(raw, memory_changes=raw['memory_changes'] * 2))
        dimensions = list(emotion.CATALOG)
        bad.append(dict(raw, changes=[dict(dimension=k, delta=1, quote='谢谢你') for k in dimensions[:8]],
            memory_changes=[dict(raw['memory_changes'][0], dimension=dimensions[8])]))
        for value in bad:
            with self.subTest(value=value), self.assertRaises(MemoryError):
                affect.parse_appraisal(json.dumps(value), '谢谢你')

    async def test_shared_builder_exposes_evidence_only_to_appraisal_and_projects_once(self):
        identifier = self.fact('我喜欢蓝色')
        self.enable_emotion()
        await self.enable()
        await self.index.rebuild_batch(self.scope, self.auth)
        bundle = await self.builder.build(self.scope, self.auth, 'turn-ci', '我喜欢蓝色，谢谢你', limits=self.limits)
        inputs = json.loads(bundle.inputs_json)
        self.assertEqual(inputs['youmi_affect']['_memory_link_context']['evidence'][0]['id'], identifier)
        self.assertNotIn('_memory_link_context', bundle.request_json)
        proposal = affect.parse_appraisal(json.dumps(self.raw(identifier)), '我喜欢蓝色，谢谢你')
        proposal['system_version'] = emotion.status(self.store, self.scope.owner)['version']
        projected = await self.builder.with_affect(bundle, self.auth, proposal)
        self.assertEqual(json.loads(projected.inputs_json)['youmi_affect']['affect_system']['values']['joy'], 22)
        self.assertEqual(emotion.status(self.store, self.scope.owner)['values']['joy'], 10)
        self.assertNotIn('_memory_changes', proposal)
        self.assertNotIn('_memory_link_context', projected.request_json)
        self.auth.active = False
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.builder.with_affect(bundle, self.auth, proposal)

    async def test_appraisal_request_keeps_current_text_separate_and_original_budget(self):
        identifier = self.fact('我喜欢蓝色')
        self.enable_emotion()
        await self.enable()
        current = affect.state(self.store, self.scope.owner)
        current['_memory_link_context'] = linkage.evidence_context(self.store, self.scope.owner, [self.evidence(identifier)])
        from unittest.mock import Mock
        create = Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}'))]))
        provider = SimpleNamespace(client=SimpleNamespace(with_options=lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))),
            model_name='fixture', _apply_thinking_disabled=lambda params: None)
        affect.request_appraisal(provider, '谢谢你', current)
        params = create.call_args.kwargs
        payload = json.loads(params['messages'][1]['content'])
        self.assertEqual(payload['user_text'], '谢谢你')
        self.assertEqual(payload['memory_evidence'][0]['content'], '我喜欢蓝色')
        self.assertEqual(params['max_tokens'], 1024)
        self.assertEqual(create.call_count, 1)

    def ranked(self):
        state = emotion.status(self.store, self.scope.owner)
        history = dict(kind='affect_system', values=state['values'], enabled_dimensions=list(state['values']))
        return [dict(id='unknown', content_version=1, retrieval=dict(final_score=1), state_snapshot=None),
            dict(id='known', content_version=1, retrieval=dict(final_score=.99), state_snapshot=dict(affect=history, content_version=1))]

    async def test_rank_is_bounded_same_candidates_read_only_and_unknown_not_fabricated(self):
        self.enable_emotion()
        await self.enable()
        rows = self.ranked()
        before = list(self.store.db.iterdump())
        ranked = linkage.rerank(self.store, self.scope.owner, rows, '喜欢什么颜色')
        self.assertEqual([r['id'] for r in ranked], ['known', 'unknown'])
        self.assertEqual({r['id'] for r in ranked}, {r['id'] for r in rows})
        self.assertTrue(all(1 <= r['retrieval']['emotion_factor'] <= 1.1 for r in ranked))
        self.assertIsNone(rows[0]['state_snapshot'])
        self.assertEqual(list(self.store.db.iterdump()), before)
        self.assertNotIn('emotion_factor', rows[0]['retrieval'])
        for query in ('不喜欢什么', '更正颜色', '以前喜欢什么', '现在呢', 'not blue'):
            self.assertEqual(linkage.rerank(self.store, self.scope.owner, rows, query), rows)
        stale = copy.deepcopy(rows)
        stale[1]['content_version'] = 2
        self.assertEqual([r['id'] for r in linkage.rerank(self.store, self.scope.owner, stale, '颜色')], ['unknown', 'known'])

    async def test_link_configuration_does_not_modify_persona_cognition_or_51_settings(self):
        from server.companion import character
        from server.companion.memory_state import cognitive_status
        character.update(self.store, self.scope.owner, dict(enabled=True, version=0,
            values=dict(character.YOUMI_V1, background='完整合成人设\n' * 600)))
        before = character.status(self.store, self.scope.owner)
        emotion_before = self.enable_emotion()
        cognition = cognitive_status(self.store, self.scope.owner)
        await self.enable()
        self.assertEqual(character.status(self.store, self.scope.owner), before)
        self.assertEqual(emotion.status(self.store, self.scope.owner), emotion_before)
        self.assertEqual(cognitive_status(self.store, self.scope.owner), cognition)

    async def test_forgetting_cascades_cooldown_and_clear_keeps_configuration(self):
        identifier = self.fact('我喜欢蓝色')
        self.enable_emotion()
        cfg = await self.enable()
        self.commit(self.prepared(self.raw(identifier), [self.evidence(identifier)]))
        self.store.delete_memory(self.scope.owner, identifier)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_emotion_cooldown').fetchone()[0], 0)
        self.store.clear(self.scope.owner)
        self.assertEqual(linkage.settings(self.store, self.scope.owner), cfg)


class LinkMigrationTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.ContextBuilderTests.setUp
    tearDown = fixture.ContextBuilderTests.tearDown

    def v13(self):
        with self.store.db:
            remove_v14(self.store.db)
            self.store.db.execute('DELETE FROM mia_schema_migrations WHERE version>=14')
        namespace = {'__file__': str(Path('server/companion/schema.py').resolve())}
        source = subprocess.check_output(['git', 'show', 'f6aa0180:server/companion/schema.py'], text=True)
        exec(compile(source, 'frozen-v13.py', 'exec'), namespace)
        namespace['migrate'](self.store.db)
        return namespace['migrate']

    async def test_v13_failure_rolls_back_and_old_loader_rejects_v14(self):
        old = self.v13()
        before = list(self.store.db.iterdump())
        self.store.db.set_authorizer(lambda action, name, *args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TABLE and name == 'youmi_memory_emotion_cooldown' else sqlite3.SQLITE_OK)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                migrate(self.store.db)
        finally:
            self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(list(self.store.db.iterdump()), before)
        old(self.store.db)
        migrate(self.store.db)
        migrate(self.store.db)
        with self.assertRaises(ValueError):
            old(self.store.db)
        self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    async def test_process_exit_during_v14_leaves_v13_reopenable(self):
        old = self.v13()
        code = '''import os,sqlite3,sys
from server.companion.schema import migrate
db=sqlite3.connect(sys.argv[1])
db.execute('PRAGMA foreign_keys=ON')
def interrupt(action,name,*args):
    if action==sqlite3.SQLITE_CREATE_TABLE and name=='youmi_memory_emotion_cooldown': os._exit(77)
    return sqlite3.SQLITE_OK
db.set_authorizer(interrupt)
migrate(db)
'''
        result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', code,
            str(Path(self.temp.name) / 'memory.sqlite3')], capture_output=True)
        self.assertEqual(result.returncode, 77)
        old(self.store.db)
        migrate(self.store.db)
        self.assertEqual(self.store.db.execute('SELECT MAX(version) FROM mia_schema_migrations').fetchone()[0], 17)


class NativeLinkCompletionTests(unittest.IsolatedAsyncioTestCase):
    setUp = reply_fixture.ReplyTests.setUp
    tearDown = reply_fixture.ReplyTests.tearDown
    write_profile = reply_fixture.ReplyTests.write_profile

    async def test_actual_reply_and_completion_apply_link_and_capture_snapshot_once(self):
        from server.companion import reply
        from server.companion.jobs import TurnCompleted
        identifier = self.store.apply_extraction(self.scope.owner, 'seed', 0, ['我喜欢蓝色'],
            {'memories': [{'category': 'preference', 'content': '我喜欢蓝色', 'source_index': 0}]}, 'seed')['memories'][0]
        current = emotion.status(self.store, self.scope.owner)
        emotion.manage(self.store, self.scope.owner, dict(action='configure', enabled=True, version=0, dimensions=current['dimensions']))
        await linkage.YoumiMemoryEmotion(self.store).manage(self.scope, self.auth, dict(linkage.DEFAULTS, action='configure', enabled=True))
        self.conn._youmi_context_builder = YoumiEmotionContextBuilder(YoumiMemoryRuntime(self.index))
        await self.index.rebuild_batch(self.scope, self.auth)
        fact = dict(self.store.db.execute('SELECT content_version,evidence_version FROM mia_memory_meta WHERE memory_id=?', (identifier,)).fetchone())
        raw = dict(version=2, changes=[dict(dimension='joy', delta=2, quote='谢谢你')], memory_changes=[
            dict(fact, memory_id=identifier, quote='我喜欢蓝色', dimension='joy', delta=40)])
        self.conn.dialogue.dialogue = [reply_fixture.dto('user', '我喜欢蓝色，谢谢你', marker='actual')]
        self.conn.loop = asyncio.get_running_loop()
        with patch('server.companion.affect.request_appraisal', return_value=json.dumps(raw)) as appraisal:
            shown = await asyncio.to_thread(lambda: list(reply.response(self.conn, None, 0)))
        self.assertTrue(shown)
        self.assertEqual(appraisal.call_count, 1)
        self.assertEqual(emotion.status(self.store, self.scope.owner)['values']['joy'], 10)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_emotion_cooldown').fetchone()[0], 0)
        turn = self.conn._youmi_context_turn
        event = TurnCompleted(self.scope, self.conn.session_id, turn.bundle.turn_id, 'completed',
            '我喜欢蓝色，谢谢你', turn.bundle.snapshot[0])
        self.assertTrue(await reply.finish_affect(self.conn, event))
        self.assertFalse(await reply.finish_affect(self.conn, event))
        self.assertEqual(emotion.status(self.store, self.scope.owner)['values']['joy'], 22)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_emotion_cooldown').fetchone()[0], 1)
        snapshot = json.loads(self.store.db.execute('SELECT affect_json FROM youmi_memory_turn_state').fetchone()[0])
        self.assertEqual(snapshot['values']['joy'], 22)


class QQLinkCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await bridge_fixture.BridgeHTTPTests.asyncSetUp(self)
        # Completion idempotency uses one instant; recovery has separate tests.
        self.store.clock = lambda: 1800000000
    asyncTearDown = bridge_fixture.BridgeHTTPTests.asyncTearDown
    post = bridge_fixture.BridgeHTTPTests.post
    prepare = bridge_fixture.BridgeHTTPTests.prepare
    completion = bridge_fixture.BridgeHTTPTests.completion
    claim = bridge_fixture.BridgeHTTPTests.claim

    async def prepared_link(self):
        from types import ModuleType
        identifier = self.store.apply_extraction(self.scope.owner, 'seed', 0, ['我喜欢蓝色'],
            {'memories': [{'category': 'preference', 'content': '我喜欢蓝色', 'source_index': 0}]}, 'seed')['memories'][0]
        current = emotion.status(self.store, self.scope.owner)
        emotion.manage(self.store, self.scope.owner, dict(action='configure', enabled=True, version=0, dimensions=current['dimensions']))
        await linkage.YoumiMemoryEmotion(self.store).manage(self.scope, self.auth, dict(linkage.DEFAULTS, action='configure', enabled=True))
        self.builder = self.handler.builder = YoumiEmotionContextBuilder(YoumiMemoryRuntime(self.builder.index))
        await self.builder.index._index.rebuild_batch(self.scope, self.auth)
        fact = dict(self.store.db.execute('SELECT content_version,evidence_version FROM mia_memory_meta WHERE memory_id=?', (identifier,)).fetchone())
        core, utils, llm = (ModuleType(name) for name in ('core', 'core.utils', 'core.utils.llm'))
        llm.create_instance = lambda *args: SimpleNamespace(client=SimpleNamespace(close=lambda: None))
        self.auth.models.update(selected_module={'LLM': 'fixture'}, LLM={'fixture': {'type': 'fixture'}})
        raw = dict(version=2, changes=[dict(dimension='joy', delta=2, quote='谢谢你')], memory_changes=[
            dict(fact, memory_id=identifier, quote='我喜欢蓝色', dimension='joy', delta=40)])
        with patch.dict(sys.modules, {'core': core, 'core.utils': utils, 'core.utils.llm': llm}), \
                patch('server.companion.affect.request_appraisal', return_value=json.dumps(raw)):
            prepared = await self.prepare(query_text='我喜欢蓝色，谢谢你')
        self.assertEqual(prepared['affect_status'], 'projected')
        self.assertEqual(emotion.status(self.store, self.scope.owner)['values']['joy'], 10)
        self.assertNotIn('_memory_link_context', json.dumps(prepared))
        return prepared

    async def test_qq_success_and_retry_commit_link_only_once(self):
        prepared = await self.prepared_link()
        body = self.completion(prepared)
        await self.claim(body)
        await self.post('complete', body)
        await self.post('complete', body)
        self.assertEqual(emotion.status(self.store, self.scope.owner)['values']['joy'], 22)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_emotion_cooldown').fetchone()[0], 1)

    async def test_qq_failed_send_never_consumes_cooldown(self):
        prepared = await self.prepared_link()
        body = self.completion(prepared, result='failed')
        await self.claim(body)
        await self.post('complete', body)
        self.assertEqual(emotion.status(self.store, self.scope.owner)['values']['joy'], 10)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_emotion_cooldown').fetchone()[0], 0)
