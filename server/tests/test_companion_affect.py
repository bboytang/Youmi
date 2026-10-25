"""Persistent Youmi affect mechanisms; synthetic appraisals are not model evidence."""

import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from server.companion import affect, features, reply, youmi_adapter as youmi
from server.companion.authorization import PrivateAuthorizationRequired
from server.companion.context import ContextBuilder, ContextError
from server.companion.memory import MemoryStore
from server.companion.schema import migrate, _schema_checksum
from server.companion.semantic import SemanticMemoryIndex
from server.tests import test_companion_context as fixture
from server.tests import test_companion_reply as reply_fixture


class AffectStateTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.ContextBuilderTests.setUp
    tearDown = fixture.ContextBuilderTests.tearDown
    build = fixture.ContextBuilderTests.build
    data = fixture.ContextBuilderTests.data

    def enable(self):
        features.update(self.store, self.scope.owner, {'companion_emotion': True})

    def commit(self, feeling='hurt', turn='t', intensity=2):
        with self.store.lock, self.store.db:
            return affect.commit(self.store, self.scope.owner, 's', turn, '你真烦',
                {'feeling': feeling, 'intensity': intensity}, affect.state(self.store, self.scope.owner)['version'])

    async def test_default_off_and_no_mood_source_confusion(self):
        state = features.status(self.store, self.scope.owner)
        self.assertFalse(state['features']['companion_emotion'])
        self.assertEqual(state['mia_emotion']['subject'], 'youmi')
        self.assertEqual(state['emotion_tracking']['enabled'], 0)
        self.assertNotIn('youmi_affect', self.data(await self.build()))
        self.assertFalse(self.commit())
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM mia_affect_state').fetchone()[0], 0)

    async def test_inertia_repair_time_recovery_persist_across_restarts_and_bound_devices(self):
        self.enable();now = [1800000000];self.store.clock = lambda: now[0]
        self.assertTrue(self.commit())
        hurt = affect.state(self.store, self.scope.owner)
        self.assertLess(hurt['valence'], 0)
        self.assertEqual(hurt['feeling'], 'hurt')
        self.assertIn('尚未完全平复', (await self.build()).request()['messages'][0]['content'])
        self.commit('steady', 'next')
        residual = affect.state(self.store, self.scope.owner)
        self.assertEqual(residual['feeling'], 'hurt')
        self.assertLess(residual['tension'], hurt['tension'])
        self.commit('repair', 'apology')
        repaired = affect.state(self.store, self.scope.owner)
        self.assertLess(repaired['tension'], residual['tension'])
        self.assertGreater(repaired['tension'], 0)
        path = Path(self.temp.name) / 'memory.sqlite3'
        phone = MemoryStore(path, clock=lambda: now[0])
        try:
            phone_scope = replace(self.scope, device_id='iphone', client_id='ios')
            builder = ContextBuilder(SemanticMemoryIndex(phone, self.encoder))
            bundle = await builder.build(phone_scope, self.auth, 'phone', '你好', limits=self.limits)
            self.assertEqual(self.data(bundle)['youmi_affect'], repaired)
            self.assertNotIn('youmi_affect', self.data(await self.builder.build(
                replace(self.scope, owner='user:2:agent:b', user_id=2, agent_id='b'), self.auth,
                'other', '你好', limits=self.limits)))
            now[0] += 7200
            recovered = affect.state(phone, self.scope.owner)
            self.assertAlmostEqual(recovered['tension'], repaired['tension'] / 2, places=3)
            now[0] += 7 * 86400
            self.assertEqual(affect.state(phone, self.scope.owner)['feeling'], 'steady')
            now[0] -= 8 * 86400
            self.assertLessEqual(affect.state(phone, self.scope.owner)['tension'], repaired['tension'])
        finally:
            phone.close()

    async def test_switch_reset_invalidate_active_reply_and_leave_roles_facts_user_mood_untouched(self):
        self.enable();self.commit()
        old = await self.build()
        features.update(self.store, self.scope.owner, {'companion_emotion': False})
        with self.assertRaises(ContextError):await self.builder.validate(old, self.auth)
        self.assertNotIn('youmi_affect', self.data(await self.build()))
        self.assertFalse(self.commit(turn='off'))
        features.update(self.store, self.scope.owner, {'companion_emotion': True})
        old = await self.build()
        affect.reset(self.store, self.scope.owner)
        with self.assertRaises(ContextError):await self.builder.validate(old, self.auth)
        self.assertEqual(affect.state(self.store, self.scope.owner)['feeling'], 'steady')
        self.assertEqual(affect.state(self.store, self.scope.owner)['version'], 0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM mia_affect_turns').fetchone()[0], 0)
        self.assertEqual(features.status(self.store, self.scope.owner)['emotion_tracking']['enabled'], 0)

    async def test_idempotent_bounded_ledger_and_concurrent_version_compare(self):
        self.enable();self.commit()
        self.assertFalse(self.commit())
        with self.store.lock, self.store.db:
            self.assertFalse(affect.commit(self.store, self.scope.owner, 's', 'stale-phone', '你真烦',
                {'feeling': 'hurt', 'intensity': 2}, 0))
        for i in range(140):self.commit('steady', str(i))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM mia_affect_turns').fetchone()[0], 128)
        row = self.store.db.execute('SELECT source_sha256 FROM mia_affect_turns LIMIT 1').fetchone()[0]
        self.assertEqual(row, hashlib.sha256('你真烦'.encode()).hexdigest())
        self.store.clear(self.scope.owner)
        self.assertEqual(affect.state(self.store, self.scope.owner)['version'], 0)

    async def test_v5_migration_failure_is_atomic_and_reopen_preserves_defaults(self):
        with self.store.lock, self.store.db:
            for table in ('youmi_affect_system_history', 'youmi_affect_system_turns', 'youmi_affect_system'):
                self.store.db.execute('DROP TABLE ' + table)
            self.store.db.execute('DROP TABLE mia_affect_turns')
            self.store.db.execute('DROP TABLE mia_affect_state')
            self.store.db.execute('ALTER TABLE mia_feature_settings DROP COLUMN companion_emotion')
            self.store.db.execute('DROP TABLE mia_astrbot_turns')
            self.store.db.execute('DROP TABLE mia_companion_profile')
            self.store.db.execute('DELETE FROM mia_schema_migrations WHERE version>=6')
            self.store.db.execute('UPDATE mia_schema_migrations SET schema_checksum=? WHERE version=5', (_schema_checksum(self.store.db),))
        self.store.db.set_authorizer(lambda action, name, *rest: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TABLE and name == 'mia_affect_state' else sqlite3.SQLITE_OK)
        with self.assertRaises(sqlite3.DatabaseError):migrate(self.store.db)
        self.store.db.set_authorizer(None)
        self.assertNotIn('companion_emotion', [r[1] for r in self.store.db.execute('PRAGMA table_info(mia_feature_settings)')])
        migrate(self.store.db)
        self.assertFalse(features.settings(self.store, self.scope.owner)['companion_emotion'])
        self.assertEqual(self.store.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')


class AppraisalTests(unittest.TestCase):
    def test_valid_json_or_fences_preserve_literal_source_without_persisting_quote(self):
        raw = json.dumps({'feeling': 'hurt', 'intensity': 2, 'quote': '你真烦'})
        for text in (raw, '```json\n' + raw + '\n```'):
            self.assertEqual(affect.parse_appraisal(text, '你真烦'), {'feeling': 'hurt', 'intensity': 2})
        for bad in ({'feeling': 'evil', 'intensity': 1, 'quote': '你真烦'},
                    {'feeling': 'hurt', 'intensity': True, 'quote': '你真烦'},
                    {'feeling': 'hurt', 'intensity': 2, 'quote': '虚构原话'},
                    {'feeling': 'hurt', 'intensity': 2, 'quote': '你真烦', 'role': '改写'}):
            with self.assertRaises(ValueError):affect.parse_appraisal(json.dumps(bad), '你真烦')
        for text in ('bad JSON', None, 'x' * 2049):
            with self.assertRaises(ValueError):affect.parse_appraisal(text, '你好')

    def test_only_explicit_state_management_commands_take_the_management_path(self):
        self.assertTrue(affect.CONTROL.search('请把你的情绪设置为难过，写入后台'))
        self.assertFalse(affect.CONTROL.search('我今天有点难过'))
        self.assertFalse(affect.CONTROL.search('你开心点'))


class AffectReplyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reply_fixture.ReplyTests.setUp(self)
        self.appraisal_raw = json.dumps({'feeling': 'hurt', 'intensity': 2, 'quote': '你真烦'})
        self.appraisal_requests, self.client_options = [], []
        def create(**request):
            self.appraisal_requests.append(request)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.appraisal_raw))])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        def options(**values):
            self.client_options.append(values)
            return client
        self.provider.client = SimpleNamespace(with_options=options)
        self.provider._apply_thinking_disabled = lambda params: params.update(extra_body={'thinking': {'type': 'disabled'}})
    tearDown = reply_fixture.ReplyTests.tearDown
    write_profile = reply_fixture.ReplyTests.write_profile

    async def run_reply(self, raw=None, functions=None, query="你真烦"):
        self.conn.loop = asyncio.get_running_loop()
        features.update(self.store, self.scope.owner, {'companion_emotion': True})
        self.conn.dialogue.dialogue = [reply_fixture.dto('user', query, marker='actual')]
        if raw is not None:
            self.provider.response = lambda *args, **kwargs: iter(raw)
        return await asyncio.to_thread(lambda: list(reply.response(self.conn, functions, 0)))

    async def test_actual_reply_stream_and_completion_persist_only_after_checked_completion(self):
        shown = await self.run_reply(['😔这句话让我有些难过。'])
        self.assertEqual(''.join(shown), '😔这句话让我有些难过。')
        self.assertEqual(self.client_options, [{'max_retries': 0, 'timeout': 5}])
        self.assertEqual(self.appraisal_requests[0]['model'], self.provider.model_name)
        self.assertEqual(self.appraisal_requests[0]['max_tokens'], 256)
        self.assertEqual(affect.state(self.store, self.scope.owner)['version'], 0)
        from types import SimpleNamespace
        self.conn._youmi_memory = SimpleNamespace(suppressed=True)
        self.assertFalse(await reply.finish_affect(self.conn))
        self.conn._youmi_memory.suppressed = False
        self.assertTrue(await reply.finish_affect(self.conn))
        self.assertFalse(await reply.finish_affect(self.conn))
        state = affect.state(self.store, self.scope.owner)
        self.assertEqual(state['feeling'], 'hurt')
        await reply.validate(self.conn)
        # Ordinary affect updates don't throw away local history or cancel memory jobs.
        self.assertIn('youmi_affect', self.conn._youmi_context_turn.bundle.request()['messages'][1]['content'])

    async def test_missing_appraisal_preserves_spoken_reply_without_inventing_a_transition(self):
        self.appraisal_raw = 'invalid JSON'
        shown = await self.run_reply(['😔我还有些在意。'])
        self.assertEqual(''.join(shown), '😔我还有些在意。')
        self.assertFalse(await reply.finish_affect(self.conn))
        self.assertEqual(affect.state(self.store, self.scope.owner)['version'], 0)

    async def test_appraisal_timeout_keeps_normal_reply_and_releases_model_slot(self):
        from server.companion.model_requests import model_requests
        with patch('server.companion.affect.request_appraisal', side_effect=TimeoutError):
            shown = await self.run_reply()
        self.assertEqual(''.join(shown), '😌第一句。第二句。')
        self.assertFalse(await reply.finish_affect(self.conn))
        self.assertEqual(model_requests.snapshot()['active'], 0)

    async def test_switch_change_during_appraisal_stops_generation_and_state_write(self):
        from server.companion.model_requests import model_requests
        def changed(*args):
            features.update(self.store, self.scope.owner, {'companion_emotion': False})
            return self.appraisal_raw
        with patch('server.companion.affect.request_appraisal', side_effect=changed):
            with self.assertRaises(ContextError):await self.run_reply()
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(affect.state(self.store, self.scope.owner)['version'], 0)
        self.assertEqual(model_requests.snapshot()['active'], 0)

    async def test_tool_pairs_strip_prefix_without_changing_tool_payloads(self):
        shown = await self.run_reply(['😔我想先把话说清楚。'], functions=self.tools)
        self.assertEqual(shown, [('😔我想先把话说清楚。', None)])
        self.assertTrue(await reply.finish_affect(self.conn))

    async def test_revocation_reset_or_invalid_metadata_cannot_commit(self):
        await self.run_reply(['😔我听见了。'])
        self.auth.active = False
        with self.assertRaises(PrivateAuthorizationRequired):await reply.finish_affect(self.conn)
        self.auth.active = True
        previous_scope = self.conn._youmi_scope
        self.conn._youmi_scope = replace(self.scope, permissions=frozenset({'memory:read'}))
        turn, proposal = self.conn._youmi_affect_pending
        turn = replace(turn, bundle=replace(turn.bundle, scope=self.conn._youmi_scope))
        self.conn._youmi_affect_pending = (turn, proposal)
        with self.assertRaises(PrivateAuthorizationRequired):await reply.finish_affect(self.conn)
        self.conn._youmi_scope = previous_scope
        affect.reset(self.store, self.scope.owner)
        with self.assertRaises(ContextError):await reply.finish_affect(self.conn)
        self.appraisal_raw = 'bad JSON'
        self.assertEqual(await self.run_reply(['照常回应。']), ['照常回应。'])
        self.assertEqual(affect.state(self.store, self.scope.owner)['version'], 0)


    async def test_state_set_request_has_no_model_calls_or_fabricated_state_write(self):
        shown = await self.run_reply(query='请把你的情绪设置为难过，写入后台')
        self.assertEqual(shown, [affect.CONTROL_REPLY])
        self.assertEqual(self.appraisal_requests, [])
        self.assertEqual(self.provider.calls, [])
        self.assertFalse(await reply.finish_affect(self.conn))

    async def test_outer_chat_hook_commits_without_memory_worker_and_skips_interrupted_turns(self):
        from unittest.mock import AsyncMock
        self.conn.loop = asyncio.get_running_loop()
        features.update(self.store, self.scope.owner, {'companion_emotion': True})
        self.provider.response = lambda *args, **kwargs: iter(['😔这句话让我有些难过。'])
        for mode in ('interrupted', 'limited', 'complete'):
            self.conn.client_abort = False
            self.conn.sentence_id = mode
            self.conn.dialogue.dialogue = [reply_fixture.dto('user', '你真烦', marker=mode)]
            @youmi.completed_chat
            def chat(conn, query, depth=0):
                youmi.begin_turn(conn, query)
                spoken = ''.join(reply.response(conn, None, depth))
                youmi.guard_history(conn.dialogue, reply_fixture.dto('assistant', spoken))
                if mode == 'interrupted':conn.client_abort = True
                if mode == 'limited':youmi.incomplete_turn()
            with patch('server.companion.authorization.allow_output', new=AsyncMock(return_value=False)):
                await asyncio.to_thread(chat, self.conn, '你真烦')
            self.assertEqual(affect.state(self.store, self.scope.owner)['version'], 1 if mode == 'complete' else 0)
            self.assertIsNone(self.conn._youmi_job_status)


class AffectManagementTests(unittest.IsolatedAsyncioTestCase):
    from server.tests import test_companion_features as management_fixture
    asyncSetUp = management_fixture.ManagementRouteTests.asyncSetUp
    asyncTearDown = management_fixture.ManagementRouteTests.asyncTearDown
    post = management_fixture.ManagementRouteTests.post

    async def test_console_own_emotion_read_enable_reset_keeps_user_consent_and_other_settings(self):
        response = await self.post({'action': 'set_feature_settings', 'features': {'companion_emotion': True}})
        self.assertTrue((await response.json())['data']['mia_emotion']['enabled'])
        owner = 'user:1:agent:allowed'
        with self.store.lock, self.store.db:
            self.assertTrue(affect.commit(self.store, owner, 's', 't', '你真烦', {'feeling': 'hurt', 'intensity': 2}, 0))
        response = await self.post({'action': 'status'})
        self.assertEqual((await response.json())['data']['mia_emotion']['feeling'], 'hurt')
        response = await self.post({'action': 'reset_mia_emotion'})
        state = (await response.json())['data']
        self.assertEqual(state['mia_emotion']['version'], 0)
        self.assertEqual(state['mia_emotion']['feeling'], 'steady')
        self.assertEqual(state['emotion_tracking']['enabled'], 0)
        self.assertFalse(state['paused'])
        self.assertTrue(state['features']['memory_recall'])
        self.assertTrue(state['features']['companion_emotion'])
