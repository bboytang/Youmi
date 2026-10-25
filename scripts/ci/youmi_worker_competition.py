"""P23 actual extraction/foreground overlap; no P22 holdouts or fake outputs."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import ssl
import threading
import time
import uuid


SOURCE = '我每周六在书桌边整理纸质邮票册'


def active_job(worker, owner, sources):
    """Bind the native extractor to exactly one original durable active row."""
    with worker.jobs.store.lock:
        rows = worker.jobs.store.db.execute(
            "SELECT id,source_payload,attempts FROM mia_jobs WHERE owner=? AND state='extracting'",
            (owner,)).fetchall()
    matches = [dict(row) for row in rows if [json.loads(row['source_payload'])['text']] == sources]
    assert len(matches) == 1, 'Actual extractor is not bound to exactly one active source/Scope job'
    return {'job_id': matches[0]['id'], 'owner': owner, 'attempt': matches[0]['attempts'],
        'sources': sources, 'source_sha256': hashlib.sha256(sources[0].encode()).hexdigest()}


def observe_validation_failures(worker, report, lock, persist):
    import youmi_companion.jobs as jobs
    from youmi_companion.memory import MemoryError as ProposalError
    original = jobs.validate_output

    def validate(value, sources, context):
        try:
            return original(value, sources, context)
        except ProposalError as error:
            with lock:
                native = report['native_extractions'][-1]
                binding = active_job(worker, native['owner'], sources)
                assert binding['job_id'] == native['job_id'] and binding['attempt'] == native['attempt']
                encoded = json.dumps(value, ensure_ascii=False).encode()
                failure = dict(binding, error_code=error.code, error_message=str(error),
                    error_category=getattr(error, 'category', 'invalid_output'),
                    rejected_clues=getattr(error, 'rejected_clues', {}),
                    original_output_sha256=hashlib.sha256(encoded).hexdigest(),
                    original_output_capture_truncated=len(encoded) > 131072)
                assert failure['original_output_sha256'] == native['original_output_sha256']
                if len(encoded) <= 131072:
                    failure['original_output'] = value
                failures = report.setdefault('validation_failures', [])
                assert len(failures) < 72
                failures.append(failure)
                persist(report)
            raise  # The original Worker receives the same rejected proposal/error.

    jobs.validate_output = validate


async def open_driver(server, scopes, config, root, report, persist):
    import core.utils.llm as existing_llm
    from core.auth import AuthManager
    from youmi_companion.jobs import TurnCompleted
    from youmi_companion.memory import MemoryError as ProposalError
    from websockets.asyncio.server import serve
    from youmi_reply_latency import open_driver as open_replies

    lock, local = threading.RLock(), threading.local()
    diagnostic = os.environ['MIA_P23_MODE'] == 'worker-fairness-diagnostic'
    if diagnostic:
        report['capture_extraction_stream'] = True
    replies = await open_replies(server, scopes, config, root, report, persist, lock=lock)
    worker = server._youmi_worker
    if os.environ['MIA_P23_MODE'] == 'worker-fairness':
        observe_validation_failures(worker, report, lock, persist)
    overlap = os.environ['MIA_P23_MODE'] == 'worker-smoke'
    started = time.perf_counter()
    elapsed = lambda: (time.perf_counter() - started) * 1000
    report.update(worker_probe={'source': SOURCE, 'owner': scopes[2].owner},
        extraction_requests=[], native_extractions=[], foreground_transitions=[], foreground_peak=0,
        first_sentence_cases_scope=('Two concurrent actual memory replies, not same-Provider baseline or a D08 percentile measurement'
            if overlap else 'No foreground replies in isolated extraction contract'),
        worker_probe_scope='Original Worker.submit/resolve/LLMMemoryExtractor/SDK/validation/BGE/commit; '
            'one new synthetic user source. Two original chat replies use fixed P23 questions. '
            'A bounded SDK response handoff rendezvous can keep the native lane occupied until both foreground chats enter; '
            'this is controlled overlap, not an unmodified end-to-end latency measurement.'
            if overlap else 'Original Worker and real SDK/proposal/validation/BGE/commit for one new source, no foreground replies or concurrency acceptance.')
    persist(report)
    original_factory, original_resolve = existing_llm.create_instance, worker.resolve

    def factory(name, model):
        provider = original_factory(name, model)
        create = provider.client.chat.completions.create

        def observed(*args, **kwargs):
            context = local.context
            payload = json.loads(kwargs['messages'][1]['content'])
            assert payload['user_excerpts'] == context['sources']
            entry = dict(context, at_ms=elapsed(), model=kwargs['model'],
                messages_sha256=hashlib.sha256(json.dumps(kwargs['messages'], ensure_ascii=False).encode()).hexdigest(),
                max_tokens=kwargs.get('max_tokens'), tools_count=len(kwargs.get('tools', [])), foreground_at_start=worker.foreground)
            assert entry['model'] == 'glm-4-flash-250414' and entry['tools_count'] == 1 and entry['max_tokens'] == 4096
            with lock:
                report['extraction_requests'].append(entry)
                persist(report)
            try:
                response = create(*args, **kwargs)  # Original SDK and real remote response.
                handoff = time.perf_counter()
                deadline = handoff + 6
                while overlap and report['foreground_peak'] < 2 and time.perf_counter() < deadline:
                    time.sleep(.005)
                with lock:
                    entry['response_handoff_wait_ms'] = (time.perf_counter() - handoff) * 1000
                    entry['foreground_peak_before_handoff'] = report['foreground_peak']
                    entry['response_handoff_at_ms'] = elapsed()
                    persist(report)
                return response
            finally:
                with lock:
                    entry['create_return_at_ms'] = elapsed()
                    persist(report)

        provider.client.chat.completions.create = observed
        return provider

    async def resolve(scope):
        extractor = await original_resolve(scope)

        def extract(sources, context):
            local.context = active_job(worker, scope.owner, sources)
            evidence = dict(local.context, started_at_ms=elapsed())
            with lock:
                report['native_extractions'].append(evidence)
                persist(report)
            try:
                result = extractor(sources, context)  # No proposal repair or substitute response.
                with lock:
                    evidence['original_output_sha256'] = hashlib.sha256(json.dumps(result, ensure_ascii=False).encode()).hexdigest()
                    if diagnostic:
                        encoded = json.dumps(result, ensure_ascii=False).encode()
                        evidence['original_output_capture_truncated'] = len(encoded) > 131072
                        if len(encoded) <= 131072:
                            evidence['original_output'] = result
                if diagnostic:
                    from youmi_companion.extraction import validate_output
                    try:
                        validate_output(json.loads(json.dumps(result)), sources, context)
                        validation = {'passed': True}
                    except Exception as error:
                        validation = {'passed': False, 'error_class': type(error).__name__}
                        if isinstance(error, ProposalError):
                            validation.update(code=error.code, message=str(error),
                                rejected_clues=getattr(error, 'rejected_clues', {}))
                    with lock:
                        evidence['original_output_validation'] = validation
                return result
            except Exception as error:
                with lock:
                    evidence['error_class'] = type(error).__name__
                    if isinstance(error, ProposalError):
                        evidence.update(error_code=error.code, error_message=str(error))
                    persist(report)
                raise
            finally:
                with lock:
                    evidence['finished_at_ms'] = elapsed()
                    persist(report)
                local.context = None

        return extract

    existing_llm.create_instance, worker.resolve = factory, resolve

    async def watch():
        from youmi_companion.model_requests import model_requests
        previous = None
        previous_gate = None
        while True:
            gate = model_requests.snapshot()
            if gate != previous_gate:
                with lock:
                    transitions = report.setdefault('model_gate_transitions', [])
                    assert len(transitions) < 256, 'Unbounded gate evidence'
                    transitions.append(dict(gate, at_ms=elapsed()))
                    persist(report)
                previous_gate = gate
            value = worker.foreground
            if value != previous:
                with lock:
                    report['foreground_peak'] = max(report['foreground_peak'], value)
                    report['foreground_transitions'].append({'at_ms': elapsed(), 'foreground': value})
                    persist(report)
                previous = value
            await asyncio.sleep(.005)

    watcher = asyncio.create_task(watch(), name='p23-foreground-observer')
    transport = AuthManager('synthetic-p23-latency-transport')

    async def connection(socket):
        try:
            headers = dict(socket.request.headers)
            assert transport.verify_token(headers.get('authorization', '')[7:], headers.get('client-id'), headers.get('device-id'))
            scope = await worker.authorization.verify(headers['device-id'], headers['client-id'], headers['mia-private-credential'])
            assert scope.snapshot() == scopes[2].snapshot()
            request = json.loads(await socket.recv())
            if request['type'] == 'submit_probe':
                assert 'admission' not in report['worker_probe'], 'Probe already submitted'
                event = TurnCompleted(scope, 'p23-worker-overlap', uuid.uuid4().hex, uuid.uuid4().hex,
                    SOURCE, worker.jobs.store.snapshot(scope.owner)['revision'])
                admission = await worker.submit(event)
                assert admission['status'] == 'accepted'
                with lock:
                    report['worker_probe'].update(admission=admission, submit_at_ms=elapsed())
                    persist(report)
            elif request['type'] == 'finish_probe':
                admission = report['worker_probe']['admission']
                deadline = time.monotonic() + 125
                while True:
                    job = await asyncio.to_thread(worker.jobs.current, admission)
                    if job['state'] not in ('pending', 'extracting', 'validated', 'retry_wait'):
                        break
                    assert time.monotonic() < deadline, 'Real extraction did not terminate'
                    await asyncio.sleep(.05)
                facts = await asyncio.to_thread(worker.jobs.store.list_memories, scope.owner)
                matches = [x for x in facts if x['content'] == SOURCE]
                vectors = []
                with worker.jobs.store.lock:
                    for fact in matches:
                        row = worker.jobs.store.db.execute('SELECT normalized_vector AS vector,dimension,model_id,model_revision '
                            'FROM mia_vectors WHERE memory_id=?', (fact['id'],)).fetchone()
                        if row:
                            vectors.append({'memory_id': fact['id'], 'bytes': len(row['vector']),
                                'sha256': hashlib.sha256(row['vector']).hexdigest(), 'dimension': row['dimension'],
                                'model_id': row['model_id'], 'revision': row['model_revision']})
                calls = [x for x in report['extraction_requests'] if x['job_id'] == admission['id']]
                with lock:
                    report['worker_probe'].update(state=job['state'], attempts=job['attempts'],
                        submit_to_observed_terminal_seconds=(elapsed() - report['worker_probe']['submit_at_ms']) / 1000,
                        fact_ids=[x['id'] for x in matches], facts=matches, vectors=vectors, extraction_call_count=len(calls))
                    persist(report)
                assert job['state'] == 'committed' and 1 <= len(calls) <= 2 and job['attempts'] <= 2
                assert len(matches) == len(vectors) == 1 and vectors[0]['bytes'] == 768 * 4
                assert matches[0]['confirmed']
                assert matches[0]['source'] == SOURCE and matches[0]['session_id'] == 'p23-worker-overlap'
                assert vectors[0]['dimension'] == 768 and vectors[0]['model_id'] == worker.index.encoder.model_id
                assert vectors[0]['revision'] == worker.index.encoder.revision
                assert report['worker_probe']['submit_to_observed_terminal_seconds'] <= 120
                if overlap:
                    assert report['foreground_peak'] == 2
                    assert any(x.get('foreground_peak_before_handoff') == 2 for x in calls)
                else:
                    assert report['foreground_peak'] == 0 and not report['cases']
                report['worker_extraction_contract_passed'] = True
                report['worker_contract_passed'] = not report.get('case_errors') and not report.get('provider_errors')
                if overlap:
                    report['worker_overlap_contract_passed'] = report['worker_contract_passed']
                persist(report)
            else:
                assert request['type'] == 'probe_status'
            with lock:
                value = {'type': 'p23_worker', 'probe': report['worker_probe'],
                    'requests': report['extraction_requests'], 'foreground_peak': report['foreground_peak'],
                    'extraction_passed': report.get('worker_extraction_contract_passed', False),
                    'passed': report.get('worker_contract_passed', False)}
                serialized = json.dumps(value)
            await socket.send(serialized)
        except Exception as error:
            with lock:
                report.setdefault('case_errors', []).append({'error_class': type(error).__name__,
                    'assertion': str(error) if isinstance(error, AssertionError) else None})
                persist(report)
            await socket.send(json.dumps({'type': 'p23_error', 'error_class': type(error).__name__}))

    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(os.environ['MIA_TLS_CERT_FILE'], os.environ['MIA_TLS_KEY_FILE'])
    if os.environ['MIA_P23_MODE'] in ('worker-fairness', 'worker-fairness-diagnostic'):
        from youmi_worker_fairness import handler_for
        handler = handler_for(worker, scopes, report, lock, persist, elapsed)
    else:
        handler = connection
    commands = await serve(handler, '127.0.0.1', 0, ssl=tls)
    report['worker_driver_port'] = commands.sockets[0].getsockname()[1]
    persist(report)

    class Drivers:
        def close(self):
            commands.close()
            replies.close()
            watcher.cancel()

        async def wait_closed(self):
            await asyncio.gather(commands.wait_closed(), replies.wait_closed(), watcher, return_exceptions=True)

    return Drivers()


async def drive(state, report, persist):
    from core.auth import AuthManager
    from websockets.asyncio.client import connect

    peers = json.loads(os.environ['MIA_P23_PEERS'])
    tls = ssl.create_default_context(cafile=os.environ['MIA_SCOPE_CA'])
    transport = AuthManager('synthetic-p23-latency-transport')

    async def call(port, peer, value, expected):
        headers = {'device-id': peer['device_id'], 'client-id': peer['client_id'],
            'authorization': 'Bearer ' + transport.generate_token(peer['client_id'], peer['device_id']),
            'mia-private-credential': peer['private_credential']}
        async with connect(f'wss://127.0.0.1:{port}', ssl=tls, additional_headers=headers) as socket:
            await socket.send(json.dumps(value))
            while True:
                result = json.loads(await asyncio.wait_for(socket.recv(), 135))
                if result['type'] in (expected, 'p23_error'):
                    assert result['type'] == expected, 'Worker overlap contract failed; inspect sanitized child report'
                    return result

    async def command(kind):
        return await call(state['worker_port'], peers[2], {'type': kind}, 'p23_worker')

    await command('submit_probe')
    overlap = os.environ['MIA_P23_MODE'] == 'worker-smoke'
    if overlap:
        deadline = time.monotonic() + 15
        while True:
            value = await command('probe_status')
            if value['requests']:
                break
            assert time.monotonic() < deadline, 'No actual extraction SDK request observed'
            await asyncio.sleep(.05)
        values = await asyncio.gather(*[
            call(state['text_port'], peers[i], {'type': 'p23_reply', 'case': f'worker-smoke-front-{i}',
                'variant': 'memory', 'query_index': 1, 'warm': False}, 'p23_case') for i in range(2)], return_exceptions=True)
        report['foreground_cases'] = [x for x in values if not isinstance(x, BaseException)]
        report['foreground_errors'] = [{'error_class': type(x).__name__} for x in values if isinstance(x, BaseException)]
        persist(report)
    result = await command('finish_probe')
    report.update(worker_contract_passed=result['passed'], worker_extraction_contract_passed=result['extraction_passed'], worker_probe=result['probe'],
        extraction_requests=result['requests'], foreground_peak=result['foreground_peak'])
    if overlap:
        report['worker_overlap_contract_passed'] = result['passed'] and len(report['foreground_cases']) == 2
        child = json.loads((Path(os.environ['MIA_P23_REPORT_DIR']) / 'app-child/app-results.json').read_text())
        states = child['model_gate_transitions']
        entries = child['model_gate_sdk_entries']
        assert all(1 <= x['active'] <= 2 for x in entries)
        assert max(x['active'] for x in states) == 2
        assert any(x['waiting_foreground'] for x in states), 'No actual foreground request queued'
        report['model_gate_runtime_verified'] = True
        report['model_gate_transitions'] = states
        report['model_gate_sdk_entries'] = entries
    persist(report)
