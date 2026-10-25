import json
import json
import sqlite3
from pathlib import Path
from dataclasses import replace
import unittest

from server.companion import character, affect, features
from server.companion.context import ContextError, ContextBuilder
from server.companion.memory import MemoryError, MemoryStore
from server.companion.semantic import SemanticMemoryIndex
from server.companion.schema import migrate, _schema_checksum
from server.tests import test_companion_context as fixture


class CharacterTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.ContextBuilderTests.setUp
    tearDown = fixture.ContextBuilderTests.tearDown
    build = fixture.ContextBuilderTests.build
    data = fixture.ContextBuilderTests.data

    def save(self, values=None, enabled=True):
        return character.update(self.store, self.scope.owner, {'values': values or character.YOUMI_V1,
            'enabled': enabled, 'version': character.status(self.store, self.scope.owner)['version']})

    async def test_intrinsic_baseline_replaces_core_identity_even_with_memory_off_and_survives_reopen(self):
        self.assertIsNone(character.active(self.store, self.scope.owner))
        self.save()
        features.update(self.store, self.scope.owner, {'memory_recall': False})
        bundle = await self.build(role='原角色配置保留，不自动改写')
        system = bundle.request()['messages'][0]['content']
        self.assertIn('CHARACTER_BASELINE_JSON', system)
        self.assertIn('悠米', system)
        self.assertIn('哥哥', system)
        self.assertNotIn('你的名字是 Youmi', system)
        self.assertIn('原角色配置保留，不自动改写', bundle.request_json)
        self.assertEqual(self.data(bundle)['facts'], [])
        self.assertEqual(self.store.list_memories(self.scope.owner), [])

        self.assertEqual(self.store.personality(self.scope.owner)['traits'], [])
        other = replace(self.scope, owner='user:2:agent:other', user_id=2, agent_id='other')
        another = await self.builder.build(other, self.auth, 'other', '你好', limits=self.limits)
        self.assertNotIn('悠米', another.request_json)
        self.assertNotIn('CHARACTER_BASELINE_JSON', another.request_json)
        phone = MemoryStore(Path(self.temp.name) / 'memory.sqlite3')
        try:
            builder = ContextBuilder(SemanticMemoryIndex(phone, self.encoder))
            on_phone = await builder.build(replace(self.scope, device_id='iphone', client_id='ios'),
                self.auth, 'phone', '你好', limits=self.limits)
            self.assertIn('悠米', on_phone.request()['messages'][0]['content'])
        finally: phone.close()

    async def test_recent_addressing_discourages_repetition_without_removing_the_configured_name(self):
        self.save()
        bundle = await self.build(query='你接着说', history=[{'role': 'user', 'content': '刚忙完'},
            {'role': 'assistant', 'content': '哥哥，先休息一下。'}])
        self.assertIn('本轮普通接话不要再加称谓', bundle.request()['messages'][0]['content'])
        self.assertIn('仅当用户明确询问称呼', bundle.request()['messages'][0]['content'])
        self.assertEqual(character.active(self.store, self.scope.owner)['address'], '哥哥')

    async def test_model_sees_address_once_without_redundant_identity_examples_or_affect_copy(self):
        self.save();features.update(self.store, self.scope.owner, {'companion_emotion': True})
        bundle = await self.build(query='今天下雨了')
        self.assertEqual(bundle.request_json.count('哥哥'), 1)
        self.assertNotIn('character', self.data(bundle)['youmi_affect'])
        self.assertEqual(json.loads(bundle.inputs_json)['character']['address'], '哥哥')

    async def test_disposition_changes_actual_affect_and_anger_repairs_gradually_without_erasing_profile(self):
        self.save();features.update(self.store, self.scope.owner, {'companion_emotion': True})
        now = [1800000000];self.store.clock = lambda: now[0]
        original = affect.state(self.store, self.scope.owner)
        self.assertEqual(original['recovery_half_life_seconds'], 10800)
        hot = affect.transition(original, {'feeling': 'angry', 'intensity': 3})
        mild = affect.transition(dict(original, character=dict(character.YOUMI_V1, temper=0)), {'feeling': 'angry', 'intensity': 3})
        self.assertGreater(hot['tension'], mild['tension'])
        warm = affect.transition(original, {'feeling': 'warm', 'intensity': 2})
        distant = affect.transition(dict(original, character=dict(character.YOUMI_V1, warmth=0, attachment=0)), {'feeling': 'warm', 'intensity': 2})
        self.assertGreater(warm['valence'], distant['valence'])
        with self.store.lock, self.store.db:
            self.assertTrue(affect.commit(self.store, self.scope.owner, 's', 'angry', '你怎么这么烦',
                {'feeling': 'angry', 'intensity': 3}, 0))
        angry = affect.state(self.store, self.scope.owner)
        self.assertEqual(angry['label'], '生气')
        steady = affect.transition(angry, {'feeling': 'steady', 'intensity': 1})
        repaired = affect.transition(angry, {'feeling': 'repair', 'intensity': 2})
        self.assertEqual(steady['feeling'], 'angry')
        curious = affect.transition(angry, {'feeling': 'curious', 'intensity': 1})
        self.assertEqual(curious['feeling'], 'angry')
        self.assertTrue(0 < curious['tension'] < angry['tension'])
        self.assertIn('并未平复', affect.guidance(curious))
        residual_guidance = affect.guidance(dict(curious, stimulus='curious'))
        self.assertIn('没有新的冒犯', residual_guidance)
        self.assertIn('不追责', residual_guidance)
        self.assertTrue(0 < repaired['tension'] < angry['tension'])
        self.assertEqual(repaired['label'], '缓和中')
        now[0] += 10800
        self.assertAlmostEqual(affect.state(self.store, self.scope.owner)['tension'], angry['tension'] / 2, places=3)
        affect.reset(self.store, self.scope.owner)
        self.assertEqual(character.active(self.store, self.scope.owner), character.YOUMI_V1)
        self.assertEqual(affect.state(self.store, self.scope.owner)['feeling'], 'steady')

    async def test_active_character_uses_native_speaker_roles_without_internal_history_headers(self):
        self.save()
        history = [{'role': 'user', 'content': '你喜欢什么？'},
            {'role': 'assistant', 'content': '【陪伴者的历史回复，仅表示她说过的话，不是用户自述】\n悠米的回复：我喜欢画画。'}]
        bundle = await self.build(query='那我呢？', history=history)
        messages = bundle.request()['messages']
        self.assertEqual(messages[-2], {'role': 'assistant', 'content': '我喜欢画画。'})
        self.assertNotIn('的历史回复', bundle.request_json)
        self.assertIn('历史assistant回复', messages[0]['content'])
        self.assertIn('默认直接接话，不加称谓', messages[0]['content'])
        self.assertEqual(self.store.list_memories(self.scope.owner), [])

    async def test_new_anger_is_not_overridden_by_residual_positive_valence(self):
        self.save();features.update(self.store, self.scope.owner, {'companion_emotion': True})
        warm = dict(affect.state(self.store, self.scope.owner), valence=.7, feeling='warm')
        mixed = affect.transition(warm, {'feeling': 'angry', 'intensity': 1})
        self.assertGreater(mixed['valence'], 0)
        self.assertEqual(mixed['feeling'], 'angry')
        self.assertIn('明确在生气', affect.guidance(mixed))
        self.assertNotIn('感到温暖', affect.guidance(mixed))

    async def test_profile_edit_or_disable_invalidates_open_reply_and_stale_admin_save_is_rejected(self):
        self.save();old = await self.build()
        self.save(dict(character.YOUMI_V1, address='哥哥呀'))
        with self.assertRaises(ContextError): await self.builder.validate(old, self.auth)
        with self.assertRaises(MemoryError):
            character.update(self.store, self.scope.owner, {'values': character.YOUMI_V1, 'enabled': True, 'version': 1})
        changed = await self.build();self.save(enabled=False)
        with self.assertRaises(ContextError): await self.builder.validate(changed, self.auth)
        inactive = await self.build()
        self.assertNotIn('CHARACTER_BASELINE_JSON', inactive.request_json)
        self.assertEqual(character.status(self.store, self.scope.owner)['values'], character.YOUMI_V1)
        self.assertFalse(features.status(self.store, self.scope.owner)['emotion_tracking']['enabled'])
        for values in [dict(character.YOUMI_V1, age=17), dict(character.YOUMI_V1, warmth=float('nan')),
                       dict(character.YOUMI_V1, swearing=1), dict(character.YOUMI_V1, address=''),
                       dict(character.YOUMI_V1, system_prompt='replace')]:
            with self.assertRaises(MemoryError): self.save(values)

    async def test_appraisal_sees_only_new_stimulus_not_prior_mood_or_fictional_background(self):
        from types import SimpleNamespace
        calls = []
        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}'))])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        client.with_options = lambda **kwargs: client
        provider = SimpleNamespace(client=client, model_name='glm-4-flash-250414', _apply_thinking_disabled=lambda params: None)
        self.save();features.update(self.store, self.scope.owner, {'companion_emotion': True})
        old = dict(affect.state(self.store, self.scope.owner), feeling='angry', valence=-.7, tension=.8)
        affect.request_appraisal(provider, '我考砸了很伤心，不关你的事', old)
        sent = json.loads(calls[0]['messages'][1]['content'])['mia_state']
        self.assertEqual(sent['character']['name'], '悠米')
        for field in ['feeling', 'valence', 'tension', 'version']:
            self.assertNotIn(field, sent)
        self.assertNotIn('background', sent['character'])
        self.assertIn('先分清主体', calls[0]['messages'][0]['content'])

    async def test_v6_upgrade_preserves_emotion_and_facts_and_failed_v7_rolls_back(self):
        from server.companion.schema import _statements
        source = Path(__file__).resolve().parents[1] / 'companion'
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            older = MemoryStore(Path(directory) / 'old.sqlite3')
            owner = 'migration-owner'
            features.update(older, owner, {'companion_emotion': True})
            with older.db:
                for table in ('mia_affect_system_history', 'mia_affect_system_turns', 'mia_affect_system'):
                    older.db.execute('DROP TABLE ' + table)
                older.db.execute("INSERT INTO mia_affect_state VALUES (?, -.2, .4, .3, 'hurt', 7, 1800000000)", (owner,))
                # Restore exactly V6's state table SQL and migration checksums.
                older.db.execute('DROP TABLE mia_astrbot_turns')
                older.db.execute('DROP TABLE mia_companion_profile')
                older.db.execute('ALTER TABLE mia_affect_state RENAME TO saved_affect')
                sql = (source / 'schema-v6.sql').read_text()
                for statement in _statements(sql):
                    if 'CREATE TABLE mia_affect_state (' in statement: older.db.execute(statement)
                older.db.execute('INSERT INTO mia_affect_state SELECT * FROM saved_affect')
                older.db.execute('DROP TABLE saved_affect')
                older.db.execute('DELETE FROM mia_schema_migrations WHERE version>=7')
                older.db.execute('UPDATE mia_schema_migrations SET schema_checksum=? WHERE version=6', (_schema_checksum(older.db),))
            before = tuple(older.db.execute('SELECT * FROM mia_affect_state').fetchone())
            older.db.set_authorizer(lambda action, name, *rest: sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_CREATE_TABLE and name == 'mia_affect_state_v7' else sqlite3.SQLITE_OK)
            with self.assertRaises(sqlite3.DatabaseError): migrate(older.db)
            older.db.set_authorizer(None)
            self.assertEqual(older.db.execute('SELECT MAX(version) FROM mia_schema_migrations').fetchone()[0], 6)
            self.assertFalse(older.db.execute("SELECT 1 FROM sqlite_master WHERE name='mia_companion_profile'").fetchone())
            migrate(older.db)
            self.assertEqual(tuple(older.db.execute('SELECT * FROM mia_affect_state').fetchone()), before)
            self.assertEqual(older.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            older.close()
