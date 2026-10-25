"""Frozen normal-load background Scope fairness, separate from foreground concurrency."""

import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import ssl
import statistics
import time
from types import SimpleNamespace
import uuid


FIXTURE = Path(__file__).resolve().parents[2] / 'server/tests/fixtures/persona_memory_worker_p23_v1.json'


def round_evidence(jobs, native):
    by_id = {x['admission']['id']: x for x in jobs}
    assert len(jobs) == len(by_id) == 6
    owners = {x['owner'] for x in jobs}
    assert len(owners) == 3 and all(sum(x['owner'] == owner for x in jobs) == 2 for owner in owners)
    calls = sorted((x for x in native if x['job_id'] in by_id), key=lambda x: x['started_at_ms'])
    order = []
    for call in calls:
        assert call['owner'] == by_id[call['job_id']]['owner']
        if call['job_id'] not in order:
            order.append(call['job_id'])
    assert set(order) == set(by_id), 'An admitted job has no actual native extraction entry'
    assert all('finished_at_ms' in x for x in calls)
    first_owners = [by_id[key]['owner'] for key in order[:3]]
    return {'claim_order': order, 'first_three_owners': first_owners,
        'scope_first_cycle_fair': len(set(first_owners)) == 3,
        'all_admitted_before_first_native_finished': max(x['admitted_at_ms'] for x in jobs) < calls[0]['finished_at_ms']}


def latency_summary(jobs):
    samples = [x['submit_to_observed_terminal_seconds'] for x in jobs]
    assert len(samples) >= 2
    return {'count': len(samples), 'p50_seconds': statistics.median(samples),
        'p95_seconds': statistics.quantiles(samples, n=100, method='inclusive')[94], 'samples_seconds': samples}


def task_verdict(jobs):
    assert len(jobs) == 36 and all('state' in x for x in jobs)
    measured = [x for x in jobs if not x['warm']]
    assert len(measured) == 30
    successful = [x for x in measured if x['state'] in ('committed', 'empty')]
    valid = all(x['state'] in ('committed', 'empty') for x in jobs)
    normal = latency_summary(measured) if len(successful) == 30 else None
    return {'terminal_task_outcomes': dict(Counter(x['state'] for x in measured)),
        'terminal_task_latency': latency_summary(measured),
        'successful_task_latency_exploratory': latency_summary(successful) if len(successful) >= 2 else None,
        'normal_task_latency': normal, 'all_normal_jobs_valid': valid,
        'normal_task_acceptance_passed': valid and normal is not None and normal['p95_seconds'] <= 120}


def handler_for(worker, scopes, report, lock, persist, elapsed):
    from core.auth import AuthManager
    from youmi_companion.jobs import TurnCompleted
    from youmi_companion.youmi_adapter import _user_excerpts

    fixture = json.loads(FIXTURE.read_text())
    diagnostic = os.environ['MIA_P23_MODE'] == 'worker-fairness-diagnostic'
    report.update(fair_jobs=[], fair_dataset_sha256=hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        worker_probe_scope=fixture['scope'], first_sentence_cases_scope='No foreground SDK requests; normal background fairness only')
    if diagnostic:
        report.update(worker_probe_scope='Diagnostic only: original frozen warm short input at Scope 0 and dense input at Scope 2; no fairness/normal-task percentile verdict',
            first_sentence_cases_scope='No foreground SDK requests in diagnostic collection')
    snapshots = {scope.owner: scope.snapshot() for scope in scopes}
    transport = AuthManager('synthetic-p23-latency-transport')

    async def connection(socket):
        try:
            headers = dict(socket.request.headers)
            assert transport.verify_token(headers.get('authorization', '')[7:], headers.get('client-id'), headers.get('device-id'))
            scope = await worker.authorization.verify(headers['device-id'], headers['client-id'], headers['mia-private-credential'])
            assert scope.owner in snapshots and scope.snapshot() == snapshots[scope.owner]
            request = json.loads(await socket.recv())
            number = request['round']
            assert type(number) is int and 0 <= number < len(fixture['rounds'])
            own = [x for x in report['fair_jobs'] if x['round'] == number and x['owner'] == scope.owner]
            if request['type'] == 'submit_fair':
                profile = request['profile']
                if diagnostic:
                    assert number == 0 and (scope.owner, profile) in ((scopes[0].owner, 'short'), (scopes[2].owner, 'dense'))
                assert profile in ('short', 'dense') and all(x['profile'] != profile for x in own)
                assert all('state' in x for x in report['fair_jobs'] if x['round'] < number)
                raw_source = fixture['rounds'][number][profile]
                message_id = uuid.uuid4().hex
                excerpts = _user_excerpts([SimpleNamespace(role='user', content=raw_source, uniq_id=message_id)])
                assert len(excerpts) == 1 and excerpts[0][0] == message_id
                source = excerpts[0][1]  # Same NFKC/bounded source path as original begin_turn.
                job = {'round': number, 'warm': number == 0, 'owner': scope.owner, 'profile': profile,
                    'source': source, 'raw_source': raw_source,
                    'session_id': f'p23-fair-{number}-{profile}', 'submitted_at_ms': elapsed()}
                event = TurnCompleted(scope, job['session_id'], uuid.uuid4().hex, message_id,
                    source, worker.jobs.store.snapshot(scope.owner)['revision'])
                admission = await worker.submit(event)
                assert admission['status'] == 'accepted'
                with lock:
                    job.update(admission=admission, admitted_at_ms=elapsed())
                    report['fair_jobs'].append(job)
                    persist(report)
                own = [job]
            else:
                assert request['type'] == 'finish_fair' and len(own) == (1 if diagnostic else 2)
                await worker.authorization.require(scope, 'memory:read')
                deadline = time.monotonic() + 125
                while True:
                    def current():
                        with worker.jobs.store.lock:
                            return [worker.jobs.current(x['admission']) for x in own]
                    rows = await asyncio.to_thread(current)
                    with lock:
                        for job, row in zip(own, rows):
                            assert row['owner'] == scope.owner
                            if 'state' not in job and row['state'] not in ('pending', 'extracting', 'validated', 'retry_wait'):
                                job.update(state=row['state'], attempts=row['attempts'], error_category=row['error_category'],
                                    submit_to_observed_terminal_seconds=(elapsed() - job['submitted_at_ms']) / 1000)
                        persist(report)
                    if all('state' in x for x in own):
                        break
                    assert time.monotonic() < deadline, 'Normal background jobs did not terminate'
                    await asyncio.sleep(.05)
                assert all(1 <= x['attempts'] <= 2 for x in own)
                for job in own:
                    calls = [x for x in report['extraction_requests'] if x['job_id'] == job['admission']['id']]
                    assert len(calls) == job['attempts'], 'Normal factual task bypassed the real SDK or made an uncounted call'
                    assert all(x['owner'] == scope.owner and x['sources'] == [job['source']] for x in calls)
                assert worker.foreground == 0 and not report['cases']
                report['worker_fairness_contract_passed'] = (len(report['fair_jobs']) == 36
                    and all(x.get('state') in ('committed', 'empty') for x in report['fair_jobs'])
                    and not report.get('provider_errors') and not report.get('case_errors'))
                if diagnostic:
                    report['worker_diagnostic_complete'] = len(report['fair_jobs']) == 2 and all('state' in x for x in report['fair_jobs'])
                persist(report)
            # A peer receives only its own jobs; cross-Scope audit stays server-side.
            await socket.send(json.dumps({'type': 'p23_fair', 'jobs': own}))
        except Exception as error:
            with lock:
                report.setdefault('case_errors', []).append({'error_class': type(error).__name__,
                    'assertion': str(error) if isinstance(error, AssertionError) else None})
                persist(report)
            await socket.send(json.dumps({'type': 'p23_error', 'error_class': type(error).__name__}))

    return connection


async def drive(state, report, persist):
    from core.auth import AuthManager
    from websockets.asyncio.client import connect

    peers = json.loads(os.environ['MIA_P23_PEERS'])
    tls = ssl.create_default_context(cafile=os.environ['MIA_SCOPE_CA'])
    transport = AuthManager('synthetic-p23-latency-transport')
    report.update(fair_jobs=[], fair_rounds=[], fair_dataset_sha256=hashlib.sha256(FIXTURE.read_bytes()).hexdigest())

    async def command(peer, value):
        headers = {'device-id': peer['device_id'], 'client-id': peer['client_id'],
            'authorization': 'Bearer ' + transport.generate_token(peer['client_id'], peer['device_id']),
            'mia-private-credential': peer['private_credential']}
        async with connect(f'wss://127.0.0.1:{state["worker_port"]}', ssl=tls, additional_headers=headers) as socket:
            await socket.send(json.dumps(value))
            result = json.loads(await asyncio.wait_for(socket.recv(), 135))
            assert result['type'] == 'p23_fair', 'Normal Worker fairness contract failed; inspect sanitized child report'
            return result['jobs']

    if os.environ['MIA_P23_MODE'] == 'worker-fairness-diagnostic':
        report['diagnostic_jobs'] = []
        for peer_index, profile in ((0, 'short'), (2, 'dense')):
            await command(peers[peer_index], {'type': 'submit_fair', 'round': 0, 'profile': profile})
            jobs = await command(peers[peer_index], {'type': 'finish_fair', 'round': 0})
            report['diagnostic_jobs'].extend(jobs)
            persist(report)
        child = json.loads((Path(os.environ['MIA_P23_REPORT_DIR']) / 'app-child/app-results.json').read_text())
        assert child['worker_diagnostic_complete'] and len(child['extraction_streams']) == child['provider_requests']
        report.update(worker_diagnostic_complete=True, worker_contract_passed=True,
            fair_measurement_scope='Diagnostic collection only: two original frozen warm inputs/Scopes, no normal-load or D08 verdict. Failed model/parse outcomes are retained.')
        persist(report)
        return

    for number in range(6):
        await asyncio.gather(*[command(peer, {'type': 'submit_fair', 'round': number, 'profile': profile})
            for peer in peers for profile in ('short', 'dense')])
        values = await asyncio.gather(*[command(peer, {'type': 'finish_fair', 'round': number}) for peer in peers],
            return_exceptions=True)
        jobs = [job for value in values if not isinstance(value, BaseException) for job in value]
        report['fair_jobs'].extend(jobs)
        persist(report)
        assert all(not isinstance(x, BaseException) for x in values), 'A normal Scope did not finish successfully'
        child = json.loads((Path(os.environ['MIA_P23_REPORT_DIR']) / 'app-child/app-results.json').read_text())
        evidence = round_evidence(jobs, child['native_extractions'])
        report['fair_rounds'].append(dict(evidence, round=number, warm=number == 0))
        persist(report)
        assert evidence['all_admitted_before_first_native_finished'], 'No complete three-Scope backlog to assess fairness'
        assert evidence['scope_first_cycle_fair'], 'One Scope was served twice before all backlogged Scopes received service'
    measured = [x for x in report['fair_jobs'] if not x['warm']]
    report.update(task_verdict(report['fair_jobs']))
    if report['normal_task_latency'] is not None:
        report['normal_task_latency_by_profile'] = {profile: latency_summary([x for x in measured if x['profile'] == profile])
            for profile in ('short', 'dense')}
        report['normal_task_latency_by_owner'] = {owner: latency_summary([x for x in measured if x['owner'] == owner])
            for owner in {x['owner'] for x in measured}}
    passed = report['normal_task_acceptance_passed'] and child['worker_fairness_contract_passed']
    report.update(scope_fairness_sampling_passed=True, fairness_batch_complete=True,
        worker_fairness_contract_passed=child['worker_fairness_contract_passed'], worker_contract_passed=passed,
        fair_measurement_scope='Six rounds of two real factual tasks per Scope; first round warm, five measured. '
            'Invalid outputs remain failed and are never normal-success latency samples. '
            'Terminal latency includes failures and is a separate diagnostic. '
            'Original single background lane and three owner backlogs; no foreground SDK or same-Provider first-sentence verdict.')
    persist(report)
