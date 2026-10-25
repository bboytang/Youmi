"""Fixed zero-remote V2 contention and cold/warm observations; never installed."""
import asyncio
import hashlib
import json
import threading
import time
from unittest.mock import patch


SDK_CAP = 34  # Two fixed arms * (two foreground lanes * eight + one Worker).
FOREGROUND_SECONDS = 2
BACKGROUND_SECONDS = 8.8167721825  # Historical 66 native calls' median, not an SLA.


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


async def cold_warm(index, scope, authorization, query):
    from youmi_companion.retrieval import FeatureCache
    from youmi_stage_trace import StageTrace
    rows = []
    original = FeatureCache._prepare
    for number in range(6):
        counter = {'calls': 0, 'inclusive_ms': 0}
        def prepare(cache, *args):
            start = time.perf_counter()
            try:
                return original(cache, *args)
            finally:
                counter['calls'] += 1
                counter['inclusive_ms'] += (time.perf_counter() - start) * 1000
        sample = {'started': time.perf_counter()}
        trace = StageTrace(sample)
        row = {'number': number, 'warm': number > 0,
            'cache_entries_before': len(index._features.entries), 'cache_bytes_before': index._features.bytes}
        rows.append(row)
        try:
            trace.install(index.store)
            trace.wrap(FeatureCache, 'begin', 'features.version_invalidation')
            trace.wrap(FeatureCache, 'snapshot', 'features.snapshot')
            scan = index._scan
            row['scan_thread_cpu_ms'], row['scan_wall_ms'] = 0, 0
            def observed_scan(*args):
                wall, cpu = time.perf_counter(), time.thread_time()
                try:
                    return scan(*args)
                finally:
                    row['scan_thread_cpu_ms'] += (time.thread_time() - cpu) * 1000
                    row['scan_wall_ms'] += (time.perf_counter() - wall) * 1000
            with patch.object(FeatureCache, '_prepare', prepare), patch.object(index, '_scan', observed_scan):
                start = time.perf_counter()
                value = await index.retrieve(scope, authorization, query)
                row['elapsed_ms'] = (time.perf_counter() - start) * 1000
            row.update(response_sha256=digest(value), feature_preparation=counter,
                cache_entries_after=len(index._features.entries), cache_bytes_after=index._features.bytes,
                trace=trace.report())
            assert row['trace']['dropped'] == 0
        finally:
            trace.close()
    assert len({row['response_sha256'] for row in rows}) == 1, 'Cold/warm semantic results differ'
    assert rows[0]['feature_preparation']['calls'] == 10000, 'Cold 10k feature state was not cold'
    assert all(row['feature_preparation']['calls'] == 0 for row in rows[1:]), 'Warm path rebuilt features'
    return {'scope': 'One cold-feature read and five fixed warm reads, real BGE already initialized; no formal latency acceptance',
        'observer_scope': 'Per-row feature preparation aggregates only, no per-row spans or disk writes; reported times include observation overhead',
        'cases': rows}


async def contention(worker, scopes, query, report, persist):
    import httpx
    import openai
    from core.providers.llm.openai.openai import LLMProvider
    from youmi_companion import jobs, extraction
    from youmi_companion.jobs import TurnCompleted
    from youmi_companion.model_requests import ModelRequestGate

    result = {'remote_requests': 0, 'sdk_cap': SDK_CAP, 'sdk_calls': [], 'http_entries': [], 'arms': [],
        'scope': 'Actual Worker/manager/BGE and actual fixed Provider SDK with MockTransport; foreground model lanes, not full ConnectionHandler/TTS/audio acceptance',
        'fixed_order': ['strict-control', 'four-ten'], 'foreground_hold_seconds': FOREGROUND_SECONDS,
        'background_hold_seconds': BACKGROUND_SECONDS,
        'boundary': 'Finite fixed arrivals; native durations controlled. No unconditional deadline or foreground latency guarantee for saturated real Provider.'}
    report['v2_contention'] = result
    lock = threading.Lock()
    origin = time.perf_counter()
    original_resolve = worker.resolve
    for label, age in (('strict-control', None), ('four-ten', 10)):
        gate = ModelRequestGate(background_after=age, foreground_burst=4)
        arm = {'policy': label, 'foreground': [], 'background': None}
        result['arms'].append(arm)
        entered = threading.Event()
        front_entries = [0]
        def factory(purpose):
            provider = LLMProvider({'model_name': 'glm-4-flash-250414', 'api_key': 'synthetic-only',
                'base_url': 'https://controlled.invalid/v4', 'timeout': 10})
            provider.client.close()
            def transport(request):
                with lock:
                    result['http_entries'].append({'arm': label, 'purpose': purpose,
                        'at_ms': (time.perf_counter() - origin) * 1000, 'gate': gate.snapshot(), 'worker_foreground': worker.foreground})
                    assert len(result['http_entries']) <= SDK_CAP, 'Mock HTTP budget exceeded'
                    if purpose == 'foreground':
                        front_entries[0] += 1
                        if front_entries[0] == 2:
                            entered.set()
                time.sleep(FOREGROUND_SECONDS if purpose == 'foreground' else BACKGROUND_SECONDS)
                content = json.dumps({'memories': []}) if purpose == 'background' else '我们先慢慢整理。'
                chunk = {'id': 'controlled', 'object': 'chat.completion.chunk', 'created': 0,
                    'model': 'glm-4-flash-250414', 'choices': [{'index': 0, 'finish_reason': 'stop', 'delta': {'content': content}}]}
                return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                    content=('data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\ndata: [DONE]\n\n').encode())
            provider.client = openai.OpenAI(api_key='synthetic-only', base_url=provider.base_url, max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(transport)))
            create = provider.client.chat.completions.create
            def counted(*args, **kwargs):
                with lock:
                    assert len(result['sdk_calls']) < SDK_CAP, 'Controlled SDK budget exceeded'
                    result['sdk_calls'].append({'arm': label, 'purpose': purpose,
                        'at_ms': (time.perf_counter() - origin) * 1000})
                return create(*args, **kwargs)
            provider.client.chat.completions.create = counted
            return provider
        async def resolve(scope):
            return extraction.LLMMemoryExtractor(lambda: factory('background'))
        def foreground_call():
            queued = time.perf_counter()
            with gate.acquire(foreground=True, timeout=10):
                acquired = time.perf_counter()
                provider = factory('foreground')
                try:
                    text = ''.join(provider.response('', [{'role': 'user', 'content': '受控调度机械检查'}]))
                    assert text == '我们先慢慢整理。'
                finally:
                    provider.client.close()
            return {'queue_ms': (acquired - queued) * 1000,
                'sdk_and_consume_ms': (time.perf_counter() - acquired) * 1000}
        async def lane(scope):
            for number in range(8):
                started = time.perf_counter()
                value = await worker.index.retrieve(scope, worker.authorization, query)
                assert value['candidates']
                retrieval = (time.perf_counter() - started) * 1000
                sample = await asyncio.to_thread(foreground_call)
                arm['foreground'].append(dict(sample, number=number, owner=scope.owner, retrieval_ms=retrieval,
                    total_ms=(time.perf_counter() - started) * 1000))
        worker.resolve = resolve
        worker.foreground += 2
        tasks = []
        try:
            with patch.object(jobs, 'model_requests', gate), patch.object(extraction, 'model_requests', gate):
                tasks = [asyncio.create_task(lane(scope)) for scope in scopes[:2]]
                assert await asyncio.to_thread(entered.wait, 8), 'Both foreground SDKs did not start'
                admitted_at = time.perf_counter()
                receipt = await worker.submit(TurnCompleted(scopes[2], 'v2-controlled', label, label,
                    '我每周三都会整理书桌。', worker.jobs.store.snapshot(scopes[2].owner)['revision']))
                assert receipt['status'] == 'accepted'
                arm['admission'] = receipt
                await asyncio.gather(*tasks)
                worker.foreground -= 2
                while True:
                    row = await asyncio.to_thread(worker.jobs.current, receipt)
                    if row['state'] not in jobs.ACTIVE:
                        break
                    assert time.perf_counter() - admitted_at < 120
                    worker.wake.set()
                    await asyncio.sleep(.02)
                arm['background'] = {k: row[k] for k in ('id', 'state', 'attempts', 'error_category', 'deadline')}
                with worker.jobs.store.lock:
                    arm['ledger'] = [dict(r) for r in worker.jobs.store.db.execute(
                        'SELECT * FROM mia_model_requests WHERE job_id=? ORDER BY ordinal', (row['id'],))]
                arm['observed_terminal_seconds'] = time.perf_counter() - admitted_at
                assert row['state'] == 'empty' and row['attempts'] == 1
                request, = arm['ledger']
                arm['persisted_queue_ms'] = (request['slot_acquired_at'] - request['waiting_at']) * 1000
                assert request['http_sends'] == 1 and request['native_finished_at'] is not None
                bg = [r for r in result['http_entries'] if r['arm'] == label and r['purpose'] == 'background']
                assert len(bg) == 1
                assert bg[0]['worker_foreground'] == (0 if age is None else 2), 'Worker entry and gate must both permit bounded progress'
                persist(report)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            worker.foreground = 0
            worker.resolve = original_resolve
            persist(report)
    assert len(result['sdk_calls']) == len(result['http_entries']) == SDK_CAP
    return result


async def run(worker, scopes, query, root, report, persist):
    observation = await cold_warm(worker.index, scopes[1], worker.authorization, query)
    raw = (json.dumps(observation, ensure_ascii=False, indent=2) + '\n').encode()
    path = root / 'v2-cold-warm.json'
    path.write_bytes(raw)
    report['v2_cold_warm'] = {'file': path.name, 'sha256': hashlib.sha256(raw).hexdigest(), 'read_count': 6}
    persist(report)
    await contention(worker, scopes, query, report, persist)
