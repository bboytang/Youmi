"""Real-store per-agent activation, in-flight protection and same-schema rollback."""

from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import subprocess
import sqlite3
import unittest
from unittest.mock import patch

from server.companion import emotional_system, memory_engine
from server.companion.context import ContextBuilder, ContextError
from server.companion.embedding import EmbeddingUnavailable
from server.companion.jobs import JobStore, TurnCompleted
from server.companion.memory import MemoryError, MemoryStore
from server.companion.memory_runtime import YoumiMemoryRuntime
from server.companion.semantic import SemanticMemoryIndex
from server.tests import test_companion_context as fixture
from server.tests.companion_schema_fixtures import remove_v18
from server.companion.schema import migrate


class MemoryEngineTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.ContextBuilderTests.setUp
    tearDown = fixture.ContextBuilderTests.tearDown
    fact = fixture.ContextBuilderTests.fact

    def select(self, engine, version=0, owner=None):
        return memory_engine.configure(self.store, owner or self.scope.owner,
            dict(action='engine', engine=engine, version=version))

    async def test_default_read_keeps_exact_legacy_results_without_cache_or_settings_writes(self):
        self.fact('我喜欢热茶')
        await self.index.rebuild_batch(self.scope, self.auth)
        before = list(self.store.db.iterdump())
        self.assertEqual(await YoumiMemoryRuntime(self.index).retrieve(self.scope, self.auth, '热茶'),
            await self.index.retrieve(self.scope, self.auth, '热茶'))
        self.assertEqual(memory_engine.status(self.store, self.scope.owner), dict(engine='legacy', version=0))
        self.assertEqual(list(self.store.db.iterdump()), before)
        self.assertEqual(self.store.cache_cleanup_status(self.scope.owner)['state'], 'not_registered')

    @unittest.skipUnless(importlib.util.find_spec('tantivy'), 'Optional native dependency')
    async def test_same_agent_endpoints_share_managed_engine_but_another_agent_stays_legacy(self):
        own = self.fact('我喜欢热茶')
        foreign = self.fact('我喜欢咖啡', owner='user:1:agent:b')
        peer = replace(self.scope, owner='user:1:agent:b', agent_id='b')
        await self.index.rebuild_batch(self.scope, self.auth)
        await self.index.rebuild_batch(peer, self.auth)
        runtime = YoumiMemoryRuntime(self.index)
        self.select('youmi')
        for device in ('app', 'device', 'astrbot'):
            current = replace(self.scope, device_id=device, client_id=device)
            result = await runtime.retrieve(current, self.auth, '热茶')
            self.assertFalse(result['shadow_only'])
            self.assertEqual([row['id'] for row in result['results']], [own])
            self.assertIsNotNone(result['results'][0]['source'])
        self.assertEqual(self.store.cache_cleanup_status(self.scope.owner)['state'], 'complete')
        self.assertEqual(memory_engine.status(self.store, peer.owner), dict(engine='legacy', version=0))
        other = await runtime.retrieve(peer, self.auth, '咖啡')
        self.assertNotIn('shadow_only', other)
        self.assertEqual([row['id'] for row in other['results']], [foreign])

    @unittest.skipUnless(importlib.util.find_spec('tantivy'), 'Optional native dependency')
    async def test_compatible_rollback_keeps_new_facts_latest_emotion_and_restart_selection(self):
        self.store.clock = lambda: 1800000000
        self.fact('我喜欢热茶')
        self.select('youmi')
        newest = self.fact('迁移后新增：我喜欢罗勒')
        state = emotional_system.status(self.store, self.scope.owner)
        emotional_system.manage(self.store, self.scope.owner, dict(action='configure', version=state['version'],
            enabled=True, dimensions=state['dimensions']))
        state = emotional_system.status(self.store, self.scope.owner)
        emotional_system.manage(self.store, self.scope.owner, dict(action='adjust', version=state['version'], values=dict(joy=42)))
        await self.index.rebuild_batch(self.scope, self.auth)
        result = await YoumiMemoryRuntime(self.index).retrieve(self.scope, self.auth, '罗勒')
        self.assertIn(newest, [row['id'] for row in result['results']])
        self.select('legacy', version=1)
        restored = MemoryStore(self.temp.name + '/memory.sqlite3', clock=lambda: 1800000000)
        try:
            self.assertEqual(memory_engine.status(restored, self.scope.owner), dict(engine='legacy', version=2))
            result = await YoumiMemoryRuntime(SemanticMemoryIndex(restored, self.encoder)).retrieve(self.scope, self.auth, '罗勒')
            self.assertIn(newest, [row['id'] for row in result['results']])
            self.assertEqual(emotional_system.status(restored, self.scope.owner)['values']['joy'], 42)
            self.assertEqual(restored.db.execute('PRAGMA foreign_key_check').fetchall(), [])
        finally:
            restored.close()

    async def test_strict_version_conflict_and_same_selection_are_read_only(self):
        self.select('youmi')
        before = list(self.store.db.iterdump())
        self.assertEqual(self.select('youmi', 1), dict(engine='youmi', version=1))
        with self.assertRaises(MemoryError):
            self.select('legacy', 0)
        for command in ({}, dict(action='engine', engine='other', version=1),
                dict(action='engine', engine='legacy', version=True),
                dict(action='engine', engine='legacy', version=1, owner='foreign')):
            with self.assertRaises(MemoryError):
                memory_engine.configure(self.store, self.scope.owner, command)
        self.assertEqual(list(self.store.db.iterdump()), before)

    async def test_active_job_and_late_native_request_each_block_switch_until_terminal(self):
        jobs = JobStore(self.store)
        admitted = jobs.submit(TurnCompleted(self.scope, 'session', 'turn', 'message', '我喜欢热茶', 0))
        with self.assertRaisesRegex(MemoryError, 'active turns'):
            self.select('youmi')
        with self.store.db:
            self.store.db.execute('INSERT INTO mia_model_requests(job_id,ordinal,waiting_at,slot_acquired_at,rechecked_at,intent_at,outcome) VALUES (?,1,1,1,1,1,?)', (admitted['id'], 'running'))
        jobs.update(jobs.current(admitted), 'failed', error='synthetic')
        with self.assertRaisesRegex(MemoryError, 'active turns'):
            self.select('youmi')
        with self.store.db:
            self.store.db.execute('UPDATE mia_model_requests SET native_finished_at=2 WHERE job_id=?', (admitted['id'],))
        self.assertEqual(self.select('youmi')['engine'], 'youmi')

    async def test_qq_turn_blocks_its_owner_but_not_another_agent(self):
        self.store.clock = lambda: 1800000000
        with self.store.db:
            self.store.db.execute('INSERT INTO mia_astrbot_turns(turn_key,token,attempt_id,request_hash,session_id,scope_json,status,created_at,expires_at) '
                "VALUES ('turn','synthetic-token','attempt','hash','session',?,'sending',1800000000,1800000100)",
                (json.dumps(self.scope.snapshot()),))
        with self.assertRaisesRegex(MemoryError, 'active turns'):
            self.select('youmi')
        self.assertEqual(self.select('youmi', owner='user:1:agent:b')['engine'], 'youmi')
        with self.store.db:
            self.store.db.execute("UPDATE mia_astrbot_turns SET status='completed'")
        self.assertEqual(self.select('youmi')['engine'], 'youmi')

    async def test_switch_invalidates_prepared_native_context(self):
        builder = ContextBuilder(YoumiMemoryRuntime(self.index))
        bundle = await builder.build(self.scope, self.auth, 'prepared', '你好', limits=self.limits)
        self.select('youmi')
        with self.assertRaisesRegex(ContextError, 'context_changed'):
            await builder.validate(bundle, self.auth)

    async def test_owner_revision_failure_rolls_back_engine_setting(self):
        self.store.snapshot(self.scope.owner)
        before = list(self.store.db.iterdump())
        self.store.db.set_authorizer(lambda action, name, *args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_UPDATE and name == 'owners' else sqlite3.SQLITE_OK)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.select('youmi')
        finally:
            self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(list(self.store.db.iterdump()), before)


class EngineMigrationTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.ContextBuilderTests.setUp
    tearDown = fixture.ContextBuilderTests.tearDown

    async def test_v17_upgrade_failure_retries_and_frozen_loader_rejects_new_schema(self):
        with self.store.db:
            remove_v18(self.store.db)
            self.store.db.execute('DELETE FROM mia_schema_migrations WHERE version>=18')
        namespace = {'__file__': str(Path('server/companion/schema.py').resolve())}
        source = subprocess.check_output(['git', 'show', '08a99335:server/companion/schema.py'], text=True)
        exec(compile(source, 'frozen-v17.py', 'exec'), namespace)
        namespace['migrate'](self.store.db)
        before = list(self.store.db.iterdump())
        self.store.db.set_authorizer(lambda action, name, *args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TABLE and name == 'youmi_memory_engine' else sqlite3.SQLITE_OK)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                migrate(self.store.db)
        finally:
            self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(list(self.store.db.iterdump()), before)
        migrate(self.store.db)
        migrate(self.store.db)
        self.assertEqual(memory_engine.status(self.store, self.scope.owner), dict(engine='legacy', version=0))
        with self.assertRaisesRegex(ValueError, 'version or checksum'):
            namespace['migrate'](self.store.db)
