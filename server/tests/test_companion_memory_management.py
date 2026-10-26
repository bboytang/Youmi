"""Agent-owner console HTTP operations against the real canonical store."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from server.companion import emotional_system, memory_management
from server.companion.management import ManagementHandler
from server.companion.memory import MemoryError
from server.tests import test_companion_features as fixture
from server.tests import test_companion_memory_artifacts as artifacts_fixture


OWNER = 'user:1:agent:allowed'
FOREIGN = 'user:2:agent:other'


class MemoryManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.ManagementRouteTests.asyncSetUp(self)
        self.store.clock = lambda: 1800000000
    asyncTearDown = fixture.ManagementRouteTests.asyncTearDown
    post = fixture.ManagementRouteTests.post

    async def call(self, operation, **kwargs):
        return await self.post(dict(action='memory_system', operation=operation), **kwargs)

    async def data(self, operation):
        response = await self.call(operation)
        self.assertEqual(response.status, 200, await response.text())
        return (await response.json())['data']

    def fact(self, content='我喜欢蓝色', owner=OWNER, session='seed'):
        return self.store.apply_extraction(owner, session, self.store.snapshot(owner)['revision'],
            [content], {'memories': [dict(category='preference', content=content, source_index=0)]},
            session)['memories'][0]

    async def test_status_is_read_only_default_off_and_reports_unimplemented_capabilities(self):
        before = self.store.db.total_changes
        state = await self.data({'action': 'status'})
        self.assertEqual(self.store.db.total_changes, before)
        self.assertFalse(state['reflection']['enabled'])
        self.assertFalse(state['emotion_linkage']['enabled'])
        self.assertFalse(state['cognition']['enabled'])
        self.assertEqual(state['capabilities'], dict(engine='legacy', emotion_ranking='shadow_only',
            automatic_cognition=True, automatic_candidate_execution=False, permanent_memories=True))
        self.assertNotIn('owner', state['reflection'])
        self.assertEqual(len(self.calls), 2)

    async def test_ownership_and_secret_checked_before_any_operation(self):
        self.assertEqual((await self.call({'action': 'status'}, secret='browser')).status, 401)
        self.assertEqual(self.calls, [])
        self.allowed = False
        self.assertEqual((await self.call({'action': 'reflection', 'operation': {
            'action': 'configure', 'version': 0, 'enabled': True, 'interval_seconds': 60}})).status, 403)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_reflection_settings').fetchone()[0], 0)

    async def test_list_detail_and_sources_are_owner_scoped_and_paginated(self):
        foreign = self.fact('我是另一个用户', FOREIGN, 'foreign')
        ids = [self.fact(f'我喜欢颜色{n}', session=str(n)) for n in range(53)]
        first = await self.data({'action': 'list'})
        second = await self.data({'action': 'list', 'offset': 50})
        self.assertEqual((first['total'], len(first['memories']), len(second['memories'])), (53, 50, 3))
        self.assertEqual({row['id'] for row in first['memories'] + second['memories']}, set(ids))
        identifier = ids[0]
        for n in range(52):
            self.fact('我喜欢颜色0', session='corroboration-' + str(n))
        detail = await self.data({'action': 'detail', 'id': identifier})
        page = await self.data({'action': 'detail', 'id': identifier, 'offset': 50})
        self.assertEqual((detail['source_total'], len(detail['sources']), len(page['sources'])), (53, 50, 3))
        self.assertTrue(all(row['excerpt'] == '我喜欢颜色0' for row in detail['sources'] + page['sources']))
        self.assertEqual(detail['memory']['content_version'], 1)
        self.assertEqual(detail['memory']['evidence_version'], 54)
        for value in (foreign, 'missing'):
            response = await self.call({'action': 'detail', 'id': value})
            self.assertEqual(response.status, 400)
            self.assertEqual(await response.json(), {'code': 400, 'error': 'not_found'})

    async def test_pending_and_deleted_records_do_not_become_confirmed_or_resurrect(self):
        identifier = self.fact()
        self.store.edit_memory(OWNER, identifier, '我喜欢蓝色', confirmed=False)
        record = (await self.data({'action': 'detail', 'id': identifier}))['memory']
        self.assertFalse(record['confirmed'])
        self.assertEqual(record['state'], 'pending')
        self.store.delete_memory(OWNER, identifier)
        self.assertEqual((await self.data({'action': 'list'}))['total'], 0)
        self.assertEqual((await self.call({'action': 'detail', 'id': identifier})).status, 400)

    async def test_strict_commands_reject_browser_identity_unbounded_pages_and_model_actions(self):
        invalid = [None, [], {}, {'action': []}, {'action': 'clear'},
            {'action': 'status', 'owner': FOREIGN}, {'action': 'list', 'offset': True},
            {'action': 'list', 'offset': -1}, {'action': 'list', 'offset': 2147483648},
            {'action': 'detail', 'id': ''}, {'action': 'reflection', 'operation': None},
            {'action': 'cognition', 'operation': {'action': 'status', 'owner': FOREIGN}},
            {'action': 'remember', 'content': '伪造事实'}, {'action': 'delete_artifact', 'id': 'x', 'version': True}]
        for command in invalid:
            with self.subTest(command=command):
                self.assertEqual((await self.call(command)).status, 400)
        self.assertEqual((await self.post({'action': 'memory_system', 'operation': {'action': 'status'},
            'owner': FOREIGN})).status, 400)

    async def test_reflection_configuration_conflicts_are_structured_and_owner_isolated(self):
        operation = dict(action='configure', enabled=True, interval_seconds=60, version=0)
        state = await self.data({'action': 'reflection', 'operation': operation})
        self.assertEqual(state['version'], 1)
        response = await self.call({'action': 'reflection', 'operation': operation})
        self.assertEqual(response.status, 409)
        self.assertEqual(await response.json(), dict(code=409, error='conflict'))
        from server.companion.memory_reflection_policy import status
        self.assertFalse(status(self.store, FOREIGN)['enabled'])
        self.assertEqual((await self.data({'action': 'reflection', 'operation': {'action': 'status'}}))['version'], 1)

    async def test_linkage_configuration_preserves_all_emotion_and_persona_values(self):
        from server.companion.character import YOUMI_V1, status, update
        update(self.store, OWNER, dict(action='set_character', version=0, enabled=True,
            values=dict(YOUMI_V1, background='人设原文，不得改写\n' * 2000)))
        emotion = emotional_system.status(self.store, OWNER)
        emotion['dimensions']['joy']['mode'] = 'fixed'
        emotion['dimensions']['joy']['baseline'] = 37
        emotional_system.manage(self.store, OWNER, dict(action='configure', version=emotion['version'],
            enabled=True, dimensions=emotion['dimensions']))
        original = emotional_system.status(self.store, OWNER)
        persona = status(self.store, OWNER)
        state = await self.data({'action': 'emotion_linkage', 'operation': {'action': 'status'}})
        command = {**state, 'action': 'configure', 'enabled': True, 'stimulus_weight': .4}
        saved = await self.data({'action': 'emotion_linkage', 'operation': command})
        self.assertTrue(saved['enabled'])
        self.assertEqual(saved['version'], 1)
        self.assertEqual((await self.call({'action': 'emotion_linkage', 'operation': command})).status, 409)
        self.assertEqual(emotional_system.status(self.store, OWNER), original)
        self.assertEqual(status(self.store, OWNER), persona)
        self.assertTrue((await self.data({'action': 'status'}))['capabilities']['automatic_cognition'])

    async def test_cognition_configuration_and_energy_adjustment_use_separate_versions(self):
        state = await self.data({'action': 'cognition', 'operation': {'action': 'status'}})
        state['dimensions']['recall']['mode'] = 'dynamic'
        saved = await self.data({'action': 'cognition', 'operation': dict(action='configure', enabled=True,
            version=state['version'], dimensions=state['dimensions'])})
        adjusted = await self.data({'action': 'cognition', 'operation': dict(action='adjust',
            version=saved['version'], energy={'recall': 2, 'expression': 4})})
        self.assertEqual(adjusted['energy']['recall'], 2)
        self.assertEqual(adjusted['energy']['expression'], 0)
        self.assertEqual((await self.call({'action': 'cognition', 'operation': dict(action='adjust',
            version=saved['version'], energy={'recall': 3})})).status, 409)
        self.assertEqual(len(emotional_system.status(self.store, OWNER)['dimensions']), 51)

    async def test_manual_merge_preserves_ids_sources_versions_and_retry_identity(self):
        retired = self.fact()
        survivor = self.fact('我喜欢浅蓝色', session='second')
        listing = await self.data({'action': 'list'})
        rows = {row['id']: row for row in listing['memories']}
        command = dict(action='merge', retired_id=retired, survivor_id=survivor, operation_id='console-merge',
            revision=listing['revision'], versions={name: [rows[i]['content_version'], rows[i]['evidence_version']]
                for name, i in [('retired', retired), ('survivor', survivor)]})
        merged = await self.data(command)
        self.assertFalse(merged['duplicate'])
        self.assertTrue((await self.data(command))['duplicate'])
        detail = await self.data({'action': 'detail', 'id': retired})
        self.assertEqual(detail['memory']['state'], 'superseded')
        self.assertEqual(detail['sources'][0]['excerpt'], '我喜欢蓝色')
        self.assertEqual(detail['replacements'][0]['survivor_id'], survivor)
        self.assertEqual((await self.data({'action': 'list'}))['total'], 2)
        self.assertEqual((await self.call({**command, 'operation_id': 'another'})).status, 409)

    async def test_foreign_merge_and_stale_evidence_roll_back_without_changing_facts(self):
        retired = self.fact()
        foreign = self.fact('我喜欢绿色', FOREIGN, 'foreign')
        command = dict(action='merge', operation_id='merge', retired_id=retired, survivor_id=foreign,
            revision=self.store.snapshot(OWNER)['revision'], versions={'retired': [1, 2], 'survivor': [1, 2]})
        before = [tuple(r) for r in self.store.db.execute('SELECT * FROM memories')]
        response = await self.call(command)
        self.assertEqual(response.status, 409)
        self.assertEqual((await response.json())['error'], 'evidence_changed')
        self.assertEqual([tuple(r) for r in self.store.db.execute('SELECT * FROM memories')], before)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_replacements').fetchone()[0], 0)

    async def test_post_operation_revocation_blocks_data_even_for_conflicts(self):
        for fail in (False, True):
            self.allowed = True
            def apply(*args):
                self.allowed = False
                if fail:
                    raise MemoryError('conflict', 'private conflict details')
                return {'private': 'never return'}
            with patch.object(memory_management, 'manage', apply):
                response = await self.call({'action': 'status'})
                self.assertEqual(response.status, 403)
                self.assertNotIn('private', await response.text())

    async def test_changed_snapshot_cannot_be_reported_as_a_success(self):
        with patch.object(memory_management, '_read', side_effect=MemoryError('memory_changed', 'stale read')):
            response = await self.call({'action': 'list'})
        self.assertEqual(response.status, 409)
        self.assertNotIn('data', await response.json())

    async def test_fact_edit_requires_versions_and_removes_old_sources_without_replacing_id(self):
        identifier = self.fact()
        detail = await self.data({'action': 'detail', 'id': identifier})
        expected = dict(revision=detail['revision'], content_version=detail['memory']['content_version'],
            evidence_version=detail['memory']['evidence_version'])
        command = dict(action='edit', id=identifier, expected=expected, content='我现在不喜欢蓝色', confirmed=True)
        self.assertEqual(await self.data(command), {'saved': True})
        after = await self.data({'action': 'detail', 'id': identifier})
        self.assertEqual(after['memory']['id'], identifier)
        self.assertEqual(after['memory']['content'], '我现在不喜欢蓝色')
        self.assertEqual(after['sources'], [])
        self.assertGreater(after['memory']['content_version'], expected['content_version'])
        self.assertEqual((await self.call(command)).status, 409)

    async def test_fact_writes_reject_new_evidence_and_cross_process_or_owner_changes(self):
        from server.companion.memory import MemoryStore
        identifier = self.fact()
        detail = await self.data({'action': 'detail', 'id': identifier})
        expected = dict(revision=detail['revision'], content_version=detail['memory']['content_version'],
            evidence_version=detail['memory']['evidence_version'])
        self.fact(session='new-evidence')
        for action in ('edit', 'delete'):
            command = dict(action=action, id=identifier, expected=expected)
            if action == 'edit':
                command.update(content='旧页面覆盖', confirmed=True)
            self.assertEqual((await self.call(command)).status, 409)
        other = MemoryStore(self.temp.name + '/db.sqlite3')
        try:
            other.edit_memory(OWNER, identifier, '另一进程已修改')
        finally:
            other.close()
        self.assertEqual((await self.call(dict(action='delete', id=identifier, expected=expected))).status, 409)
        foreign = self.fact('他人的记忆', FOREIGN, 'foreign')
        self.assertEqual((await self.call(dict(action='delete', id=foreign, expected=expected))).status, 409)
        self.assertEqual((await self.data({'action': 'detail', 'id': identifier}))['memory']['content'], '另一进程已修改')

    async def test_fact_version_check_runs_under_write_transaction_and_delete_cascades_sources(self):
        import sqlite3
        identifier = self.fact()
        detail = await self.data({'action': 'detail', 'id': identifier})
        expected = dict(revision=detail['revision'], content_version=detail['memory']['content_version'],
            evidence_version=detail['memory']['evidence_version'])
        original = self.store._memory_cas
        def verify(owner, memory_id, versions):
            self.assertTrue(self.store.db.in_transaction)
            contender = sqlite3.connect(self.temp.name + '/db.sqlite3', timeout=0)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    contender.execute('BEGIN IMMEDIATE')
            finally:
                contender.close()
            return original(owner, memory_id, versions)
        with patch.object(self.store, '_memory_cas', side_effect=verify):
            self.assertEqual(await self.data(dict(action='delete', id=identifier, expected=expected)), {'saved': True})
        self.assertEqual((await self.data({'action': 'list'}))['total'], 0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM sources').fetchone()[0], 0)

    async def test_fact_writes_cannot_omit_versions_or_use_boolean_versions(self):
        identifier = self.fact()
        for expected in (None, {}, {'revision': True, 'content_version': 1, 'evidence_version': 2}):
            response = await self.call(dict(action='delete', id=identifier, expected=expected))
            self.assertEqual(response.status, 400)
        self.assertEqual((await self.data({'action': 'list'}))['total'], 1)


    async def test_owner_pin_http_preserves_sources_and_rejects_stale_unpin(self):
        identifier = self.fact()
        detail = await self.data(dict(action='detail', id=identifier))
        expected = dict(revision=detail['revision'], content_version=detail['memory']['content_version'],
            evidence_version=detail['memory']['evidence_version'])
        result = await self.data(dict(action='pin', id=identifier, pinned=True, expected=expected))
        self.assertTrue(result['pinned'])
        updated = await self.data(dict(action='detail', id=identifier))
        self.assertIs(updated['memory']['pinned'], True)
        self.assertEqual(updated['sources'], detail['sources'])
        self.assertEqual((await self.call(dict(action='pin', id=identifier, pinned=False, expected=expected))).status, 409)

class ArtifactManagementTests(unittest.IsolatedAsyncioTestCase):
    setUp = artifacts_fixture.ArtifactTests.setUp
    tearDown = artifacts_fixture.ArtifactTests.tearDown
    event = artifacts_fixture.ArtifactTests.event
    row = artifacts_fixture.ArtifactTests.row
    seed = artifacts_fixture.ArtifactTests.seed
    action = artifacts_fixture.ArtifactTests.action
    apply = artifacts_fixture.ArtifactTests.apply
    call = MemoryManagementTests.call
    data = MemoryManagementTests.data

    async def asyncSetUp(self):
        async def verify(endpoint, proof):
            return {'owner': self.scope.owner}
        handler = ManagementHandler({'manager-api': {'secret': 'synthetic-server-secret'}},
            store=self.store, authorization=SimpleNamespace(_request=verify))
        app = web.Application()
        app.router.add_post('/youmi/management', handler.handle)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def post(self, command):
        return await self.client.post('/youmi/management', json={'operator_id': self.scope.operator_id,
            'agent_id': self.scope.agent_id, 'request': command},
            headers={'Authorization': 'Bearer synthetic-server-secret'})

    async def test_artifacts_have_canonical_evidence_and_never_execute_candidates(self):
        fact = await self.seed()
        await self.seed('foreign', '我喜欢绿色', self.peer)
        await self.apply(self.action(fact), self.action(fact, action='propose_retirement', key='old'))
        items = (await self.data({'action': 'artifacts'}))['artifacts']
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item['body'] == fact['content'] for item in items))
        self.assertTrue(next(item for item in items if item['kind'] == 'retirement')['preview_only'])
        self.assertTrue((await self.data({'action': 'detail', 'id': fact['id']}))['memory']['confirmed'])

    async def test_artifact_delete_uses_version_and_cannot_delete_other_agents_or_facts(self):
        fact = await self.seed()
        await self.apply(self.action(fact))
        item = (await self.data({'action': 'artifacts'}))['artifacts'][0]
        self.assertEqual((await self.call({'action': 'delete_artifact', 'id': item['id'], 'version': 2})).status, 409)
        from server.companion.memory_artifacts import YoumiMemoryArtifacts
        self.assertEqual(len((await self.data({'action': 'artifacts'}))['artifacts']), 1)
        with self.assertRaises(MemoryError):
            YoumiMemoryArtifacts(self.store)._remove(self.peer.owner, item['id'], item['version'])
        self.assertEqual(await self.data({'action': 'delete_artifact', 'id': item['id'], 'version': item['version']}),
            {'deleted': True})
        self.assertEqual((await self.data({'action': 'artifacts'}))['artifacts'], [])
        self.assertEqual((await self.data({'action': 'detail', 'id': fact['id']}))['memory']['content'], fact['content'])

    async def test_source_change_and_read_switch_remove_views_without_restoring_old_prose(self):
        from server.companion import features
        fact = await self.seed()
        await self.apply(self.action(fact))
        features.update(self.store, self.scope.owner, {'memory_recall': False})
        self.assertEqual((await self.data({'action': 'artifacts'}))['artifacts'], [])
        features.update(self.store, self.scope.owner, {'memory_recall': True})
        self.assertEqual(len((await self.data({'action': 'artifacts'}))['artifacts']), 1)
        self.store.edit_memory(self.scope.owner, fact['id'], '我已经不喜欢蓝色')
        self.assertEqual((await self.data({'action': 'artifacts'}))['artifacts'], [])
        detail = await self.data({'action': 'detail', 'id': fact['id']})
        self.assertEqual(detail['sources'], [])
        self.assertEqual(detail['memory']['content'], '我已经不喜欢蓝色')
