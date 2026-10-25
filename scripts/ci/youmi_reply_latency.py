"""P23 real Provider/text reply timing; no P22 behavior or audio-quality claims."""

import asyncio
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import ssl
import threading
import time
import uuid


MODEL = 'glm-4-flash-250414'
REQUEST_CAPS = {'reply-smoke': 4, 'reply-stages': 4, 'reply-stages-replay': 0, 'reply-latency': 144, 'worker-smoke': 8,
    'worker-extraction': 2, 'worker-faults': 6, 'worker-fairness': 72,
    'worker-fairness-diagnostic': 4, 'worker-shutdown': 2}


async def read_case(socket, timeout=130):
    """Intermediate original TTS/LLM events are not the driver terminal result."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError()
        value = json.loads(await asyncio.wait_for(socket.recv(), remaining))
        if value['type'] in ('p23_case', 'p23_error'):
            return value


def empty_memory_request(builder, scope, limits, query, role, session):
    """Baseline: original context assembly/core/role, without any memory lookup.

    Baseline generation uses the same Provider and original phrase guard. It
    omits retrieval, snapshots and per-delta context authorization, whose cost
    is part of the integrated path's additional latency. It admits no jobs.
    """
    inputs = builder._prepare(query, [], [], role, [], None)
    inputs.update(facts=[], traits=[], events=[], emotion_trend={'status': 'not_relevant'},
        memory_status='available', session_id=session)
    return builder._assemble(scope, uuid.uuid4().hex, limits, None, inputs).request()


def observe_provider(configuration, purpose, report, lock, persist, factory):
    import httpx
    from youmi_model_comparison import safe_provider_error
    from youmi_tts_timing import case_elapsed_ms

    assert configuration['model_name'] == MODEL
    assert configuration['api_key'] == os.environ['MIA_TEST_LLM_API_KEY']
    assert configuration['base_url'] == os.environ['MIA_TEST_LLM_BASE_URL']
    provider = factory(configuration['type'], configuration)
    old = provider.client
    provider.client = old.with_options(max_retries=0, timeout=30,
        http_client=httpx.Client(timeout=30, follow_redirects=False))
    old.close()
    create = provider.client.chat.completions.create
    provider._p23_sample = None

    def save_observation():
        trace = getattr(provider, '_p23_stage_trace', None)
        with trace.span('observer.persist') if trace else nullcontext():
            persist(report)

    def counted(*args, **kwargs):
        from youmi_companion.model_requests import model_requests
        sample = provider._p23_sample
        capture = None
        started = time.perf_counter()
        with lock:
            report.setdefault('model_gate_sdk_entries', []).append(dict(model_requests.snapshot(), purpose=purpose))
            assert report['provider_requests'] < report['provider_request_cap'], 'P23 real request cap exceeded'
            report['provider_requests'] += 1
            report.setdefault('provider_request_purposes', []).append(purpose)
            if ((purpose == 'extraction' and report.get('capture_extraction_stream'))
                    or (purpose == 'reply' and report.get('capture_reply_stream'))):
                from youmi_extraction_diagnostics import StreamCapture
                capture = StreamCapture(report['provider_requests'], kwargs)
                report.setdefault('reply_streams' if purpose == 'reply' else 'extraction_streams', []).append(capture.entry)
            if sample is not None:
                assert 'sdk_start_ms' not in sample, 'Expected a single real reply request'
                sample['sdk_start_ms'] = case_elapsed_ms(sample, started)
                messages = kwargs['messages']
                data = json.loads(messages[1]['content'])['youmi_context']
                sample['sdk_request'] = {'model': kwargs['model'], 'max_tokens': kwargs['max_tokens'],
                    'messages_sha256': hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode()).hexdigest(),
                    'system_sha256': hashlib.sha256(messages[0]['content'].encode()).hexdigest(),
                    'fact_ids': [row['id'] for row in data['facts']], 'events': len(data.get('events', [])),
                    'last_message': messages[-1], 'tools_count': len(kwargs.get('tools', []))}
            save_observation()
        try:
            response = create(*args, **kwargs)
        except Exception as error:
            with lock:
                report.setdefault('provider_errors', []).append(dict(safe_provider_error(error), purpose=purpose))
                if capture:
                    capture.entry['create_error_class'] = type(error).__name__
                    capture.finish()
                save_observation()
            raise
        if not kwargs.get('stream'):
            return response

        class Stream:
            def __iter__(self):
                try:
                    for chunk in response:
                        arrived = None
                        if sample is not None and 'sdk_first_content_ms' not in sample:
                            if any(getattr(choice.delta, 'content', None) for choice in chunk.choices):
                                arrived = time.perf_counter()
                        trace = getattr(provider, '_p23_stage_trace', None)
                        with trace.span('observer.sdk_chunk') if trace else nullcontext():
                            with lock:
                                if capture:
                                    capture.append(chunk)
                                if arrived is not None:
                                    sample['sdk_first_content_ms'] = case_elapsed_ms(sample, arrived)
                        yield chunk
                except Exception as error:
                    with lock:
                        report.setdefault('provider_errors', []).append(dict(safe_provider_error(error), purpose=purpose))
                        save_observation()
                    raise
                finally:
                    if capture:
                        with lock:
                            capture.finish()
                            save_observation()

            def close(self):
                try:
                    response.close()
                finally:
                    if capture:
                        with lock:
                            capture.finish()
                            save_observation()

        return Stream()

    provider.client.chat.completions.create = counted
    return provider


async def open_driver(server, scopes, config, root, report, persist, *, lock=None):
    import core.utils.llm as existing_llm
    from core.auth import AuthManager
    from core.connection import ConnectionHandler
    from core.utils.dialogue import Dialogue
    from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType
    from youmi_companion.authorization import load_private_config
    from youmi_companion.context import ContextBuilder
    from youmi_companion import reply, youmi_adapter as adapter
    from verify_youmi_performance import populate
    from websockets.asyncio.server import serve
    from youmi_tts_timing import TIMING_REVISION, make_probe, verify_original_text_stage

    lock = lock if lock is not None else threading.RLock()
    factory = existing_llm.create_instance
    worker, index = server._youmi_worker, server._youmi_worker.index
    fixture_path = Path(__file__).resolve().parents[2] / 'server/tests/fixtures/persona_memory_performance_p23_v1.json'
    fixture = json.loads(fixture_path.read_text())
    cap = REQUEST_CAPS[os.environ['MIA_P23_MODE']]
    report.update(provider_request_cap=cap,
        provider_request_purposes=[], cases=[], accepted_extraction_jobs=0, job_submit_times={},
        model=MODEL, dataset_sha256=hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        fixture_kind='Numbered synthetic capacity rows repeat actual BGE seed vectors; no claim of model-extracted bulk facts or semantic quality',
        timing_revision=TIMING_REVISION,
        timing_stage='First nonempty text after original TTS consumer segmentation and text preprocessing; no waveform/audio timing',
        sdk_timing_scope='All *_ms timestamps use case start; SDK interval is first-content minus SDK-start. Includes remote compute, transport and consumer/observer overhead, not pure network time',
        baseline_scope='Same Provider/core/role with original empty context assembly and phrase guard; no retrieval/context validation/jobs')
    report['tts_stage_mechanism'] = await verify_original_text_stage(server.logger)
    stages_mode = os.environ['MIA_P23_MODE'] in ('reply-stages', 'reply-stages-replay')
    if stages_mode:
        from youmi_stage_trace import calibrate
        from youmi_tts_boundaries import diagnose
        report.update(stage_observer_calibration=calibrate(), capture_reply_stream=True,
            capture_extraction_stream=True, controlled_sdk_call_cap=4,
            diagnostic_scope='10k stage diagnosis; per-mode fixed remote cap, four controlled SDK ABBA replays, no formal p95')
        boundaries = await diagnose(server.logger)
        raw_boundaries = (json.dumps(boundaries, ensure_ascii=False, indent=2) + '\n').encode()
        boundary_file = root / 'tts-boundaries.json'
        boundary_file.write_bytes(raw_boundaries)
        report['tts_boundary_diagnosis'] = {'file': boundary_file.name, 'passed': boundaries['passed'],
            'case_count': len(boundaries['cases']), 'sha256': hashlib.sha256(raw_boundaries).hexdigest(),
            'scope': 'Full controlled component evidence saved before requests; avoid serializing it on every SDK observation'}

    def extract_factory(name, model):
        assert cap > 0, 'Remote model factory disabled in controlled replay'
        assert name == model['type']
        return observe_provider(model, 'extraction', report, lock, persist, factory)

    existing_llm.create_instance = extract_factory
    submit = worker.submit

    async def observed_submit(event):
        started = time.perf_counter()
        result = await submit(event)
        if result.get('status') == 'accepted':
            with lock:
                report['accepted_extraction_jobs'] += 1
                report['job_submit_times'][result['id']] = started
                persist(report)
        return result

    worker.submit = observed_submit
    vectors = {(i, 'dense'): await index._encode(seed['dense']) for i, seed in enumerate(fixture['records'])}
    for scope, size in zip(scopes, (1000, 10000, 32)):
        await asyncio.to_thread(populate, worker.jobs.store, scope, size, 'dense', fixture, vectors)
    report['capacity_per_owner'] = dict(zip((scope.owner for scope in scopes), (1000, 10000, 32)))
    if os.environ['MIA_P23_MODE'] == 'reply-stages-replay':
        from youmi_v2_diagnostics import run as v2_diagnostics
        await v2_diagnostics(worker, scopes, fixture['queries'][0], root, report, persist)
        # The real-pair mode warms this index before ABBA. Its zero-remote
        # replay needs explicit fixed warm-up, rather than a cold first arm.
        for _ in range(5):
            await index.retrieve(scopes[1], worker.authorization, fixture['queries'][0])
        report['controlled_warmup_reads'] = 5
        report['controlled_warmup_scope'] = 'Fixed same-query retrieval only, no model requests or selected timing samples'
    report['first_sentence_cases_scope'] = 'Sequential paired replies; real original Worker drains each admitted turn; not two-foreground fairness'
    transport = AuthManager('synthetic-p23-latency-transport')

    async def connection(socket):
        conn = provider = probe = trace = None
        sample = None
        try:
            headers = dict(socket.request.headers)
            assert transport.verify_token(headers.get('authorization', '')[7:], headers.get('client-id'), headers.get('device-id'))
            request = json.loads(await socket.recv())
            assert request['type'] == 'p23_reply' and request['variant'] in ('baseline', 'memory')
            assert type(request['query_index']) is int and 0 <= request['query_index'] < len(fixture['queries'])
            query = fixture['queries'][request['query_index']]
            sample = {'case': request['case'], 'variant': request['variant'], 'query': query,
                'warm': request['warm'], 'timing_revision': TIMING_REVISION,
                'controlled': bool(request.get('controlled', False))}
            assert not sample['controlled'] or stages_mode
            if stages_mode and (not sample['controlled'] or request['trace']):
                from youmi_stage_trace import StageTrace
                sample['started'] = time.perf_counter()
                trace = StageTrace(sample)
                trace.install(worker.jobs.store)
            sample['trace_enabled'] = trace is not None
            conn = object.__new__(ConnectionHandler)
            conn.__dict__.update(config=config, headers=headers, websocket=socket, read_config_from_api=True,
                need_bind=False, dialogue=Dialogue(), session_id=uuid.uuid4().hex, sentence_id=uuid.uuid4().hex,
                loop=asyncio.get_running_loop(), client_abort=False, stop_event=threading.Event(), logger=server.logger,
                intent_type='nointent', current_speaker=None, system_introduced_speakers=set(), features={'emoji': False})
            private = await load_private_config(conn)
            scope = conn._youmi_scope
            assert scope.owner in report['capacity_per_owner']
            model = private['LLM'][private['selected_module']['LLM']]
            probe = conn.tts = make_probe(conn, sample, lock, {'correct_words': private['correct_words']})
            if sample['controlled']:
                from youmi_stage_trace import controlled_provider
                provider = conn.llm = controlled_provider(factory, model, report, sample)
            else:
                provider = conn.llm = observe_provider(model, 'reply', report, lock, persist, factory)
            provider._p23_stage_trace = trace
            profile = root / 'context-profile.json'
            profile.write_text(json.dumps({'provider_module': type(provider).__module__, 'model': provider.model_name,
                'base_url': provider.base_url, 'context_window': 16384,
                'capacity_source': 'P23 isolated short-request test envelope, not production capacity'}))
            os.environ['MIA_CONTEXT_PROFILE_FILE'] = str(profile)
            conn._youmi_context_builder = ContextBuilder(index)
            conn.memory = None
            conn._initialize_memory()
            assert conn._youmi_memory.store is worker.jobs.store
            conn.change_system_prompt(private['prompt'])
            adapter.attach_output_guard(conn)
            conn.server = server
            provider._p23_sample = sample
            started = time.perf_counter()
            sample.update(scope_owner=scope.owner, capacity=report['capacity_per_owner'][scope.owner],
                started=started, case_start_monotonic_seconds=started, case_start_ms=0.0)
            with lock:
                report['cases'].append(sample)
                persist(report)
            if request['variant'] == 'baseline':
                limits = reply.provider_limits(provider)
                baseline = empty_memory_request(conn._youmi_context_builder, scope, limits, query,
                    getattr(conn, '_youmi_role_data', ''), conn.session_id)

                def generate():
                    queue = conn.tts.tts_text_queue
                    queue.put(TTSMessageDTO(conn.sentence_id, SentenceType.FIRST, ContentType.ACTION))
                    from youmi_companion.model_requests import model_requests
                    with model_requests.acquire(foreground=True, timeout=10):
                        stream = provider.response(conn.session_id, baseline['messages'], max_tokens=baseline['max_tokens'])
                        try:
                            for text in stream:
                                queue.put(TTSMessageDTO(conn.sentence_id, SentenceType.MIDDLE, ContentType.TEXT, text))
                        finally:
                            stream.close()
                    queue.put(TTSMessageDTO(conn.sentence_id, SentenceType.LAST, ContentType.ACTION))

                await asyncio.to_thread(generate)
                sample['chat_completed'] = None  # Explicit baseline, not a claim that original chat ran.
            else:
                completed = await asyncio.to_thread(conn.chat, query)
                with lock:
                    sample['chat_completed'] = completed
                assert sample['chat_completed'] is True and conn._youmi_context_status['status'] == 'ready'
                status = conn._youmi_job_status
                assert status and status['status'] == 'accepted'
                with lock:
                    sample['admission'] = status
                accepted = report['job_submit_times'][status['id']]
                deadline = time.monotonic() + 125
                while True:
                    job = await asyncio.to_thread(worker.jobs.current, status)
                    if job['state'] not in ('pending', 'extracting', 'validated', 'retry_wait'):
                        break
                    assert time.monotonic() < deadline, 'Actual admitted extraction job did not terminate'
                    await asyncio.sleep(.05)
                with lock:
                    sample['job'] = {'id': job['id'], 'state': job['state'], 'attempts': job['attempts'],
                        'submit_to_observed_terminal_seconds': time.perf_counter() - accepted}
                assert job['state'] in ('committed', 'empty'), 'Normal reply extraction did not reach saved/valid empty'
                assert job['attempts'] <= 2
            assert await asyncio.to_thread(probe.drained.wait, 2), 'Original TTS text processing did not drain'
            with lock:
                sample['output_replacement'] = getattr(conn, '_youmi_output_replacement', None)
                assert not sample['output_replacement'] and 'first_sentence_ms' in sample and 'sdk_first_content_ms' in sample
                sample['total_case_ms'] = (time.perf_counter() - sample['started']) * 1000
                from youmi_tts_timing import stage_durations
                sample['stages'] = stage_durations(sample)
                if trace:
                    sample['stage_trace'] = trace.report()
                    assert not sample['stage_trace']['dropped'], 'Stage trace capacity exceeded'
                sample.pop('started')
                persist(report)
            await conn._youmi_authorization.require(scope, 'memory:read')
            await socket.send(json.dumps({'type': 'p23_case', **sample}))
        except Exception as error:
            with lock:
                report.setdefault('case_errors', []).append({'error_class': type(error).__name__,
                    'assertion': str(error) if isinstance(error, AssertionError) else None})
                persist(report)
            await socket.send(json.dumps({'type': 'p23_error', 'error_class': type(error).__name__}))
        finally:
            if trace:
                if sample is not None and 'started' in sample:
                    sample['stage_trace'] = trace.report()
                    sample.pop('started')
                trace.close()
                persist(report)
            if probe:
                await probe.close()
            if provider:
                provider.client.close()

    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(os.environ['MIA_TLS_CERT_FILE'], os.environ['MIA_TLS_KEY_FILE'])
    driver = await serve(connection, '127.0.0.1', 0, ssl=tls)
    report['text_driver_port'] = driver.sockets[0].getsockname()[1]
    persist(report)
    return driver


async def drive(state, report, persist):
    from core.auth import AuthManager
    from verify_youmi_performance import summary
    from youmi_performance_budgets import LIMITS, OPTIMIZATION_TARGETS, REVISION
    from websockets.asyncio.client import connect
    from youmi_tts_timing import TIMING_REVISION

    replay = os.environ['MIA_P23_MODE'] == 'reply-stages-replay'
    stages = os.environ['MIA_P23_MODE'] in ('reply-stages', 'reply-stages-replay')
    smoke = os.environ['MIA_P23_MODE'] in ('reply-smoke', 'reply-stages', 'reply-stages-replay')
    peers = json.loads(os.environ['MIA_P23_PEERS'])
    tls = ssl.create_default_context(cafile=os.environ['MIA_SCOPE_CA'])
    transport = AuthManager('synthetic-p23-latency-transport')
    rows = []
    report.update(reply_cases=rows, acceptance_revision=REVISION, limits=LIMITS,
        optimization_targets=OPTIMIZATION_TARGETS, timing_revision=TIMING_REVISION)
    for owner_index in (() if replay else (1,) if stages else range(1 if smoke else 2)):
        peer = peers[owner_index]
        headers = {'device-id': peer['device_id'], 'client-id': peer['client_id'],
            'authorization': 'Bearer ' + transport.generate_token(peer['client_id'], peer['device_id']),
            'mia-private-credential': peer['private_credential']}
        for query_index in range(1 if smoke else 3):
            row = {'capacity': (1000, 10000)[owner_index], 'query_index': query_index, 'pairs': []}
            rows.append(row)
            for number in range(1 if smoke else 6):
                pair = {'number': number, 'warm': False if smoke else number == 0, 'variants': {}}
                row['pairs'].append(pair)
                order = ('baseline', 'memory') if number % 2 == 0 else ('memory', 'baseline')
                pair['order'] = order
                for variant in order:
                    async with connect('wss://127.0.0.1:' + str(state['text_port']), ssl=tls,
                            additional_headers=headers, open_timeout=10) as socket:
                        await socket.send(json.dumps({'type': 'p23_reply', 'case': f'{owner_index}-{query_index}-{number}-{variant}',
                            'variant': variant, 'query_index': query_index, 'warm': pair['warm']}))
                        value = await read_case(socket)
                        if stages and value['type'] == 'p23_error':
                            pair['variants'][variant] = value
                            persist(report)
                            continue
                        assert value['type'] == 'p23_case', 'Actual reply contract failed; inspect sanitized child report'
                        assert value['timing_revision'] == TIMING_REVISION
                        pair['variants'][variant] = value
                        persist(report)
                baseline, memory = (pair['variants'][name] for name in ('baseline', 'memory'))
                if stages and any(x['type'] != 'p23_case' for x in (baseline, memory)):
                    row['diagnostic_failed'] = True
                    continue
                assert baseline['sdk_request']['model'] == memory['sdk_request']['model'] == MODEL
                assert baseline['sdk_request']['system_sha256'] == memory['sdk_request']['system_sha256']
                assert baseline['sdk_request']['last_message'] == memory['sdk_request']['last_message']
                assert not baseline['sdk_request']['fact_ids'] and memory['sdk_request']['fact_ids']
                assert all(value.startswith(memory['scope_owner'] + '-') for value in memory['sdk_request']['fact_ids'])
                pair['first_sentence_delta_ms'] = memory['first_sentence_ms'] - baseline['first_sentence_ms']
                pair['first_checked_text_delta_ms'] = memory['first_checked_text_ms'] - baseline['first_checked_text_ms']
            measured = [p['first_sentence_delta_ms'] for p in row['pairs'] if not p['warm'] and 'first_sentence_delta_ms' in p]
            if not measured:
                continue
            row['first_sentence_delta'] = summary(measured)
            row['first_checked_text_delta'] = summary([p['first_checked_text_delta_ms'] for p in row['pairs'] if not p['warm']])
            if not smoke:
                assert len(measured) == 5
                row['acceptance_passed'] = row['first_sentence_delta']['p95_ms'] <= LIMITS['first_sentence_delta_p95_ms']
                row['optimization_targets_met'] = row['first_sentence_delta']['p95_ms'] <= OPTIMIZATION_TARGETS['first_sentence_delta_p95_ms']
    if stages:
        report['controlled_abba'] = []
        peer = peers[1]
        headers = {'device-id': peer['device_id'], 'client-id': peer['client_id'],
            'authorization': 'Bearer ' + transport.generate_token(peer['client_id'], peer['device_id']),
            'mia-private-credential': peer['private_credential']}
        for number, enabled in enumerate((False, True, True, False)):
            async with connect('wss://127.0.0.1:' + str(state['text_port']), ssl=tls,
                    additional_headers=headers, open_timeout=10) as socket:
                await socket.send(json.dumps({'type': 'p23_reply', 'case': 'controlled-' + str(number),
                    'variant': 'memory', 'query_index': 0, 'warm': False, 'controlled': True, 'trace': enabled}))
                value = await read_case(socket)
                report['controlled_abba'].append(value)
                persist(report)
        assert all(x['type'] == 'p23_case' for x in report['controlled_abba']), 'Controlled stage replay failed; all results preserved'
        assert len({x['first_sentence'] for x in report['controlled_abba']}) == 1
    report['reply_contract_passed'] = not any(row.get('diagnostic_failed') for row in rows)
    report['first_sentence_acceptance_passed'] = None if smoke else all(r['acceptance_passed'] for r in rows)
    report['first_sentence_optimization_targets_met'] = None if smoke else all(r['optimization_targets_met'] for r in rows)
    report['reply_measurement_scope'] = 'Smoke contract only, one pair; no D08 percentile verdict' if smoke else 'Three queries per Scope capacity; one warm pair then five measured pairs, alternating order; negative deltas retained'
    persist(report)
