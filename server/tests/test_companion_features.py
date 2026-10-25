from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from server.companion import features, events
from server.companion.context import ContextError
from server.companion.extraction import context_for, growth_eligible
from server.companion.management import ManagementHandler
from server.companion.memory import MemoryError, MemoryStore
from server.tests import test_companion_context as fixture


class FeatureSwitchTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.ContextBuilderTests.setUp
    tearDown = fixture.ContextBuilderTests.tearDown
    fact = fixture.ContextBuilderTests.fact
    build = fixture.ContextBuilderTests.build
    data = fixture.ContextBuilderTests.data

    async def test_console_change_controls_hardware_and_phone_with_the_same_agent_owner(self):
        from dataclasses import replace
        from server.companion.context import ContextBuilder
        from server.companion.semantic import SemanticMemoryIndex
        self.fact('我喜欢蓝色。')
        await self.index.rebuild_batch(self.scope, self.auth)
        phone_store = MemoryStore(Path(self.temp.name) / 'memory.sqlite3')
        phone_scope = replace(self.scope, device_id='bound-iphone', client_id='ios-client')
        phone_index = SemanticMemoryIndex(phone_store, self.encoder)
        phone_builder = ContextBuilder(phone_index)
        try:
            hardware_turn = await self.build()
            phone_turn = await phone_builder.build(phone_scope, self.auth, 'phone-turn', '蓝色', limits=self.limits)
            self.assertTrue(self.data(phone_turn)['facts'])
            features.update(self.store, self.scope.owner, {'memory_recall': False})
            for builder, turn in [(self.builder, hardware_turn), (phone_builder, phone_turn)]:
                with self.assertRaises(ContextError):
                    await builder.validate(turn, self.auth)
            for index, scope in [(self.index, self.scope), (phone_index, phone_scope)]:
                self.assertEqual((await index.retrieve(scope, self.auth, '蓝色'))['results'], [])
            self.assertEqual(self.data(await self.build())['facts'], [])
            next_phone = await phone_builder.build(phone_scope, self.auth, 'next-phone', '蓝色', limits=self.limits)
            self.assertEqual(self.data(next_phone)['facts'], [])
            self.assertFalse(features.settings(phone_store, phone_scope.owner)['memory_recall'])
        finally:
            phone_store.close()

    async def test_recall_disabled_skips_encoder_preserves_facts_and_invalidates_old_reply(self):
        fact = self.fact('我喜欢蓝色。')
        await self.index.rebuild_batch(self.scope, self.auth)
        old = await self.build()
        features.update(self.store, self.scope.owner, {'memory_recall': False})
        with self.assertRaises(ContextError):
            await self.builder.validate(old, self.auth)
        calls = self.encoder.calls
        self.assertEqual((await self.index.retrieve(self.scope, self.auth, '蓝色'))['results'], [])
        self.assertEqual(self.encoder.calls, calls)
        self.assertEqual(self.store.list_memories(self.scope.owner)[0]['id'], fact)
        features.update(self.store, self.scope.owner, {'memory_recall': True})
        self.assertTrue((await self.index.retrieve(self.scope, self.auth, '蓝色'))['results'])

    async def test_growth_switch_stops_writes_and_context_but_keeps_existing_traits(self):
        ids = [self.fact('我喜欢蓝色' + thing) for thing in ('衬衫', '杯子', '自行车')]
        trait = {'name': '蓝色', 'description': '喜欢蓝色', 'evidence_ids': ids}
        with self.store.lock, self.store.db:
            self.assertTrue(self.store._trait(self.scope.owner, trait, 1))
        await self.index.rebuild_batch(self.scope, self.auth)
        self.assertTrue(self.data(await self.build(query='蓝色'))['traits'])
        features.update(self.store, self.scope.owner, {'personality_growth': False})
        self.assertEqual(self.data(await self.build(query='蓝色'))['traits'], [])
        self.assertFalse(growth_eligible(context_for(self.store, self.scope.owner)))
        with self.store.lock, self.store.db:
            self.assertFalse(self.store._trait(self.scope.owner, dict(trait, name='另一特点'), 2))
        self.assertEqual(len(self.store.personality(self.scope.owner)['traits']), 1)
        features.update(self.store, self.scope.owner, {'personality_growth': True, 'memory_recall': False})
        self.assertEqual(self.data(await self.build(query='蓝色'))['traits'], [])

    async def test_events_switch_stops_creation_and_use_without_deleting_events(self):
        self.fact('我喜欢蓝色。')
        await self.index.rebuild_batch(self.scope, self.auth)
        bundle = await self.build(query='你还记得我喜欢蓝色吗', session_id='session')
        await self.builder.complete(bundle, self.auth, '记得，你喜欢蓝色。')
        self.assertTrue(self.store.handle(self.scope.owner, {'action': 'events'})['events'])
        features.update(self.store, self.scope.owner, {'interaction_events': False})
        self.assertEqual(self.data(await self.build(query='你还记得我喜欢蓝色吗', session_id='next'))['events'], [])
        with self.store.lock, self.store.db:
            self.assertEqual(events.context(self.store, self.scope.owner, '请不要叫我小名', [], session_id='s', turn_id='t'), [])
        self.assertEqual(len(self.store.handle(self.scope.owner, {'action': 'events'})['events']), 1)

    async def test_switches_are_owner_scoped_persistent_and_strictly_boolean(self):
        features.update(self.store, self.scope.owner, {'interaction_events': False})
        self.assertTrue(features.settings(self.store, 'user:2:agent:other')['interaction_events'])
        reopened = MemoryStore(Path(self.temp.name) / 'memory.sqlite3')
        try:
            self.assertFalse(features.settings(reopened, self.scope.owner)['interaction_events'])
        finally:
            reopened.close()
        revision = self.store.snapshot(self.scope.owner)['revision']
        for invalid in ({'memory_recall': 0}, {'unknown': False}, {}, None):
            with self.assertRaises(MemoryError):
                features.update(self.store, self.scope.owner, invalid)
        self.assertEqual(self.store.snapshot(self.scope.owner)['revision'], revision)

    async def test_v4_upgrade_preserves_facts_and_defaults_and_ddl_failure_is_atomic(self):
        import sqlite3
        self.fact('我喜欢蓝色。')
        with self.store.lock, self.store.db:
            self.store.db.execute('DROP TABLE mia_affect_turns')
            self.store.db.execute('DROP TABLE mia_affect_state')
            self.store.db.execute('DROP TABLE mia_astrbot_turns')
            self.store.db.execute('DROP TABLE mia_companion_profile')
            self.store.db.execute('DELETE FROM mia_schema_migrations WHERE version>=6')
            self.store.db.execute('DROP TABLE mia_feature_settings')
            self.store.db.execute('DELETE FROM mia_schema_migrations WHERE version=5')
        path = Path(self.temp.name) / 'memory.sqlite3'
        self.store.db.set_authorizer(lambda action, name, *rest:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_TABLE and name == 'mia_feature_settings'
            else sqlite3.SQLITE_OK)
        from server.companion.schema import migrate
        with self.assertRaises(sqlite3.DatabaseError):
            migrate(self.store.db)
        self.store.db.set_authorizer(None)
        self.assertEqual(self.store.db.execute('SELECT MAX(version) FROM mia_schema_migrations').fetchone()[0], 4)
        upgraded = MemoryStore(path)
        try:
            self.assertEqual(features.settings(upgraded, self.scope.owner), features.DEFAULTS)
            self.assertEqual(upgraded.list_memories(self.scope.owner)[0]['content'], '我喜欢蓝色。')
            self.assertEqual(upgraded.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        finally:
            upgraded.close()


class ManagementRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp.name) / 'db.sqlite3')
        self.calls = []
        self.allowed = True
        async def verify(endpoint, proof):
            from server.companion.authorization import PrivateAuthorizationRequired
            self.calls.append((endpoint, proof))
            if not self.allowed:
                raise PrivateAuthorizationRequired()
            return {'owner': 'user:1:agent:allowed'}
        handler = ManagementHandler({'manager-api': {'secret': 'synthetic-server-secret'}},
            store=self.store, authorization=SimpleNamespace(_request=verify))
        app = web.Application()
        app.router.add_post('/mia/management', handler.handle)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.store.close()
        self.temp.cleanup()

    async def post(self, command, secret='synthetic-server-secret', **extra):
        return await self.client.post('/mia/management', json={
            'operator_id': 1, 'agent_id': 'allowed', 'request': command, **extra},
            headers={'Authorization': 'Bearer ' + secret})

    async def test_live_proof_and_readback_for_each_switch_and_emotion_default(self):
        response = await self.post({'action': 'status'})
        state = (await response.json())['data']
        self.assertEqual(state['emotion_tracking']['enabled'], 0)
        self.assertFalse(state['paused'])
        self.assertEqual(state['features'], features.DEFAULTS)
        response = await self.post({'action': 'set_feature_settings', 'features': {'personality_growth': False}})
        self.assertFalse((await response.json())['data']['features']['personality_growth'])
        response = await self.post({'action': 'pause', 'paused': True})
        self.assertTrue((await response.json())['data']['paused'])
        response = await self.post({'action': 'emotion_consent', 'enabled': True})
        self.assertEqual((await response.json())['data']['emotion_tracking']['enabled'], 1)
        self.assertEqual(len(self.calls), 8)

    async def test_rejects_browser_token_foreign_owner_invalid_values_and_revocation(self):
        self.assertEqual((await self.post({'action': 'status'}, secret='browser-token')).status, 401)
        self.assertFalse(self.calls)
        self.assertEqual((await self.post({'action': 'status'}, owner='user:2')).status, 400)
        self.assertEqual((await self.post({'action': 'set_feature_settings', 'features': {'memory_recall': 'false'}})).status, 400)
        self.assertEqual((await self.post({'action': 'clear'})).status, 400)
        self.allowed = False
        self.assertEqual((await self.post({'action': 'pause', 'paused': True})).status, 403)
        self.assertFalse(self.store.snapshot('user:1:agent:allowed')['paused'])

    async def test_memory_pages_are_owner_scoped_and_delete_invalidates_existing_context(self):
        owner = 'user:1:agent:allowed'
        def remember(who, number):
            content = f'我喜欢颜色{number}'
            return self.store.apply_extraction(who, str(number), self.store.snapshot(who)['revision'],
                [content], {'memories': [{'category': 'preference', 'content': content, 'source_index': 0}]},
                str(number))['memories'][0]
        foreign = remember('user:2:agent:other', 999)
        ids = [remember(owner, n) for n in range(53)]
        first = (await (await self.post({'action': 'memory_list'})).json())['data']
        second = (await (await self.post({'action': 'memory_list', 'offset': 50})).json())['data']
        self.assertEqual((first['total'], len(first['memories']), len(second['memories'])), (53, 50, 3))
        self.assertNotIn(foreign, {r['id'] for r in first['memories'] + second['memories']})
        revision = self.store.snapshot(owner)['revision']
        self.assertEqual((await self.post({'action': 'memory_delete', 'id': foreign})).status, 400)
        self.assertEqual(self.store.snapshot(owner)['revision'], revision)
        deleted = (await (await self.post({'action': 'memory_delete', 'id': ids[0]})).json())['data']
        self.assertEqual(deleted['total'], 52)
        self.assertGreater(self.store.snapshot(owner)['revision'], revision)
        self.assertFalse(self.store.db.execute('SELECT 1 FROM memory_search WHERE memory_id=?', (ids[0],)).fetchone())
        self.assertEqual(len(self.store.list_memories('user:2:agent:other')), 1)
        for offset in [-1, True, '0', 2**63]:
            self.assertEqual((await self.post({'action': 'memory_list', 'offset': offset})).status, 400)
        self.allowed = False
        self.assertEqual((await self.post({'action': 'memory_delete', 'id': ids[1]})).status, 403)
        self.assertEqual(len(self.store.list_memories(owner)), 52)

    async def test_owner_can_manage_intrinsic_character_without_mutating_facts_or_user_emotions(self):
        from server.companion.character import YOUMI_V1
        response = await self.post({'action': 'set_character', 'version': 0, 'enabled': True, 'values': YOUMI_V1})
        state = (await response.json())['data']
        self.assertEqual(state['character']['values']['name'], '悠米')
        self.assertEqual(state['character']['version'], 1)
        self.assertEqual(state['counts']['memories'], 0)
        self.assertEqual(state['emotion_tracking']['enabled'], 0)
        self.assertEqual((await self.post({'action': 'set_character', 'version': 0, 'enabled': False, 'values': YOUMI_V1})).status, 400)
        self.allowed = False
        self.assertEqual((await self.post({'action': 'set_character', 'version': 1, 'enabled': False, 'values': YOUMI_V1})).status, 403)
        from server.companion.character import status
        self.assertTrue(status(self.store, 'user:1:agent:allowed')['enabled'])
