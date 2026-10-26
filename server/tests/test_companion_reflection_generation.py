"""Completion-driven reflection through real SDK/MockTransport and SQLite."""

import asyncio
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from server.companion import emotional_system
from server.companion.extraction import LLMMemoryExtractor, context_for
from server.companion.jobs import JobStore, MemoryWorker
from server.companion.memory import MemoryError, MemoryStore
from server.companion.memory_artifacts import YoumiMemoryArtifacts
from server.companion.memory_reflection import validate_proposal
from server.companion.memory_reflection_policy import YoumiReflectionPolicy, status
from server.companion.schema import migrate
from server.tests import test_companion_request_accounting as sdk
from server.tests import test_companion_jobs as base
from server.tests.companion_schema_fixtures import remove_v13


class ReflectionGenerationTests(unittest.IsolatedAsyncioTestCase):
    setUp = sdk.V2RequestTests.setUp
    tearDown = sdk.V2RequestTests.tearDown
    event = base.JobTests.event
    row = base.JobTests.row
    requests = sdk.V2RequestTests.requests

    def seed(self, text='我喜欢蓝色'):
        result = self.store.apply_extraction(self.scope.owner, text, self.store.snapshot(self.scope.owner)['revision'],
            [text], dict(memories=[dict(category='preference', content=text, source_index=0)]), text)
        return dict(self.store.db.execute('SELECT * FROM memories WHERE id=?', (result['memories'][0],)).fetchone())

    async def enable(self, enabled=True, interval=60):
        return await YoumiReflectionPolicy(self.store).manage(self.scope, self.policy, dict(action='configure',
            enabled=enabled, interval_seconds=interval, version=status(self.store, self.scope.owner)['version']))

    def note(self, fact):
        return dict(reflection_version=1, memory_actions=[dict(action='note', key='color', title='喜欢',
            evidence=[dict(id=fact['id'], quote=fact['content'])])])

    def capture_request(self):
        self.bodies = []
        def response(request):
            self.bodies.append(json.loads(request.content))
            return sdk.sse(self.output)
        self.response = response

    async def run_event(self, marker='one', text='你好'):
        job = await self.worker.submit(self.event(marker, text=text))
        await self.worker.run_once()
        return job

    async def test_disabled_default_keeps_legacy_request_and_does_not_bind_job(self):
        self.capture_request()
        job = await self.run_event(text='我喜欢蓝色')
        self.assertEqual(self.row(job)['state'], 'committed')
        self.assertFalse(status(self.store, self.scope.owner)['enabled'])
        self.assertIn('memories', self.bodies[0]['tools'][0]['function']['parameters']['properties'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_reflection_jobs').fetchone()[0], 0)

    async def test_enabled_fact_uses_reflection_tool_in_one_existing_request(self):
        self.capture_request()
        memory = self.output['memories'][0]
        await self.enable()
        self.output = dict(reflection_version=1, memory_actions=[dict(action='remember', memory=memory)])
        job = await self.run_event(text=memory['content'])
        self.assertEqual(self.row(job)['state'], 'committed')
        self.assertEqual(self.row(job)['attempts'], 1)
        self.assertEqual(len(self.http), 1)
        self.assertEqual(self.requests(job)[0]['http_sends'], 1)
        properties = self.bodies[0]['tools'][0]['function']['parameters']['properties']
        self.assertIn('memory_actions', properties)
        self.assertNotIn('memories', properties)
        payload = json.loads(self.bodies[0]['messages'][1]['content'])
        self.assertFalse(payload['maintenance_due'])
        self.assertNotIn('owner', payload)

    async def test_due_maintenance_on_completed_greeting_uses_no_extra_request(self):
        fact = self.seed()
        await self.enable()
        self.capture_request()
        self.output = self.note(fact)
        job = await self.run_event()
        self.assertEqual(self.row(job)['state'], 'committed')
        self.assertEqual(len(self.http), 1)
        self.assertEqual(len(await YoumiMemoryArtifacts(self.store).list(self.scope, self.policy)), 1)
        self.assertEqual(status(self.store, self.scope.owner)['last_maintenance_at'], 100)
        self.assertTrue(json.loads(self.bodies[0]['messages'][1]['content'])['maintenance_due'])
        self.assertEqual(self.store.db.execute('SELECT maintenance_active FROM youmi_memory_reflection_jobs').fetchone()[0], 0)

    async def test_unchanged_sources_are_not_reorganized_after_interval(self):
        fact = self.seed()
        await self.enable()
        self.output = self.note(fact)
        await self.run_event()
        self.now[0] += 1000
        later = await self.run_event('later')
        self.assertEqual(self.row(later)['state'], 'empty')
        self.assertEqual(len(self.http), 1)
        self.assertEqual(status(self.store, self.scope.owner)['last_maintenance_at'], 100)

    async def test_changed_sources_wait_for_interval_then_next_completed_turn(self):
        fact = self.seed()
        await self.enable()
        self.output = self.note(fact)
        await self.run_event()
        self.store.edit_memory(self.scope.owner, fact['id'], '我喜欢绿色')
        # Editing removes provenance; add a new canonical source for the fixture.
        with self.store.db:
            self.store.db.execute('INSERT INTO sources VALUES (?,?,?,?,?)', (fact['id'], 'green', 'green', '我喜欢绿色', 101))
        self.output = self.note(dict(fact, content='我喜欢绿色'))
        self.now[0] += 10
        self.assertEqual(self.row(await self.run_event('early'))['state'], 'empty')
        self.assertEqual(len(self.http), 1)
        self.now[0] = 161
        later = await self.run_event('due')
        self.assertEqual(self.row(later)['state'], 'committed')
        self.assertEqual(len(self.http), 2)
        self.assertEqual(status(self.store, self.scope.owner)['last_maintenance_at'], 161)

    async def test_only_one_pending_maintenance_claim_and_terminal_releases_it(self):
        self.seed()
        await self.enable()
        first = await self.worker.submit(self.event('first'))
        second = await self.worker.submit(self.event('second'))
        flags = [r[0] for r in self.store.db.execute('SELECT maintenance_due FROM youmi_memory_reflection_jobs ORDER BY rowid')]
        self.assertEqual(flags, [1, 0])
        self.jobs.update(self.row(first), 'failed', error='fixture')
        third = await self.worker.submit(self.event('third'))
        self.assertEqual(self.store.db.execute('SELECT maintenance_due FROM youmi_memory_reflection_jobs WHERE job_id=?',
            (third['id'],)).fetchone()[0], 1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_reflection_jobs WHERE maintenance_active=1').fetchone()[0], 1)
        self.assertEqual(self.row(second)['state'], 'pending')
        self.assertEqual(self.http, [])

    async def test_invalid_reflection_retries_twice_without_advancing_success_time(self):
        self.seed()
        await self.enable()
        self.output = {'reflection_version': 99, 'memory_actions': []}
        job = await self.run_event()
        self.assertEqual(self.row(job)['state'], 'retry_wait')
        self.assertEqual(self.store.db.execute('SELECT maintenance_active FROM youmi_memory_reflection_jobs').fetchone()[0], 1)
        self.now[0] += 1
        await self.worker.run_once()
        self.assertEqual(self.row(job)['state'], 'failed')
        self.assertEqual(len(self.http), 2)
        self.assertEqual(len(self.requests(job)), 2)
        self.assertIsNone(status(self.store, self.scope.owner)['last_maintenance_at'])
        self.assertEqual(self.store.db.execute('SELECT maintenance_active FROM youmi_memory_reflection_jobs').fetchone()[0], 0)

    async def test_configuration_change_cancels_old_jobs_before_sdk(self):
        self.seed()
        await self.enable()
        job = await self.worker.submit(self.event())
        await self.enable(False)
        self.assertFalse(await self.worker.run_once())
        self.assertEqual(self.row(job)['state'], 'cancelled')
        self.assertEqual(self.http, [])
        self.assertEqual(self.store.db.execute('SELECT maintenance_active FROM youmi_memory_reflection_jobs').fetchone()[0], 0)

    async def test_policy_validation_versions_permissions_and_owner_isolation(self):
        policy = YoumiReflectionPolicy(self.store)
        initial = await policy.manage(self.scope, self.policy, {'action': 'status'})
        self.assertEqual(initial['interval_seconds'], 86400)
        for interval in (0, 59, 604801, True, 60.0):
            with self.assertRaises(MemoryError):
                await self.enable(interval=interval)
        enabled = await self.enable()
        with self.assertRaises(MemoryError):
            await policy.manage(self.scope, self.policy, dict(action='configure', enabled=False, interval_seconds=60, version=0))
        self.assertFalse(status(self.store, self.peer.owner)['enabled'])
        self.policy.active = False
        from server.companion.authorization import PrivateAuthorizationRequired
        with self.assertRaises(PrivateAuthorizationRequired):
            await policy.manage(self.scope, self.policy, dict(action='configure', enabled=False, interval_seconds=60, version=enabled['version']))

    async def test_explicit_revision_fastpath_does_not_call_model_or_consume_maintenance(self):
        fact = self.seed()
        await self.enable()
        job = await self.run_event(text='更正：我喜欢蓝色；我喜欢绿色')
        self.assertEqual(self.row(job)['state'], 'committed')
        self.assertEqual(self.http, [])
        self.assertEqual(self.row(job)['attempts'], 0)
        self.assertIsNone(status(self.store, self.scope.owner)['last_maintenance_at'])
        self.assertEqual(self.store.db.execute('SELECT content FROM memories WHERE id=?', (fact['id'],)).fetchone()[0], '我喜欢绿色')

    async def test_not_due_tasks_cannot_submit_maintenance_actions(self):
        await self.enable()
        self.output = self.note(dict(id='forged', content='我喜欢蓝色'))
        job = await self.run_event(text='我喜欢蓝色')
        self.assertEqual(self.row(job)['state'], 'retry_wait')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_artifacts').fetchone()[0], 0)

    async def test_sdk_recheck_rejects_changed_source_and_releases_claim(self):
        fact = self.seed()
        await self.enable()
        self.output = self.note(fact)
        def change():
            with self.store.db:
                self.store.db.execute('INSERT INTO sources VALUES (?,?,?,?,?)', (fact['id'], 'other', 'other', fact['content'], 101))
        self.sdk_entry = change
        job = await self.run_event()
        self.assertEqual(self.row(job)['state'], 'cancelled')
        self.assertEqual(self.http, [])
        self.assertIsNone(status(self.store, self.scope.owner)['last_maintenance_at'])

    async def test_database_retry_reopens_without_repeating_sdk_or_losing_success_clock(self):
        fact = self.seed()
        await self.enable()
        self.output = self.note(fact)
        job = await self.worker.submit(self.event(text='你好'))
        self.store.db.set_authorizer(lambda action, table, *args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_INSERT and table == 'youmi_memory_artifact_evidence' else sqlite3.SQLITE_OK)
        try:
            await self.worker.run_once()
        finally:
            self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(self.row(job)['state'], 'retry_wait')
        self.assertIn('_maintenance_signature', json.loads(self.row(job)['validated_result']))
        self.assertIsNone(status(self.store, self.scope.owner)['last_maintenance_at'])
        self.store.close()
        self.store = MemoryStore(self.path, clock=lambda: self.now[0])
        self.jobs.store = self.store
        self.worker = MemoryWorker(self.jobs, self.policy, self.resolve, encoder=self.encoder)
        self.now[0] += 1
        await self.worker.run_once()
        self.assertEqual(self.row(job)['state'], 'committed')
        self.assertEqual(len(self.http), 1)
        self.assertEqual(status(self.store, self.scope.owner)['last_maintenance_at'], 101)

    async def test_plain_json_adapter_uses_same_reflection_contract(self):
        fact = self.seed()
        context = context_for(self.store, self.scope.owner)
        context.reflection = dict(version=1, maintenance_due=True)
        provider = SimpleNamespace(response_no_stream=Mock(return_value=json.dumps(self.note(fact))))
        result = LLMMemoryExtractor(lambda: provider)(['你好'], context)
        self.assertTrue(context.maintenance_generated)
        self.assertEqual(len(validate_proposal(result, ['你好'], context)['artifacts']), 1)
        self.assertEqual(provider.response_no_stream.call_count, 1)

    async def test_enabled_policy_rejects_legacy_model_output(self):
        self.seed()
        await self.enable()
        self.output = {'memories': []}
        job = await self.run_event()
        self.assertEqual(self.row(job)['state'], 'retry_wait')
        self.assertIsNone(status(self.store, self.scope.owner)['last_maintenance_at'])

    async def test_clear_keeps_policy_but_resets_execution_history(self):
        fact = self.seed()
        await self.enable()
        self.output = self.note(fact)
        await self.run_event()
        self.store.clear(self.scope.owner)
        cfg = status(self.store, self.scope.owner)
        self.assertTrue(cfg['enabled'])
        self.assertEqual(cfg['interval_seconds'], 60)
        self.assertIsNone(cfg['last_maintenance_at'])
        self.assertIsNone(cfg['last_signature'])

    async def test_unsourced_console_edits_do_not_trigger_maintenance_calls(self):
        fact = self.seed()
        self.store.edit_memory(self.scope.owner, fact['id'], '我喜欢绿色')
        await self.enable()
        job = await self.run_event()
        self.assertEqual(self.row(job)['state'], 'empty')
        self.assertEqual(self.http, [])
        self.assertIsNone(status(self.store, self.scope.owner)['last_maintenance_at'])

    async def test_reflection_configuration_and_generation_preserve_all_51_emotions(self):
        fact = self.seed()
        current = emotional_system.status(self.store, self.scope.owner)
        emotional_system.manage(self.store, self.scope.owner, dict(action='configure', enabled=True,
            version=0, dimensions=current['dimensions']))
        before = emotional_system.status(self.store, self.scope.owner)
        await self.enable()
        self.output = self.note(fact)
        await self.run_event()
        self.assertEqual(emotional_system.status(self.store, self.scope.owner), before)
        self.assertEqual(len(before['values']), 51)


class ReflectionMigrationTests(unittest.IsolatedAsyncioTestCase):
    setUp = base.JobTests.setUp
    tearDown = base.JobTests.tearDown

    def v12(self):
        with self.store.db:
            remove_v13(self.store.db)
            self.store.db.execute('DELETE FROM mia_schema_migrations WHERE version>=13')
        namespace = {'__file__': str(Path('server/companion/schema.py').resolve())}
        code = subprocess.check_output(['git', 'show', 'b07bb764:server/companion/schema.py'], text=True)
        exec(compile(code, 'frozen-v12.py', 'exec'), namespace)
        namespace['migrate'](self.store.db)
        return namespace['migrate']

    async def test_v12_upgrade_failure_is_atomic_and_rejects_old_loader_after_upgrade(self):
        old = self.v12()
        before = list(self.store.db.iterdump())
        self.store.db.set_authorizer(lambda action, name, *args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TABLE and name == 'youmi_memory_reflection_jobs' else sqlite3.SQLITE_OK)
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

    async def test_process_exit_during_v13_leaves_v12_reopenable(self):
        old = self.v12()
        code = '''import os,sqlite3,sys
from server.companion.schema import migrate
db=sqlite3.connect(sys.argv[1])
db.execute('PRAGMA foreign_keys=ON')
def interrupt(action,name,*args):
    if action==sqlite3.SQLITE_CREATE_TABLE and name=='youmi_memory_reflection_jobs': os._exit(77)
    return sqlite3.SQLITE_OK
db.set_authorizer(interrupt)
migrate(db)
'''
        result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', code, str(self.path)], capture_output=True)
        self.assertEqual(result.returncode, 77)
        old(self.store.db)
        migrate(self.store.db)
        self.assertEqual(self.store.db.execute('SELECT MAX(version) FROM mia_schema_migrations').fetchone()[0], 17)
