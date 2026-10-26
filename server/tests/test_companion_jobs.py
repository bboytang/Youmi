"""Durability, bounded native work, fairness and lifecycle; no live model calls."""

import asyncio
import numpy as np
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import sys
import uuid
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from server.companion.authorization import AuthenticatedScope, PrivateAuthorizationRequired
from server.companion.jobs import JobStore, MemoryWorker, TurnCompleted
from server.companion.memory import MemoryStore, MemoryError
from server.companion.extraction import LLMMemoryExtractor, validate_output
from server.companion.embedding import DIMENSION, MODEL_KEY, REVISION


class UnitEncoder:
    model_id, revision, dimension = MODEL_KEY, REVISION, DIMENSION

    def encode(self, text, query=False):
        result = np.zeros(DIMENSION, dtype=np.float32)
        result[0] = 1
        return result


class Policy:
    def __init__(self, scopes):
        self.scopes = {scope.owner: scope for scope in scopes}
        self.active = True

    async def require(self, scope, permission):
        if not self.active or self.scopes.get(scope.owner) != scope or permission not in scope.permissions:
            raise PrivateAuthorizationRequired()

    async def restore(self, snapshot):
        scope = self.scopes.get(snapshot['owner'])
        if not self.active or scope is None or scope.snapshot() != snapshot:
            raise PrivateAuthorizationRequired()
        return scope


class JobTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'memory.sqlite3'
        self.now = [100]
        self.store = MemoryStore(self.path, clock=lambda: self.now[0])
        self.scope = AuthenticatedScope('user:1:agent:one', 1, 1, 'one', 'mia', 'device', 'client', 0, 1,
            datetime.now(timezone.utc) + timedelta(days=1), frozenset({'memory:read', 'memory:write', 'memory:manage'}))
        self.peer = replace(self.scope, owner='user:2:agent:two', user_id=2, operator_id=2, agent_id='two')
        self.policy = Policy([self.scope, self.peer])
        self.jobs = JobStore(self.store, policy_version=1)
        self.calls = []
        self.encoder = UnitEncoder()
        self.output = {'memories': [{'category': 'preference', 'content': '我喜欢蓝色', 'source_index': 0,
            'source_quote': '我喜欢蓝色', 'kind': 'explicit', 'subject': 'user', 'value': 'durable'}]}
        async def resolve(scope):
            def extract(sources, context):
                self.calls.append((scope.owner, sources))
                return self.output
            return extract
        self.resolve = resolve
        self.worker = MemoryWorker(self.jobs, self.policy, self.resolve, encoder=self.encoder)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def event(self, marker='one', scope=None, **kwargs):
        scope = scope or self.scope
        revision = self.store.snapshot(scope.owner)['revision']
        return TurnCompleted(scope, 'session', marker, marker, kwargs.get('text', '我喜欢蓝色'), revision,
                             kwargs.get('priority', False))

    def row(self, result):
        return self.jobs.current({'id': result['id']})

    async def test_timed_out_queued_extraction_never_starts_sdk_after_slot_release(self):
        from server.companion.model_requests import ModelRequestGate
        gate = ModelRequestGate()
        leases = [gate.acquire(foreground=True), gate.acquire(foreground=True)]
        provider = SimpleNamespace(response_no_stream=Mock(return_value='{"memories":[]}'),
            client=SimpleNamespace(close=Mock()))
        async def resolve(scope):
            return LLMMemoryExtractor(lambda: provider)
        self.worker.resolve = resolve
        self.worker.extraction_timeout = .03
        with patch('server.companion.extraction.model_requests', gate):
            try:
                admitted = await self.worker.submit(self.event())
                await self.worker.run_once()
                self.assertEqual(self.row(admitted)['state'], 'retry_wait')
                self.assertEqual(self.row(admitted)['attempts'], 1)
                self.assertEqual(self.row(admitted)['error_category'], 'model_queue_timeout')
            finally:
                for lease in leases:
                    lease.release()
            deadline = time.monotonic() + 2
            while not self.worker.lane.done.is_set():
                self.assertLess(time.monotonic(), deadline)
                await asyncio.sleep(.01)
        provider.response_no_stream.assert_not_called()
        provider.client.close.assert_called_once()
        self.assertEqual(gate.snapshot()['active'], 0)

    async def test_outer_timeout_retains_slot_until_actual_model_call_finishes(self):
        from server.companion.model_requests import ModelRequestGate
        gate = ModelRequestGate()
        release, entered = threading.Event(), threading.Event()
        def response(*args):
            entered.set()
            release.wait(2)
            return '{"memories":[]}'
        provider = SimpleNamespace(response_no_stream=response, client=SimpleNamespace(close=Mock()))
        async def resolve(scope):
            return LLMMemoryExtractor(lambda: provider)
        self.worker.resolve = resolve
        self.worker.extraction_timeout = .03
        original_wait_for = asyncio.wait_for
        async def wait_after_sdk_entry(future, timeout):
            if timeout == self.worker.extraction_timeout:
                # This case exercises timeout after SDK entry, not a deadline
                # that may expire while a loaded runner prepares the request.
                deadline = time.monotonic() + 1
                while not entered.is_set():
                    self.assertLess(time.monotonic(), deadline, 'Synthetic SDK did not start')
                    await asyncio.sleep(.001)
            return await original_wait_for(future, timeout)
        with patch('server.companion.extraction.model_requests', gate), patch(
                'server.companion.jobs.asyncio.wait_for', side_effect=wait_after_sdk_entry):
            try:
                admitted = await self.worker.submit(self.event())
                await self.worker.run_once()
                self.assertEqual(gate.snapshot()['active'], 1)
                self.assertEqual(self.row(admitted)['error_category'], 'timeout')
                provider.client.close.assert_not_called()
            finally:
                release.set()
            deadline = time.monotonic() + 2
            while not self.worker.lane.done.is_set():
                self.assertLess(time.monotonic(), deadline)
                await asyncio.sleep(.01)
        provider.client.close.assert_called_once()
        self.assertEqual(gate.snapshot()['active'], 0)

    async def test_admission_is_durable_not_saved_and_duplicates_share_one_job(self):
        event = self.event()
        result = await self.worker.submit(event)
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.list_memories(self.scope.owner), [])
        self.assertEqual((await self.worker.submit(event))['status'], 'duplicate')
        await self.worker.run_once()
        self.assertEqual(self.row(result)['state'], 'committed')
        self.assertEqual(self.row(result)['source_payload'], '{}')
        self.assertIsNone(self.row(result)['validated_result'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.store.list_memories(self.scope.owner)), 1)
        await self.worker.submit(event)
        self.assertFalse(await self.worker.run_once())

    async def test_capacity_rejects_explicitly_and_duplicate_still_works_when_full(self):
        self.jobs.capacity = 2
        event = self.event('one')
        await self.worker.submit(event)
        await self.worker.submit(self.event('two'))
        result = await self.worker.submit(self.event('three'))
        self.assertEqual(result, {'status': 'queue_full', 'accepted': False})
        self.assertEqual((await self.worker.submit(event))['status'], 'duplicate')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM mia_jobs').fetchone()[0], 2)

    async def test_restart_restores_extraction_and_keeps_attempt_count(self):
        result = await self.worker.submit(self.event())
        self.jobs.update(self.row(result), 'extracting', attempt=True)
        self.store.close()
        self.store = MemoryStore(self.path, clock=lambda: self.now[0])
        self.jobs = JobStore(self.store, policy_version=1)
        self.jobs.recover()
        worker = MemoryWorker(self.jobs, self.policy, self.resolve, encoder=self.encoder)
        await worker.run_once()
        self.assertEqual(self.row(result)['state'], 'committed')
        self.assertEqual(self.row(result)['attempts'], 2)

    async def test_validated_restart_skips_model_and_commits_once(self):
        result = await self.worker.submit(self.event())
        self.jobs.update(self.row(result), 'validated', result=json.dumps(validate_output(self.output, ['我喜欢蓝色'], [])))
        self.store.close()
        self.store = MemoryStore(self.path, clock=lambda: self.now[0])
        self.jobs = JobStore(self.store, policy_version=1)
        self.jobs.recover()
        await MemoryWorker(self.jobs, self.policy, self.resolve, encoder=self.encoder).run_once()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.row(result)['state'], 'committed')

    async def test_database_retry_uses_validated_result_without_second_model_call(self):
        result = await self.worker.submit(self.event())
        with patch.object(self.jobs, 'commit', side_effect=sqlite3.OperationalError('fixture')):
            await self.worker.run_once()
        self.assertEqual(self.row(result)['state'], 'retry_wait')
        self.assertIsNotNone(self.row(result)['validated_result'])
        self.now[0] += 2
        await self.worker.run_once()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.row(result)['state'], 'committed')

    async def test_validation_persistence_failure_keeps_result_for_database_retry(self):
        result = await self.worker.submit(self.event())
        update = self.jobs.update
        def fail_validated(job, state, **kwargs):
            if state == 'validated':
                raise sqlite3.OperationalError('fixture')
            return update(job, state, **kwargs)
        with patch.object(self.jobs, 'update', side_effect=fail_validated):
            await self.worker.run_once()
        self.now[0] += 2
        await self.worker.run_once()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.row(result)['state'], 'committed')

    async def test_illegal_output_retries_twice_but_valid_empty_is_terminal(self):
        self.output = 'not json'
        result = await self.worker.submit(self.event())
        await self.worker.run_once()
        self.assertEqual(self.row(result)['error_category'], 'parse')
        self.now[0] += 2
        await self.worker.run_once()
        self.assertEqual(self.row(result)['state'], 'failed')
        self.assertEqual(len(self.calls), 2)
        self.output = {'memories': [], 'trait': None}
        empty = await self.worker.submit(self.event('empty'))
        await self.worker.run_once()
        self.assertEqual(self.row(empty)['state'], 'empty')

    async def test_missing_source_index_is_invalid_not_empty_or_cross_source(self):
        self.output['memories'][0]['source_index'] = 1
        result = await self.worker.submit(self.event())
        await self.worker.run_once()
        self.assertEqual(self.row(result)['error_category'], 'source_semantic')
        self.assertEqual(self.store.list_memories(self.scope.owner), [])

    async def test_real_validation_feedback_is_task_local_and_keeps_two_attempt_limit(self):
        bad = json.loads(json.dumps(self.output))
        bad['memories'][0]['keywords'] = ['not in actual source']
        original = json.dumps(bad)
        provider = Mock()
        provider.response_no_stream.side_effect = [bad, self.output, self.output]
        resolved_owners = []
        async def resolve(scope):
            resolved_owners.append(scope.owner)
            return LLMMemoryExtractor(lambda: provider)
        self.worker.resolve = resolve
        own = await self.worker.submit(self.event('feedback'))
        await self.worker.run_once()
        self.assertEqual(self.row(own)['state'], 'retry_wait')
        self.assertEqual(self.row(own)['attempts'], 1)
        self.assertEqual(self.store.list_memories(self.scope.owner), [])
        # The native loop polls while retry_wait is not due; diagnostic state
        # must survive those polls, not only a manually advanced clock.
        for _ in range(3):
            self.assertFalse(await self.worker.run_once())
        self.assertIn(own['id'], self.worker.validation_feedback)
        peer = await self.worker.submit(self.event('other-task', scope=self.peer))
        self.now[0] += 2
        await self.worker.run_once()
        await self.worker.run_once()
        self.assertEqual(self.row(peer)['state'], 'committed')
        self.assertEqual(self.row(peer)['attempts'], 1)
        self.assertEqual(self.row(own)['state'], 'committed')
        self.assertEqual(self.row(own)['attempts'], 2)
        calls = provider.response_no_stream.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertNotIn('previous attempt for THIS task failed', calls[0].args[0])
        for owner, call in zip(resolved_owners[1:], calls[1:]):
            payload = json.loads(call.args[1])
            if owner == self.scope.owner:
                self.assertIn('keywords clue must occur as a contiguous literal substring', call.args[0])
                self.assertEqual(payload['validation_feedback']['rejected_clues'], {'keywords': ['not in actual source']})
            else:
                self.assertEqual(owner, self.peer.owner)
                self.assertNotIn('previous attempt for THIS task failed', call.args[0])
                self.assertNotIn('validation_feedback', payload)
            self.assertEqual(payload['user_excerpts'], ['我喜欢蓝色'])
        self.assertEqual(json.dumps(bad), original)
        self.assertEqual(len(self.store.list_memories(self.scope.owner)), 1)
        self.assertEqual(len(self.store.list_memories(self.peer.owner)), 1)
        self.assertNotIn(own['id'], self.worker.validation_feedback)

    async def test_native_tool_and_parse_failures_get_feedback_before_exactly_one_retry(self):
        from server.companion.proposal import NAME
        for category in ('tool_contract', 'parse'):
            with self.subTest(category=category):
                payloads = []
                output = json.dumps(self.output)
                class Provider:
                    def response_with_functions(self, session, messages, functions):
                        payloads.append(json.loads(messages[1]['content']))
                        if len(payloads) == 1 and category == 'parse':
                            yield 'not JSON', None
                        else:
                            name = 'wrong_function' if len(payloads) == 1 else NAME
                            yield None, [SimpleNamespace(index=0, id='one',
                                function=SimpleNamespace(name=name, arguments=output))]
                async def resolve(scope):
                    return LLMMemoryExtractor(Provider)
                self.worker.resolve = resolve
                with patch('server.companion.proposal.provider_capabilities', return_value={'native_tools': True}):
                    admitted = await self.worker.submit(self.event(category))
                    await self.worker.run_once()
                    self.assertEqual(self.row(admitted)['error_category'], category)
                    self.assertEqual(self.row(admitted)['attempts'], 1)
                    self.assertNotIn('validation_feedback', payloads[0])
                    self.now[0] += 2
                    await self.worker.run_once()
                self.assertEqual(payloads[1]['validation_feedback']['category'], category)
                self.assertEqual(payloads[1]['user_excerpts'], ['我喜欢蓝色'])
                self.assertEqual(len(payloads), 2)
                self.assertEqual((self.row(admitted)['state'], self.row(admitted)['attempts']), ('committed', 2))
                self.assertEqual(self.row(admitted)['deadline'] - self.row(admitted)['created_at'], 120)
                self.assertNotIn(admitted['id'], self.worker.validation_feedback)

    async def test_typed_gate_errors_keep_their_category_and_original_attempt_semantics(self):
        from server.companion.model_requests import ModelQueueError
        for reason, category in [('model_request_queue_full', 'model_queue_full'),
                ('model_request_cancelled', 'cancelled'), ('model_request_queue_timeout', 'model_queue_timeout')]:
            with self.subTest(reason=reason):
                def extract(sources, context):
                    raise ModelQueueError(reason)
                async def resolve(scope):
                    return extract
                self.worker.resolve = resolve
                admitted = await self.worker.submit(self.event(reason))
                await self.worker.run_once()
                row = self.row(admitted)
                self.assertEqual((row['state'], row['attempts'], row['error_category']), ('retry_wait', 1, category))
                self.assertNotIn(admitted['id'], self.worker.validation_feedback)

    async def test_provider_failure_is_not_model_feedback_and_keeps_two_attempt_boundary(self):
        calls = []
        class Provider:
            def response_no_stream(self, *args):
                calls.append(True)
                raise RuntimeError('sk-synthetic-private-body')
        async def resolve(scope):
            return LLMMemoryExtractor(Provider)
        self.worker.resolve = resolve
        admitted = await self.worker.submit(self.event('provider-error'))
        await self.worker.run_once()
        self.assertEqual(self.row(admitted)['error_category'], 'provider')
        self.assertNotIn(admitted['id'], self.worker.validation_feedback)
        self.now[0] += 2
        await self.worker.run_once()
        self.assertEqual((self.row(admitted)['state'], self.row(admitted)['attempts']), ('failed', 2))
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.row(admitted)['source_payload'], '{}')
        self.assertEqual(self.store.list_memories(self.scope.owner), [])

    async def test_delete_clear_pause_edit_and_reset_cancel_without_resurrection(self):
        for action in ('delete', 'clear', 'pause', 'update', 'reset_personality'):
            with self.subTest(action=action):
                self.store.set_paused(self.scope.owner, False)
                fact = self.store.apply_extraction(self.scope.owner, 's', self.store.snapshot(self.scope.owner)['revision'],
                    ['我喜欢蓝色'], self.output, action)['memories'][0]
                result = await self.worker.submit(self.event(action))
                request = {'action': action, 'id': fact, 'content': '我喜欢绿色', 'paused': True}
                self.store.handle(self.scope.owner, request)
                await self.worker.run_once()
                self.assertEqual(self.row(result)['state'], 'cancelled')
                self.assertEqual(self.row(result)['source_payload'], '{}')
        self.assertEqual(self.calls, [])

    async def test_revision_at_completion_and_foreign_scope_fail_before_model(self):
        event = self.event()
        self.store.clear(self.scope.owner)
        self.assertEqual((await self.worker.submit(event))['status'], 'cancelled')
        forged = replace(self.event(), scope=replace(self.scope, owner=self.peer.owner))
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.worker.submit(forged)
        result = await self.worker.submit(self.event('revoked'))
        self.policy.active = False
        await self.worker.run_once()
        self.assertEqual(self.row(result)['state'], 'cancelled')
        self.assertEqual(self.calls, [])

    async def test_scope_fairness_and_foreground_priority(self):
        for i in range(4):
            await self.worker.submit(self.event(str(i)))
        await self.worker.submit(self.event('peer', self.peer))
        self.worker.foreground = 1
        self.assertFalse(await self.worker.run_once())
        self.assertEqual(self.calls, [])
        self.worker.foreground = 0
        await self.worker.run_once()
        await self.worker.run_once()
        self.assertEqual([row[0] for row in self.calls[:2]], [self.scope.owner, self.peer.owner])
        self.assertEqual(self.worker.queue.maxlen, 128)

    async def test_hung_model_timeout_does_not_spawn_more_native_calls_or_block_shutdown(self):
        started, release = threading.Event(), threading.Event()
        def hung(sources, context):
            started.set()
            release.wait(3)
            return self.output
        async def resolve(scope):
            return hung
        self.worker = MemoryWorker(self.jobs, self.policy, resolve, extraction_timeout=0.02, encoder=self.encoder)
        first = await self.worker.submit(self.event())
        await self.worker.submit(self.event('two'))
        try:
            await self.worker.run_once()
            self.assertTrue(started.is_set())
            self.assertEqual(self.row(first)['error_category'], 'timeout')
            self.now[0] += 2
            self.assertFalse(await self.worker.run_once())
            self.now[0] += 121
            self.assertFalse(await self.worker.run_once())
            self.assertEqual(self.row(first)['state'], 'failed')
            self.assertEqual(self.row(first)['error_category'], 'deadline_exceeded')
            before = time.monotonic()
            await self.worker.close(timeout=0.02)
            self.assertLess(time.monotonic() - before, 0.2)
        finally:
            release.set()
            await asyncio.sleep(0.02)
        self.assertEqual(self.store.list_memories(self.scope.owner), [])

    async def test_shutdown_total_deadline_preserves_native_task_for_recovery(self):
        release, started = threading.Event(), threading.Event()
        async def resolve(scope):
            def blocked(sources, context):
                started.set()
                release.wait(3)
                return self.output
            return blocked
        self.worker = MemoryWorker(self.jobs, self.policy, resolve, encoder=self.encoder)
        result = await self.worker.submit(self.event())
        await self.worker.start()
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            before = time.monotonic()
            self.assertFalse(await self.worker.close(timeout=0.02))
            self.assertLess(time.monotonic() - before, 0.2)
            await asyncio.sleep(0)
            self.assertEqual(self.row(result)['state'], 'extracting')
            self.assertEqual((await self.worker.submit(self.event('late')))['status'], 'closing')
        finally:
            release.set()
            await asyncio.sleep(0.03)
        self.jobs.recover()
        await MemoryWorker(self.jobs, self.policy, self.resolve, encoder=self.encoder).run_once()
        self.assertEqual(self.row(result)['state'], 'committed')

    async def test_atomic_commit_rolls_back_facts_and_checkpoint_then_reuses_result(self):
        result = await self.worker.submit(self.event())
        with self.store.db:
            self.store.db.execute("CREATE TEMP TRIGGER reject_terminal BEFORE UPDATE OF state ON mia_jobs "
                "WHEN NEW.state='committed' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        await self.worker.run_once()
        self.assertEqual(self.store.list_memories(self.scope.owner), [])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0], 0)
        self.assertEqual(self.row(result)['state'], 'retry_wait')
        with self.store.db:
            self.store.db.execute('DROP TRIGGER reject_terminal')
        self.now[0] += 2
        await self.worker.run_once()
        self.assertEqual(self.row(result)['state'], 'committed')
        self.assertEqual(len(self.calls), 1)

    async def test_service_shutdown_shares_budget_and_retains_inflight_job_for_recovery(self):
        from server.companion.youmi_adapter import stop_service
        started, release = threading.Event(), threading.Event()
        async def resolve(scope):
            def blocked(sources, context):
                started.set()
                release.wait(2)
                return self.output
            return blocked
        self.worker = MemoryWorker(self.jobs, self.policy, resolve, encoder=self.encoder)
        result = await self.worker.submit(self.event())
        await self.worker.start()
        observed = []
        async def serving():
            try:
                await asyncio.Future()
            finally:
                observed.append(self.worker.closing)
                await asyncio.sleep(.3)
        services = [asyncio.create_task(serving()) for _ in range(3)]
        async def slow_gc():
            await asyncio.sleep(.3)
        gc = SimpleNamespace(stop=slow_gc)
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            before = time.monotonic()
            self.assertFalse(await stop_service(SimpleNamespace(_youmi_worker=self.worker), gc, services, timeout=.12))
            self.assertLess(time.monotonic() - before, .22)  # Scheduler tolerance, not a P23 measurement.
            self.assertEqual(observed, [True] * 3)
            self.assertTrue(all(task.done() for task in services))
            self.assertTrue(self.worker.task.done(), [(frame.f_code.co_name, frame.f_lineno)
                for frame in self.worker.task.get_stack()])
            self.assertEqual(self.row(result)['state'], 'extracting')
            self.assertEqual((await self.worker.submit(self.event('late')))['status'], 'closing')
            self.assertEqual(self.store.list_memories(self.scope.owner), [])
        finally:
            release.set()
            await asyncio.sleep(.03)
        self.jobs.recover()
        await MemoryWorker(self.jobs, self.policy, self.resolve, encoder=self.encoder).run_once()
        self.assertEqual(self.row(result)['state'], 'committed')
        self.assertEqual(len(self.store.list_memories(self.scope.owner)), 1)

    async def test_service_shutdown_drains_existing_job_and_gc_concurrently(self):
        from server.companion.youmi_adapter import stop_service
        started, release = threading.Event(), threading.Event()
        async def resolve(scope):
            def model(sources, context):
                started.set()
                release.wait(2)
                return self.output
            return model
        self.worker = MemoryWorker(self.jobs, self.policy, resolve, encoder=self.encoder)
        result = await self.worker.submit(self.event())
        await self.worker.start()
        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        async def stop_gc():
            await asyncio.sleep(.02)
            release.set()
        service = asyncio.create_task(asyncio.sleep(10))
        try:
            self.assertTrue(await stop_service(SimpleNamespace(_youmi_worker=self.worker),
                SimpleNamespace(stop=stop_gc), [service, None], timeout=1))
            self.assertTrue(service.cancelled())
            self.assertTrue(self.worker.task.done())
            self.assertEqual(self.row(result)['state'], 'committed')
            self.assertEqual(len(self.store.list_memories(self.scope.owner)), 1)
        finally:
            release.set()

    async def test_service_shutdown_gc_error_does_not_skip_worker_or_transport_cleanup(self):
        from server.companion.youmi_adapter import stop_service
        await self.worker.start()
        async def fail_gc():
            raise OSError('synthetic GC cleanup failure')
        service = asyncio.create_task(asyncio.sleep(10))
        with self.assertLogs('server.companion.youmi_adapter', level='WARNING'):
            self.assertFalse(await stop_service(SimpleNamespace(_youmi_worker=self.worker),
                SimpleNamespace(stop=fail_gc), [service], timeout=.3))
        self.assertTrue(self.worker.task.done())
        self.assertTrue(service.cancelled())

    async def test_cancelling_service_shutdown_still_cancels_worker_and_services(self):
        from server.companion.youmi_adapter import stop_service
        started, release = threading.Event(), threading.Event()
        async def resolve(scope):
            def blocked(sources, context):
                started.set()
                release.wait(2)
                return self.output
            return blocked
        self.worker = MemoryWorker(self.jobs, self.policy, resolve, encoder=self.encoder)
        result = await self.worker.submit(self.event())
        await self.worker.start()
        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        service = asyncio.create_task(asyncio.sleep(10))
        closing = asyncio.create_task(stop_service(SimpleNamespace(_youmi_worker=self.worker),
            SimpleNamespace(stop=AsyncMock(side_effect=asyncio.CancelledError)), [service], timeout=1))
        try:
            await asyncio.sleep(.01)
            closing.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await closing
            self.assertTrue(self.worker.task.done())
            self.assertTrue(service.cancelled())
            self.assertEqual(self.row(result)['state'], 'extracting')
        finally:
            release.set()
            await asyncio.sleep(.03)

    async def test_inflight_revocation_and_deletion_stop_late_result_before_commit(self):
        for operation in ('revoke', 'delete'):
            self.policy.active = True
            result = await self.worker.submit(self.event(operation))
            async def resolve(scope):
                def extract(sources, context):
                    if operation == 'revoke':
                        self.policy.active = False
                    else:
                        self.store.clear(scope.owner)
                    return self.output
                return extract
            await MemoryWorker(self.jobs, self.policy, resolve, encoder=self.encoder).run_once()
            self.assertEqual(self.row(result)['state'], 'cancelled')
            self.assertEqual(self.store.list_memories(self.scope.owner), [])
            self.assertEqual(self.row(result)['source_payload'], '{}')

    async def test_explicit_remember_priority_is_limited_to_its_own_completion(self):
        from server.companion import youmi_adapter as youmi
        provider = youmi.YoumiMemoryProvider(self.store, self.scope.owner, None, 'session', self.scope, self.policy)
        conn = SimpleNamespace(_youmi_memory=provider, _youmi_scope=self.scope, _youmi_authorization=self.policy,
            read_config_from_api=True, session_id='session', loop=asyncio.get_running_loop(),
            dialogue=SimpleNamespace(dialogue=[]), websocket=SimpleNamespace(send=AsyncMock()),
            server=SimpleNamespace(_youmi_worker=self.worker), client_abort=False)
        @youmi.completed_chat
        def complete(conn, query, depth=0):
            conn.sentence_id = uuid.uuid4().hex
            youmi.begin_turn(conn, query)
        await asyncio.to_thread(complete, conn, '请记住我喜欢蓝色')
        first = self.row(conn._youmi_job_status)
        await asyncio.to_thread(complete, conn, '今天聊点别的')
        second = self.row(conn._youmi_job_status)
        self.assertTrue(json.loads(first['source_payload'])['priority'])
        self.assertFalse(json.loads(second['source_payload'])['priority'])
        self.assertEqual(self.worker.foreground, 0)
        self.assertEqual(self.calls, [])

    async def test_wire_status_is_owner_scoped_and_does_not_return_source_or_clear_history(self):
        from server.companion import youmi_adapter as youmi
        own = await self.worker.submit(self.event())
        peer = await self.worker.submit(self.event('peer', self.peer))
        history = [SimpleNamespace(role='user', content='keep display history')]
        conn = SimpleNamespace(_youmi_memory=youmi.YoumiMemoryProvider(self.store, self.scope.owner, None,
            'session', self.scope, self.policy), read_config_from_api=True, _youmi_scope=self.scope,
            _youmi_authorization=self.policy, dialogue=SimpleNamespace(dialogue=history),
            websocket=SimpleNamespace(send=AsyncMock()))
        async def query(job):
            await youmi.handle_message(conn, json.dumps({'type': 'mia_memory', 'request_id': str(uuid.uuid4()),
                'action': 'job_status', 'id': job['id']}))
            return json.loads(conn.websocket.send.call_args.args[0])
        response = await query(own)
        self.assertEqual(set(response['result']), {'id', 'state', 'attempts', 'error_category', 'confirmation_required'})
        self.assertEqual(response['result']['state'], 'pending')
        self.assertEqual(len(history), 1)
        self.assertEqual((await query(peer))['error']['code'], 'not_found')
        self.policy.active = False
        self.assertEqual((await query(own))['error']['code'], 'authorization_required')

    async def test_service_runtime_resolves_existing_model_and_owns_start_stop(self):
        from server.companion import youmi_adapter as youmi
        selected = {'type': 'fixture', 'model_name': 'same-agent-model'}
        self.policy.worker_model = AsyncMock(return_value={'selected_module': {'LLM': 'existing'},
                                                          'LLM': {'existing': selected}})
        client = SimpleNamespace(close=lambda: None)
        calls = []
        def create(module, config):
            calls.append((module, config))
            def respond(system, user):
                self.assertEqual(json.loads(user)['user_excerpts'], ['我喜欢蓝色'])
                return self.output
            return SimpleNamespace(client=client, response_no_stream=respond)
        factory = ModuleType('core.utils.llm')
        factory.create_instance = create
        server = SimpleNamespace(config={'read_config_from_api': True})
        with patch('server.companion.youmi_adapter._store', return_value=self.store), \
                patch('server.companion.jobs.JobStore', return_value=self.jobs), \
                patch('server.companion.reply._encoder', self.encoder), \
                patch('server.companion.authorization.PrivateAuthorization', return_value=self.policy), \
                patch.dict(sys.modules, {'core': ModuleType('core'), 'core.utils': ModuleType('core.utils'),
                                        'core.utils.llm': factory}):
            await youmi.start_worker(server)
            try:
                result = await server._youmi_worker.submit(self.event())
                for _ in range(200):
                    if self.row(result)['state'] == 'committed':
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(self.row(result)['state'], 'committed')
                self.assertEqual(calls, [('fixture', selected)])
            finally:
                await youmi.stop_worker(server)
            self.assertTrue(server._youmi_worker.task.done())

    async def test_secret_never_enters_jobs_and_status_is_not_a_fact_database(self):
        with self.assertRaises(MemoryError):
            await self.worker.submit(self.event(text='password: synthetic'))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM mia_jobs').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
