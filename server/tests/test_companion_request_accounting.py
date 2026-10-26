"""Real OpenAI SDK + MockTransport; zero remote requests, explicit V2 faults."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
import openai

from server.companion.extraction import LLMMemoryExtractor, context_for
from server.companion.jobs import JobStore, MemoryWorker
from server.companion.model_requests import ModelRequestGate
from server.companion.request_accounting import RequestAccounting, bind_sdk
from server.tests import test_companion_jobs as fixtures


MODULE = 'core.providers.llm.openai.openai'
RECORDS = []


class SDKFixtureProvider:
    """Adapter stand-in only; installed fixed-upstream compatibility runs in CI."""
    __module__ = MODULE

    def __init__(self, transport):
        self.client = openai.OpenAI(api_key='fixture-only', base_url='https://fixture.invalid/v4',
            http_client=httpx.Client(transport=httpx.MockTransport(transport)))
        self.sdk_calls = 0
        self.on_sdk_entry = lambda: None
        create = self.client.chat.completions.create
        def observed(*args, **kwargs):
            self.sdk_calls += 1
            self.on_sdk_entry()
            return create(*args, **kwargs)
        self.client.chat.completions.create = observed

    def response_with_functions(self, session, dialogue, functions=None):
        stream = self.client.chat.completions.create(model='glm-4-flash-250414', messages=dialogue,
            stream=True, tools=functions)
        try:
            for chunk in stream:
                yield chunk.choices[0].delta.content, None
        finally:
            stream.close()


def sse(output):
    chunk = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'glm-4-flash-250414',
        'choices': [{'index': 0, 'finish_reason': 'stop', 'delta': {'content': json.dumps(output, ensure_ascii=False)}}]}
    return httpx.Response(200, headers={'content-type': 'text/event-stream'},
        content=('data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\ndata: [DONE]\n\n').encode())


class V2RequestTests(unittest.IsolatedAsyncioTestCase):
    event = fixtures.JobTests.event
    row = fixtures.JobTests.row

    def setUp(self):
        fixtures.JobTests.setUp(self)
        self.jobs = JobStore(self.store)
        self.http = []
        self.response = lambda request: sse(self.output)
        def transport(request):
            self.http.append(json.loads(request.content)['model'])
            return self.response(request)
        self.transport = transport
        self.providers = []
        self.sdk_entry = lambda: None
        def factory():
            provider = SDKFixtureProvider(self.transport)
            provider.on_sdk_entry = self.sdk_entry
            self.providers.append(provider)
            return provider
        async def resolve(scope):
            return LLMMemoryExtractor(factory)
        self.resolve = resolve
        self.worker = MemoryWorker(self.jobs, self.policy, resolve, encoder=self.encoder)
        self.module = patch.dict(sys.modules, {MODULE: SimpleNamespace(LLMProvider=SDKFixtureProvider)})
        self.module.start()

    def tearDown(self):
        RECORDS.append({'case': self._testMethodName, 'remote_requests': 0,
            'real_sdk_calls_with_mock_transport': sum(p.sdk_calls for p in self.providers),
            'mock_http_entries': len(self.http), 'jobs': [dict(r) for r in self.store.db.execute(
                'SELECT j.id,j.state,j.attempts,j.error_category,p.version,p.phase,p.waiting_at,p.slot_acquired_at '
                'FROM mia_jobs j LEFT JOIN mia_job_policy p ON p.job_id=j.id ORDER BY j.rowid')],
            'ledger': [dict(r) for r in self.store.db.execute('SELECT * FROM mia_model_requests ORDER BY job_id,ordinal')]})
        if os.environ.get('MIA_V2_MECHANISM_REPORT'):
            Path(os.environ['MIA_V2_MECHANISM_REPORT']).write_text(json.dumps(RECORDS, indent=2))
        self.module.stop()
        fixtures.JobTests.tearDown(self)

    def requests(self, job):
        return [dict(r) for r in self.store.db.execute('SELECT * FROM mia_model_requests WHERE job_id=? ORDER BY ordinal', (job['id'],))]

    def retry_now(self):
        self.now[0] += 1

    async def test_valid_request_has_one_intent_sdk_http_and_terminal(self):
        job = await self.worker.submit(self.event())
        await self.worker.run_once()
        self.assertEqual(self.row(job)['state'], 'committed')
        self.assertEqual(self.row(job)['attempts'], 1)
        self.assertEqual(self.http, ['glm-4-flash-250414'])
        self.assertEqual(sum(p.sdk_calls for p in self.providers), 1)
        row, = self.requests(job)
        self.assertEqual(row['http_sends'], 1)
        self.assertIsNotNone(row['response_completed_at'])
        self.assertIsNotNone(row['native_finished_at'])
        self.assertEqual(row['outcome'], 'validation_succeeded')
        self.assertEqual(self.jobs.policy(job)['phase'], 'terminal')
        self.assertEqual(self.providers[0].client.max_retries, 0)
        self.assertFalse(self.providers[0].client._client.follow_redirects)

    async def test_sdk_started_marker_is_observed_inside_sdk_not_before_invocation(self):
        job = await self.worker.submit(self.event())
        def at_entry():
            with self.store.lock:
                request, = self.requests(job)
            self.assertIsNotNone(request['intent_at'])
            self.assertIsNone(request['sdk_started_at'], 'A pre-call write cannot prove the SDK has begun')
        self.sdk_entry = at_entry
        original_response = self.response
        def at_http(request):
            with self.store.lock:
                row, = self.requests(job)
            self.assertIsNotNone(row['sdk_started_at'])
            return original_response(request)
        self.response = at_http
        await self.worker.run_once()
        self.assertEqual(self.row(job)['state'], 'committed')
        self.assertEqual(len(self.http), 1)

    async def test_default_sdk_http_client_is_reviewed_but_transport_retries_are_rejected(self):
        provider = SDKFixtureProvider(self.transport)
        provider.client.close()
        provider.client = openai.OpenAI(api_key='fixture-only')
        try:
            bind_sdk(provider, SimpleNamespace())  # No create/send, only configure.
            self.assertEqual(provider.client.max_retries, 0)
        finally:
            provider.client.close()
        provider = SDKFixtureProvider(self.transport)
        provider.client.close()
        provider.client = openai.OpenAI(api_key='fixture-only',
            http_client=httpx.Client(transport=httpx.HTTPTransport(retries=1)))
        try:
            with self.assertRaisesRegex(Exception, 'transport retry budget'):
                bind_sdk(provider, SimpleNamespace())
        finally:
            provider.client.close()

    async def test_thirty_seconds_queued_costs_no_v2_attempt(self):
        job = await self.worker.submit(self.event())
        gate = ModelRequestGate()
        gate.active = 2
        elapsed = [0]
        def wait(unused):
            elapsed[0] = 30.001
            self.now[0] = 130.001
        with patch('server.companion.extraction.model_requests', gate), \
                patch('server.companion.model_requests.time', SimpleNamespace(monotonic=lambda: elapsed[0])), \
                patch.object(gate.condition, 'wait', side_effect=wait):
            await self.worker.run_once()
        self.assertEqual(self.row(job)['attempts'], 0)
        self.assertEqual(self.row(job)['error_category'], 'model_queue_timeout')
        self.assertEqual(self.requests(job), [])
        self.assertEqual(sum(p.sdk_calls for p in self.providers), 0)
        self.assertEqual(self.http, [])
        self.assertEqual(self.jobs.policy(job)['waiting_at'], 100)

    async def test_reservation_transaction_failure_does_not_call_sdk(self):
        job = await self.worker.submit(self.event())
        def deny(action, table, *unused):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and table == 'mia_model_requests' else sqlite3.SQLITE_OK
        self.store.db.set_authorizer(deny)
        try:
            await self.worker.run_once()
        finally:
        self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        self.assertEqual(self.row(job)['attempts'], 0)
        self.assertEqual(self.row(job)['error_category'], 'database')
        self.assertEqual(self.http, [])
        self.assertEqual(self.requests(job), [])

    async def test_two_503_attempts_are_two_sdk_and_two_http_without_hidden_retry(self):
        self.response = lambda request: httpx.Response(503, json={'error': {'message': 'fixture unavailable'}})
        job = await self.worker.submit(self.event())
        await self.worker.run_once()
        self.retry_now()
        await self.worker.run_once()
        await self.worker.run_once()
        self.assertEqual(self.row(job)['attempts'], 2)
        self.assertEqual(self.row(job)['state'], 'failed')
        self.assertEqual(self.row(job)['error_category'], 'provider')
        self.assertEqual(len(self.http), 2)
        self.assertEqual(sum(p.sdk_calls for p in self.providers), 2)
        self.assertEqual([r['http_sends'] for r in self.requests(job)], [1, 1])

    async def test_redirect_is_not_a_second_http_send(self):
        self.response = lambda request: httpx.Response(307, headers={'location': 'https://fixture.invalid/elsewhere'})
        job = await self.worker.submit(self.event())
        await self.worker.run_once()
        self.assertEqual(len(self.http), 1)
        self.assertEqual(self.row(job)['attempts'], 1)
        self.assertEqual(self.requests(job)[0]['http_sends'], 1)

    async def test_timeout_and_restart_do_not_release_live_native_slot(self):
        gate = ModelRequestGate()
        entered, release = threading.Event(), threading.Event()
        def block(request):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('fixture timeout')
            return sse(self.output)
        self.response = block
        job = await self.worker.submit(self.event())
        original_call = self.worker.lane.call
        async def expire_after_send(function, unused):
            task = asyncio.create_task(original_call(function, 5))
            async def wait_for_entry():
                while not entered.is_set():
                    await asyncio.sleep(.001)
            await asyncio.wait_for(wait_for_entry(), 3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            raise asyncio.TimeoutError
        with patch('server.companion.extraction.model_requests', gate):
            try:
                with patch.object(self.worker.lane, 'call', side_effect=expire_after_send):
                    await self.worker.run_once()
                self.assertTrue(entered.is_set())
                self.assertEqual(self.row(job)['attempts'], 1)
                self.assertEqual(len(self.http), 1)
                self.assertEqual(gate.snapshot()['active'], 1)
                self.assertFalse(self.worker.lane.done.is_set())
                with self.assertRaisesRegex(RuntimeError, 'still running'):
                    self.jobs.recover()
            finally:
                release.set()
                for unused in range(300):
                    if self.worker.lane.done.is_set():
                        break
                    await asyncio.sleep(.005)
        self.assertTrue(self.worker.lane.done.is_set())
        self.assertEqual(gate.snapshot()['active'], 0)
        self.assertIsNotNone(self.requests(job)[0]['native_finished_at'])

    async def test_cancelled_queued_native_waiter_never_reserves_later(self):
        gate = ModelRequestGate()
        leases = [gate.acquire(foreground=True), gate.acquire(foreground=True)]
        self.worker.extraction_timeout = .03
        job = await self.worker.submit(self.event())
        with patch('server.companion.extraction.model_requests', gate):
            try:
                await self.worker.run_once()
                self.assertEqual(self.row(job)['attempts'], 0)
                self.assertEqual(gate.snapshot()['active'], 2)
            finally:
                for lease in leases:
                    lease.release()
                for unused in range(300):
                    if self.worker.lane.done.is_set():
                        break
                    await asyncio.sleep(.005)
        self.assertTrue(self.worker.lane.done.is_set())
        self.assertEqual(self.http, [])
        self.assertEqual(self.requests(job), [])

    async def test_context_version_edit_after_slot_is_rejected_before_reservation(self):
        self.store.apply_extraction(self.scope.owner, 'prior', 0, ['我喜欢小说'],
            {'memories': [{'category': 'preference', 'content': '我喜欢小说', 'source_index': 0}]}, 'prior')
        job = await self.worker.submit(self.event())
        original = RequestAccounting.intent
        async def change(accounting):
            with self.store.db:
                self.store.db.execute('UPDATE mia_memory_meta SET evidence_version=evidence_version+1')
            await original(accounting)
        with patch.object(RequestAccounting, 'intent', change):
            await self.worker.run_once()
        self.assertEqual(self.row(job)['state'], 'cancelled')
        self.assertEqual(self.row(job)['attempts'], 0)
        self.assertEqual(self.http, [])

    async def test_feedback_survives_worker_restart_and_database_retry_reuses_result(self):
        self.output = {'memories': 'invalid'}
        job = await self.worker.submit(self.event())
        await self.worker.run_once()
        self.assertEqual(self.row(job)['attempts'], 1)
        self.assertEqual(json.loads(self.jobs.policy(job)['feedback'])['category'], 'field_schema')
        self.worker = MemoryWorker(self.jobs, self.policy, self.resolve, encoder=self.encoder)
        self.jobs.recover()
        self.retry_now()
        self.output = {'memories': []}
        with patch.object(self.jobs, 'commit', side_effect=sqlite3.OperationalError('fixture write failed')):
            await self.worker.run_once()
        self.assertEqual(self.row(job)['attempts'], 2)
        self.assertIsNotNone(self.row(job)['validated_result'])
        self.assertIsNone(self.jobs.policy(job)['feedback'])
        self.worker = MemoryWorker(self.jobs, self.policy, self.resolve, encoder=self.encoder)
        self.jobs.recover()
        self.retry_now()
        await self.worker.run_once()
        self.assertEqual(self.row(job)['state'], 'empty')
        self.assertEqual(len(self.http), 2)

    async def test_legacy_and_v2_tasks_share_worker_without_reinterpreting_attempts(self):
        legacy = JobStore(self.store, policy_version=1).submit(self.event('legacy'))
        current = await self.worker.submit(self.event('v2'))
        self.jobs.recover()
        await self.worker.run_once()
        await self.worker.run_once()
        self.assertEqual(self.row(legacy)['state'], 'committed')
        self.assertEqual(self.row(current)['state'], 'committed')
        self.assertEqual(self.row(legacy)['attempts'], 1)
        self.assertEqual(self.row(current)['attempts'], 1)
        self.assertEqual(self.jobs.policy(legacy), {'version': 1})
        self.assertEqual(self.requests(legacy), [])
        self.assertEqual(len(self.requests(current)), 1)
        self.assertEqual(len(self.http), 2)

    async def test_two_foreground_sessions_do_not_block_v2_worker_queue_entry(self):
        gate = ModelRequestGate(background_after=10, foreground_burst=4)
        leases = [gate.acquire(foreground=True), gate.acquire(foreground=True)]
        legacy = JobStore(self.store, policy_version=1).submit(self.event('old-wait'))
        job = await self.worker.submit(self.event('new-wait'))
        self.worker.foreground = 2
        self.output = {'memories': []}  # Exercise model scheduling, no BGE admission wait.
        with patch('server.companion.jobs.model_requests', gate), patch('server.companion.extraction.model_requests', gate):
            task = asyncio.create_task(self.worker.run_once())
            try:
                async def queued():
                    while gate.snapshot()['waiting_background'] != 1:
                        await asyncio.sleep(.001)
                await asyncio.wait_for(queued(), 3)
                self.assertEqual(self.row(job)['attempts'], 0)
                self.assertEqual(self.row(legacy)['attempts'], 0)
                self.assertEqual(self.http, [])
                leases[0].release()
                await asyncio.wait_for(task, 3)
                self.assertEqual(self.worker.foreground, 2)
                self.assertEqual(self.row(job)['state'], 'empty')
                self.assertEqual(self.row(job)['attempts'], 1)
                self.assertEqual(self.row(legacy)['state'], 'pending')
                self.assertEqual(gate.snapshot()['active'], 1)
            finally:
                for lease in leases:
                    lease.release()
                if not task.done():
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task

    async def test_preflight_blocks_cancellation_deadline_revocation_and_rebinding(self):
        for reason in ('cancel', 'deadline', 'revoke', 'rebind', 'clear'):
            with self.subTest(reason=reason):
                self.now[0] = 100
                self.policy.active = True
                original_scope = self.policy.scopes[self.scope.owner]
                job = await self.worker.submit(self.event(reason))
                original = RequestAccounting.intent
                async def mutate(accounting):
                    if reason == 'cancel':
                        accounting.context.request_cancelled.set()
                    elif reason == 'deadline':
                        self.now[0] = 220
                    elif reason == 'revoke':
                        self.policy.active = False
                    elif reason == 'rebind':
                        self.policy.scopes[self.scope.owner] = None
                    else:
                        self.store.clear(self.scope.owner)
                    return await original(accounting)
                with patch.object(RequestAccounting, 'intent', mutate):
                    await self.worker.run_once()
                self.policy.scopes[self.scope.owner] = original_scope
                self.assertEqual(self.row(job)['attempts'], 0)
                self.assertEqual(self.http, [])

    async def test_revocation_after_durable_intent_consumes_attempt_without_sdk(self):
        job = await self.worker.submit(self.event())
        original = RequestAccounting.reserve
        def revoke_after_intent(accounting):
            ordinal = original(accounting)
            self.policy.active = False
            return ordinal
        with patch.object(RequestAccounting, 'reserve', revoke_after_intent):
            await self.worker.run_once()
        self.assertEqual(self.row(job)['attempts'], 1)
        self.assertEqual(self.row(job)['state'], 'cancelled')
        row, = self.requests(job)
        self.assertIsNone(row['sdk_started_at'])
        self.assertEqual(row['http_sends'], 0)
        self.assertEqual(self.http, [])

    async def test_legacy_waiter_rechecks_authorization_after_slot_without_changing_counter(self):
        gate = ModelRequestGate(background_after=10, foreground_burst=4)
        leases = [gate.acquire(foreground=True), gate.acquire(foreground=True)]
        job = JobStore(self.store, policy_version=1).submit(self.event())
        with patch('server.companion.jobs.model_requests', gate), patch('server.companion.extraction.model_requests', gate):
            task = asyncio.create_task(self.worker.run_once())
            try:
                async def queued():
                    while gate.snapshot()['waiting_background'] != 1:
                        await asyncio.sleep(.001)
                await asyncio.wait_for(queued(), 3)
                self.policy.active = False
                leases[0].release()
                await asyncio.wait_for(task, 3)
            finally:
                for lease in leases:
                    lease.release()
        self.assertEqual(self.row(job)['state'], 'cancelled')
        self.assertEqual(self.row(job)['attempts'], 1)  # V1 queue charge is retained.
        self.assertEqual(self.requests(job), [])
        self.assertEqual(sum(p.sdk_calls for p in self.providers), 0)
        self.assertEqual(self.http, [])

    async def test_crash_windows_consume_reservations_without_refunds(self):
        for mode, code, sends in [('intent', 71, 0), ('http', 72, 1)]:
            job = await self.worker.submit(self.event(mode))
            completed = await asyncio.to_thread(subprocess.run,
                [sys.executable, '-m', 'server.tests.test_companion_request_accounting', '--crash', str(self.path), job['id'], mode],
                capture_output=True, text=True, timeout=15)
            self.assertEqual(completed.returncode, code, completed.stderr)
            self.assertEqual(self.row(job)['attempts'], 1)
            row, = self.requests(job)
            self.assertEqual(row['http_sends'], sends)
            self.assertIsNone(row['native_finished_at'])
            self.jobs.recover()
            self.assertEqual(self.requests(job)[0]['outcome'], 'process_lost')
            self.assertEqual(self.row(job)['attempts'], 1)
            await self.worker.run_once()
            self.assertEqual(self.row(job)['attempts'], 2)
            self.assertEqual(self.row(job)['state'], 'committed')


async def crash_process(path, identifier, mode):
    from server.companion.memory import MemoryStore
    store = MemoryStore(path, clock=lambda: 100)
    jobs = JobStore(store)
    job = jobs.current({'id': identifier})
    context = context_for(store, job['owner'])
    context.request_cancelled = threading.Event()
    async def scope(unused):
        return None  # Crash injection isolates ledger mechanics, not manager auth.
    worker = SimpleNamespace(jobs=jobs, _scope=scope)
    accounting = context.request_accounting = RequestAccounting(worker, job, context)
    def transport(request):
        os._exit(72)
    original = RequestAccounting.reserve
    def reserve(instance):
        original(instance)
        os._exit(71)
    with patch.dict(sys.modules, {MODULE: SimpleNamespace(LLMProvider=SDKFixtureProvider)}):
        if mode == 'intent':
            RequestAccounting.reserve = reserve
        await asyncio.to_thread(LLMMemoryExtractor(lambda: SDKFixtureProvider(transport)),
            [json.loads(job['source_payload'])['text']], context)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--crash':
        asyncio.run(crash_process(*sys.argv[2:]))
    else:
        unittest.main()
