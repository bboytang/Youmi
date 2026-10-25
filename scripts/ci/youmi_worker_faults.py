"""P23 real extraction deadlines/SQLite refusal/recovery; no output replacement."""

import asyncio
import hashlib
import json
import os
import sqlite3
import ssl
import threading
import time
import uuid

from youmi_worker_competition import SOURCE, active_job


CASES = (('worker_deadline', SOURCE), ('database_retry', SOURCE),
    ('recovery', '我每周三在餐桌边擦拭陶瓷茶杯'))


def reject_fact_insert(action, table, *unused):
    return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and table == 'memories' else sqlite3.SQLITE_OK


def allow_sqlite_operation(*unused):
    # Python 3.10 does not support disabling this callback with None.
    return sqlite3.SQLITE_OK


async def open_driver(server, scopes, config, root, report, persist):
    import core.utils.llm as existing_llm
    from core.auth import AuthManager
    from youmi_companion.jobs import TurnCompleted
    from youmi_companion.extraction import validate_output
    from youmi_reply_latency import open_driver as open_replies
    from websockets.asyncio.server import serve

    lock, local = threading.RLock(), threading.local()
    replies = await open_replies(server, scopes, config, root, report, persist, lock=lock)
    worker = server._youmi_worker
    initial_timeout = worker.extraction_timeout
    started = time.perf_counter()
    elapsed = lambda: (time.perf_counter() - started) * 1000
    report.update(fault_cases=[], fault_extraction_requests=[], fault_native_calls=[], sqlite_denials=[],
        first_sentence_cases_scope='No foreground SDK requests in sequential actual extraction fault contract',
        fault_scope='Original Worker/SDK/proposal/validation/BGE/SQLite. Inject a 200ms Worker await deadline '
            '(not a wire timeout); await real native completion, reject actual fact INSERT via SQLite authorizer, '
            'then remove refusal and verify validated-result reuse and a subsequent real source. No fake model outputs.')
    original_factory, original_resolve = existing_llm.create_instance, worker.resolve

    def factory(name, model):
        provider = original_factory(name, model)
        create = provider.client.chat.completions.create

        def observed(*args, **kwargs):
            context = local.context
            payload = json.loads(kwargs['messages'][1]['content'])
            assert payload['user_excerpts'] == context['sources']
            entry = dict(context, started_at_ms=elapsed(), model=kwargs['model'],
                messages_sha256=hashlib.sha256(json.dumps(kwargs['messages'], ensure_ascii=False).encode()).hexdigest(),
                tools_count=len(kwargs.get('tools', [])))
            assert entry['model'] == 'glm-4-flash-250414' and entry['tools_count'] == 1
            with lock:
                report['fault_extraction_requests'].append(entry)
                persist(report)
            try:
                return create(*args, **kwargs)
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
            entry = dict(local.context, started_at_ms=elapsed())
            with lock:
                report['fault_native_calls'].append(entry)
                persist(report)
            try:
                value = extractor(sources, context)
                # Audit a copy only; the original return value still goes to the
                # original Worker. This proves an otherwise valid late proposal.
                try:
                    audited = validate_output(json.loads(json.dumps(value)), sources, context)
                    validation = {'passed': True, 'explicit_source_present': any(
                        x['content'] == sources[0] and x['kind'] == 'explicit' and not x['sensitive']
                        for x in audited['memories'])}
                except Exception as error:
                    validation = {'passed': False, 'error_class': type(error).__name__}
                with lock:
                    entry['original_output_sha256'] = hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()
                    entry['original_output_validation'] = validation
                return value
            finally:
                with lock:
                    entry['finished_at_ms'] = elapsed()
                    persist(report)
                local.context = None

        return extract

    existing_llm.create_instance, worker.resolve = factory, resolve

    def authorizer(action, table, *rest):
        decision = reject_fact_insert(action, table, *rest)
        if decision == sqlite3.SQLITE_DENY:
            with lock:
                report['sqlite_denials'].append({'at_ms': elapsed(), 'action': action, 'table': table})
                persist(report)
        return decision

    def set_authorizer(callback):
        with worker.jobs.store.lock:
            worker.jobs.store.db.set_authorizer(callback if callback is not None else allow_sqlite_operation)

    async def finish(case, scope):
        admission = case['admission']
        deadline = time.monotonic() + 125
        while True:
            job = await asyncio.to_thread(worker.jobs.current, admission)
            if case['kind'] == 'database_retry' and job['state'] == 'retry_wait' and 'validated_retry' not in case:
                assert job['error_category'] == 'database' and job['validated_result'] is not None
                assert json.loads(job['validated_result'])['_schema'] == 1 and job['attempts'] == 1
                calls = [x for x in report['fault_extraction_requests'] if x['job_id'] == admission['id']]
                assert len(calls) == 1 and report['sqlite_denials']
                with lock:
                    case['validated_retry'] = {'state': job['state'], 'error_category': job['error_category'],
                        'attempts': job['attempts'], 'provider_requests_for_job': len(calls),
                        'validated_result_sha256': hashlib.sha256(job['validated_result'].encode()).hexdigest(),
                        'observed_at_ms': elapsed()}
                    persist(report)
                set_authorizer(None)
                case['refusal_removed_at_ms'] = elapsed()
            if job['state'] not in ('pending', 'extracting', 'validated', 'retry_wait'):
                break
            assert time.monotonic() < deadline, 'Actual fault task did not terminate'
            await asyncio.sleep(.05)
        # A timed-out caller cannot make a still-running SDK/native call disappear.
        native_deadline = time.monotonic() + 35
        while not worker.lane.done.is_set():
            assert time.monotonic() < native_deadline, 'Actual native extraction did not settle after caller timeout'
            await asyncio.sleep(.05)
        facts = await asyncio.to_thread(worker.jobs.store.list_memories, scope.owner)
        matches = [x for x in facts if x['content'] == case['source']]
        vectors = []
        with worker.jobs.store.lock:
            for fact in matches:
                row = worker.jobs.store.db.execute('SELECT normalized_vector,dimension,model_id,model_revision '
                    'FROM mia_vectors WHERE memory_id=?', (fact['id'],)).fetchone()
                if row:
                    vectors.append({'memory_id': fact['id'], 'bytes': len(row['normalized_vector']),
                        'dimension': row['dimension'], 'model_id': row['model_id'], 'revision': row['model_revision'],
                        'sha256': hashlib.sha256(row['normalized_vector']).hexdigest()})
        calls = [x for x in report['fault_extraction_requests'] if x['job_id'] == admission['id']]
        native = [x for x in report['fault_native_calls'] if x['job_id'] == admission['id']]
        with lock:
            case.update(state=job['state'], attempts=job['attempts'], error_category=job['error_category'],
                submit_to_observed_terminal_and_native_settled_seconds=(elapsed() - case['submitted_at_ms']) / 1000,
                facts=matches, vectors=vectors, sdk_calls=len(calls), native_calls=len(native), native_settled=True)
            persist(report)
        assert calls and len(calls) == len(native) and all('finished_at_ms' in x for x in native)
        assert worker.foreground == 0 and not report['cases']
        if case['kind'] == 'worker_deadline':
            assert job['state'] == 'failed' and job['error_category'] == 'timeout' and job['attempts'] == len(calls) == 2
            assert not matches and not vectors, 'Late native proposal reached the fact store after timeout'
            assert any(x.get('original_output_validation', {}).get('explicit_source_present') for x in native)
        else:
            assert job['state'] == 'committed' and job['error_category'] is None and 1 <= job['attempts'] <= 2
            assert len(matches) == len(vectors) == 1 and matches[0]['confirmed'] and matches[0]['source'] == case['source']
            assert matches[0]['session_id'] == case['session_id']
            assert case['submit_to_observed_terminal_and_native_settled_seconds'] <= 120
            assert vectors[0]['bytes'] == 3072 and vectors[0]['dimension'] == 768
            assert (vectors[0]['model_id'], vectors[0]['revision']) == (worker.index.encoder.model_id, worker.index.encoder.revision)
            if case['kind'] == 'database_retry':
                assert case['validated_retry'] and job['attempts'] == len(calls) == 1
        case['passed'] = True
        persist(report)

    transport = AuthManager('synthetic-p23-latency-transport')

    async def connection(socket):
        try:
            headers = dict(socket.request.headers)
            assert transport.verify_token(headers.get('authorization', '')[7:], headers.get('client-id'), headers.get('device-id'))
            scope = await worker.authorization.verify(headers['device-id'], headers['client-id'], headers['mia-private-credential'])
            assert scope.snapshot() == scopes[2].snapshot()
            request = json.loads(await socket.recv())
            index = request['case_index']
            assert type(index) is int and 0 <= index < len(CASES)
            if request['type'] == 'start_fault':
                assert index == len(report['fault_cases'])
                assert all(x.get('passed') for x in report['fault_cases']) and worker.lane.done.is_set()
                kind, source = CASES[index]
                worker.extraction_timeout = .2 if kind == 'worker_deadline' else initial_timeout
                set_authorizer(authorizer if kind == 'database_retry' else None)
                case = {'kind': kind, 'source': source, 'owner': scope.owner, 'submitted_at_ms': elapsed(),
                    'worker_await_timeout_seconds': worker.extraction_timeout, 'session_id': f'p23-fault-{index}'}
                event = TurnCompleted(scope, case['session_id'], uuid.uuid4().hex, uuid.uuid4().hex,
                    source, worker.jobs.store.snapshot(scope.owner)['revision'])
                admission = await worker.submit(event)
                assert admission['status'] == 'accepted'
                case['admission'] = admission
                report['fault_cases'].append(case)
                persist(report)
            else:
                assert request['type'] == 'finish_fault' and index == len(report['fault_cases']) - 1
                await finish(report['fault_cases'][index], scope)
                report['worker_fault_contract_passed'] = len(report['fault_cases']) == len(CASES) and all(x.get('passed') for x in report['fault_cases'])
                report['worker_contract_passed'] = report['worker_fault_contract_passed'] and not report.get('provider_errors') and not report.get('case_errors')
                persist(report)
            await socket.send(json.dumps({'type': 'p23_fault', 'case': report['fault_cases'][index],
                'passed': report.get('worker_contract_passed', False)}))
        except Exception as error:
            set_authorizer(None)
            worker.extraction_timeout = initial_timeout
            with lock:
                report.setdefault('case_errors', []).append({'error_class': type(error).__name__,
                    'assertion': str(error) if isinstance(error, AssertionError) else None})
                persist(report)
            await socket.send(json.dumps({'type': 'p23_error', 'error_class': type(error).__name__}))

    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(os.environ['MIA_TLS_CERT_FILE'], os.environ['MIA_TLS_KEY_FILE'])
    commands = await serve(connection, '127.0.0.1', 0, ssl=tls)
    report['worker_driver_port'] = commands.sockets[0].getsockname()[1]
    persist(report)

    class Drivers:
        def close(self):
            set_authorizer(None)
            worker.extraction_timeout = initial_timeout
            commands.close()
            replies.close()

        async def wait_closed(self):
            await asyncio.gather(commands.wait_closed(), replies.wait_closed())

    return Drivers()


async def drive(state, report, persist):
    from core.auth import AuthManager
    from websockets.asyncio.client import connect

    peer = json.loads(os.environ['MIA_P23_PEERS'])[2]
    tls = ssl.create_default_context(cafile=os.environ['MIA_SCOPE_CA'])
    transport = AuthManager('synthetic-p23-latency-transport')
    headers = {'device-id': peer['device_id'], 'client-id': peer['client_id'],
        'authorization': 'Bearer ' + transport.generate_token(peer['client_id'], peer['device_id']),
        'mia-private-credential': peer['private_credential']}
    report['fault_cases'] = []
    for index in range(len(CASES)):
        for kind in ('start_fault', 'finish_fault'):
            async with connect(f'wss://127.0.0.1:{state["worker_port"]}', ssl=tls, additional_headers=headers) as socket:
                await socket.send(json.dumps({'type': kind, 'case_index': index}))
                result = json.loads(await asyncio.wait_for(socket.recv(), 165))
                assert result['type'] == 'p23_fault', 'Actual Worker fault contract failed; inspect sanitized child report'
            if kind == 'finish_fault':
                report['fault_cases'].append(result['case'])
                persist(report)
    report.update(worker_fault_contract_passed=result['passed'], worker_contract_passed=result['passed'])
    persist(report)
