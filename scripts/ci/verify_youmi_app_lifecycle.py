"""Actual app/Worker/BGE lifecycle in the shared P23 group; no chat acceptance."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import sys
import threading
import time


ROOT = Path(os.environ['MIA_P23_REPORT_DIR'])
CGROUP = Path('/sys/fs/cgroup/youmip23.slice')


def save(path, report):
    text = json.dumps(report, ensure_ascii=False, indent=2)
    values = [os.environ.get(name, '') for name in ('MIA_SCOPE_SERVER_SECRET', 'MIA_TEST_LLM_API_KEY')]
    values += [peer['private_credential'] for peer in json.loads(os.environ.get('MIA_P23_PEERS', '[]'))]
    for value in values:
        if value:
            text = text.replace(value, '[REDACTED]')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(text + '\n')
    temporary.replace(path)


def group_state():
    state = {name: (CGROUP / name).read_text().strip() for name in
        ('cpu.max', 'cpu.stat', 'memory.max', 'memory.swap.max', 'memory.current', 'memory.peak', 'memory.events')}
    assert state['cpu.max'] == '200000 100000'
    assert state['memory.max'] == '2147483648' and state['memory.swap.max'] == '0'
    return state


def members(app_pid):
    pids = json.loads(os.environ['MIA_P23_DATABASE_PIDS'])
    pids.update(manager=int(os.environ['MIA_P23_MANAGER_PID']), python=app_pid,
        harness=int(os.environ.get('MIA_P23_HARNESS_PID', os.getpid())))
    assert len(set(pids.values())) == 5
    result = {}
    for role, pid in pids.items():
        proc = Path('/proc') / str(pid)
        group = next(line[3:] for line in (proc / 'cgroup').read_text().splitlines() if line.startswith('0::'))
        assert group.startswith('/youmip23.slice/'), 'A service escaped the shared resource parent'
        status = (proc / 'status').read_text().splitlines()
        ticks = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]
        result[role] = {'pid': pid, 'start_ticks': ticks, 'cgroup': group, **{line.split(':')[0]: line.split(':', 1)[1].strip()
            for line in status if line.startswith(('VmRSS:', 'VmHWM:'))}}
    return result


async def serve():
    root = ROOT / 'app-child'
    root.mkdir(exist_ok=True)
    report = {'code_sha': os.environ['GITHUB_SHA'], 'mode': os.environ['MIA_P23_MODE'], 'pid': os.getpid(), 'ready': False,
        'passed': False, 'provider_requests': 0, 'accepted_extraction_jobs': 0,
        'scope': 'Original installed app.main, WS/HTTP/GC, idle persistent Worker and actual BGE; no audio/chat/extraction workload'}
    persist = lambda value: save(root / 'app-results.json', value)
    persist(report)
    task = None
    driver = None
    try:
        sys.path.insert(0, str(Path(os.environ['MIA_SCOPE_UPSTREAM']) / 'main/xiaozhi-server'))
        os.chdir(Path(os.environ['MIA_SCOPE_UPSTREAM']) / 'main/xiaozhi-server')
        os.environ['MIA_MEMORY_DB'] = str(root / 'memory.sqlite3')
        from youmi_behavior_runtime import bootstrap, launch
        config, _ = bootstrap()
        if os.environ['MIA_P23_MODE'] == 'worker-shutdown':
            from youmi_worker_shutdown import guard_recovery_provider, recovering
            if recovering():
                guard_recovery_provider(report, persist)
        from config.manage_api_client import ManageApiClient
        ManageApiClient(config)
        report['cgroup_before'] = group_state()
        report['members_before'] = members(os.getpid())
        started = time.monotonic()
        server, task = await launch(config, root, report, threading.Lock(), persist)
        report['app_ready_seconds'] = time.monotonic() - started
        worker = server._youmi_worker
        assert worker.task and not worker.task.done() and worker.index is not None
        peers = json.loads(os.environ['MIA_P23_PEERS'])
        scopes = [await worker.authorization.verify(peer['device_id'], peer['client_id'], peer['private_credential'])
            for peer in peers]
        assert len({scope.owner for scope in scopes}) == 3
        report['authorized_owners'] = [scope.owner for scope in scopes]
        report['members_before_encoder'] = members(os.getpid())
        report['python_peak_before_encoder_mib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        started = time.perf_counter()
        vector = await worker.index._encode('这是P23应用生命周期的独立机械检查。', query=True)
        report['cold_query_encode_ms'] = (time.perf_counter() - started) * 1000
        from youmi_companion import reply
        encoder = reply._encoder.encoder
        options = encoder._session.get_session_options()
        report['embedding'] = {'model_id': encoder.model_id, 'revision': encoder.revision,
            'dimension': encoder.dimension, 'vector_sha256': hashlib.sha256(vector.tobytes()).hexdigest(),
            'session_options': {'intra_op_num_threads': options.intra_op_num_threads,
                'inter_op_num_threads': options.inter_op_num_threads, 'providers': encoder._session.get_providers()}}
        assert options.intra_op_num_threads == options.inter_op_num_threads == 1
        report['python_peak_after_encoder_mib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        report['encoder_peak_increment_mib'] = (report['python_peak_after_encoder_mib']
            - report['python_peak_before_encoder_mib'])
        report['resource_scope'] = 'Integrated Python peaks and high-water increment; not isolated encoder RSS or idle-service capacity'
        report['members_after_encoder'] = members(os.getpid())
        report['integrity'] = worker.jobs.store.db.execute('PRAGMA integrity_check').fetchone()[0]
        assert report['integrity'] == 'ok'
        if os.environ['MIA_P23_MODE'] in ('reply-smoke', 'reply-stages', 'reply-stages-replay', 'reply-latency'):
            from youmi_reply_latency import open_driver
            report['scope'] = 'Actual app/services, original installed ConnectionHandler.chat, real Provider and persistent Worker; synthetic WSS text driver, audio omitted'
            driver = await open_driver(server, scopes, config, root, report, persist)
        elif os.environ['MIA_P23_MODE'] in ('worker-smoke', 'worker-extraction', 'worker-faults', 'worker-fairness', 'worker-fairness-diagnostic', 'worker-shutdown'):
            if os.environ['MIA_P23_MODE'] == 'worker-shutdown':
                from youmi_worker_shutdown import open_driver, recovering
                if recovering():
                    open_driver = None
            elif os.environ['MIA_P23_MODE'] == 'worker-faults':
                from youmi_worker_faults import open_driver
            else:
                from youmi_worker_competition import open_driver
            report['scope'] = 'Actual app/services and actual extraction Worker; per-mode probe/fault scope recorded separately, audio omitted'
            if open_driver is not None:
                driver = await open_driver(server, scopes, config, root, report, persist)
        report.update(ready=True, members_ready=members(os.getpid()), cgroup_ready=group_state())
        rss = lambda value: int(value['python']['VmRSS'].split()[0]) / 1024
        report['encoder_live_rss_increment_mib'] = rss(report['members_after_encoder']) - rss(report['members_before_encoder'])
        persist(report)
        save(root / 'ready.json', {'pid': os.getpid(), 'ports': report['app_lifecycles'][-1]['ports'],
            'text_port': report.get('text_driver_port'), 'worker_port': report.get('worker_driver_port')})
        await task  # Original app's SIGTERM handler and original stop_service own shutdown.
        assert len(report['app_shutdowns']) == 1 and report['app_shutdowns'][0]['drained']
        report['members_after_workload'] = members(os.getpid())
        report['python_peak_after_workload_mib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        report['passed'] = True
    except BaseException as error:
        report.update(error_class=type(error).__name__, reason=str(error)[:500])
        raise
    finally:
        if driver:
            driver.close()
            await driver.wait_closed()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        report['cgroup_final'] = group_state()
        persist(report)


async def control(report):
    ready = ROOT / 'app-child/ready.json'
    ready.unlink(missing_ok=True)
    environment = dict(os.environ, MIA_P23_HARNESS_PID=str(os.getpid()))
    with (ROOT / 'app-service.log').open('wb') as output:
        process = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).resolve()), '--serve',
            env=environment, stdin=asyncio.subprocess.PIPE, stdout=output, stderr=asyncio.subprocess.STDOUT)
        report['app_pid'] = process.pid
        save(ROOT / 'app-lifecycle-results.json', report)
        try:
            deadline = time.monotonic() + 90
            while not ready.exists():
                assert process.returncode is None, 'Actual app process exited before readiness; inspect sanitized child report'
                assert time.monotonic() < deadline, 'Actual app readiness timed out'
                await asyncio.sleep(.1)
            state = json.loads(ready.read_text())
            assert state['pid'] == process.pid
            report['members_ready'] = members(process.pid)
            report['cgroup_ready'] = group_state()
            if os.environ['MIA_P23_MODE'] in ('reply-smoke', 'reply-stages', 'reply-stages-replay', 'reply-latency'):
                sys.path.insert(0, str(Path(os.environ['MIA_SCOPE_UPSTREAM']) / 'main/xiaozhi-server'))
                from youmi_reply_latency import drive
                await drive(state, report, lambda value: save(ROOT / 'app-lifecycle-results.json', value))
                report['members_after_workload'] = members(process.pid)
            elif os.environ['MIA_P23_MODE'] in ('worker-smoke', 'worker-extraction', 'worker-faults', 'worker-fairness', 'worker-fairness-diagnostic', 'worker-shutdown'):
                sys.path.insert(0, str(Path(os.environ['MIA_SCOPE_UPSTREAM']) / 'main/xiaozhi-server'))
                if os.environ['MIA_P23_MODE'] == 'worker-shutdown':
                    from youmi_worker_shutdown import drive
                elif os.environ['MIA_P23_MODE'] == 'worker-faults':
                    from youmi_worker_faults import drive
                elif os.environ['MIA_P23_MODE'] in ('worker-fairness', 'worker-fairness-diagnostic'):
                    from youmi_worker_fairness import drive
                else:
                    from youmi_worker_competition import drive
                try:
                    await drive(state, report, lambda value: save(ROOT / 'app-lifecycle-results.json', value))
                except Exception as error:
                    report.update(worker_contract_passed=False, workload_error_class=type(error).__name__,
                        workload_assertion=str(error) if isinstance(error, AssertionError) else None)
                    save(ROOT / 'app-lifecycle-results.json', report)
                report['members_after_workload'] = members(process.pid)
            report['shutdown_signal_monotonic'] = time.monotonic()
            save(ROOT / 'app-lifecycle-results.json', report)
            process.send_signal(signal.SIGTERM)
            await asyncio.wait_for(process.wait(), 20)
            report['app_process_shutdown_seconds'] = time.monotonic() - report['shutdown_signal_monotonic']
            report['app_returncode'] = process.returncode
            assert process.returncode == 0, 'Actual app process failed during shutdown'
            child = json.loads((ROOT / 'app-child/app-results.json').read_text())
            assert child['passed'] and child['code_sha'] == report['code_sha']
            report['provider_requests'] = child['provider_requests']
            report['accepted_extraction_jobs'] = child['accepted_extraction_jobs']
            if os.environ['MIA_P23_MODE'] not in ('worker-smoke', 'worker-extraction', 'worker-faults', 'worker-fairness', 'worker-fairness-diagnostic', 'worker-shutdown'):
                assert not child.get('provider_errors') and not child.get('case_errors')
            for port in state['ports']:
                try:
                    reader, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', port), 1)
                except (OSError, asyncio.TimeoutError):
                    continue
                writer.close()
                await writer.wait_closed()
                raise AssertionError('Actual app socket remained open after process exit')
            report['app_lifecycle_preflight_passed'] = True
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            if process.stdin:
                process.stdin.close()


def main():
    mode = os.environ['MIA_P23_MODE']
    report = {'code_sha': os.environ['GITHUB_SHA'], 'mode': mode,
        'app_lifecycle_preflight_passed': False, 'p23_complete': False, 'provider_requests': 0,
        'scope': 'Actual full-import app/BGE/idle Worker lifecycle in common 2CPU/2GiB parent; not inflight/reply/Worker fairness acceptance',
        'remaining': ['same-Provider paired first-sentence delta', 'two actual foreground replies and persistent extraction',
            'task fairness/faults/120-second terminal latency', 'inflight application shutdown and durable recovery']}
    if mode in ('reply-smoke', 'reply-stages', 'reply-stages-replay', 'reply-latency'):
        report['scope'] = 'Actual app/services and installed chat, same-Provider empty-memory baseline and real original Worker; sequential pairs with audio omitted'
    elif mode in ('worker-smoke', 'worker-extraction', 'worker-faults', 'worker-fairness', 'worker-fairness-diagnostic', 'worker-shutdown'):
        report['scope'] = 'Actual app/services and real extraction task; per-mode worker contract, no natural-load percentile or audio claim'
    save(ROOT / 'app-lifecycle-results.json', report)
    try:
        report['cgroup_before'] = group_state()
        asyncio.run(control(report))
        if mode == 'worker-shutdown':
            from youmi_worker_shutdown import after_exit
            persist = lambda value: save(ROOT / 'app-lifecycle-results.json', value)
            after_exit(ROOT, report, persist)
            report['app_lifecycle_preflight_passed'] = False
            os.environ['MIA_P23_SHUTDOWN_STAGE'] = 'recover'
            asyncio.run(control(report))
            after_exit(ROOT, report, persist)
    except Exception as error:
        report.update(error_class=type(error).__name__, reason=str(error)[:500])
    finally:
        child_path = ROOT / 'app-child/app-results.json'
        if child_path.exists():
            child = json.loads(child_path.read_text())
            report['provider_requests'] = child['provider_requests']
            report['accepted_extraction_jobs'] = child['accepted_extraction_jobs']
            if (mode == 'worker-shutdown' and report.get('inflight_shutdown')
                    and os.environ.get('MIA_P23_SHUTDOWN_STAGE') == 'recover'):
                report['provider_requests'] += report['inflight_shutdown']['provider_requests']
                report['accepted_extraction_jobs'] += 1
        if mode == 'reply-latency' and report.get('first_sentence_acceptance_passed'):
            report['remaining'].remove('same-Provider paired first-sentence delta')
        if mode == 'worker-shutdown' and report.get('inflight_app_shutdown_recovery_passed'):
            report['remaining'].remove('inflight application shutdown and durable recovery')
        report['cgroup_final'] = group_state()
        events = dict(line.split() for line in report['cgroup_final']['memory.events'].splitlines())
        if any(int(events[key]) for key in ('oom', 'oom_kill')):
            report['app_lifecycle_preflight_passed'] = False
        save(ROOT / 'app-lifecycle-results.json', report)
    print(json.dumps({key: report[key] for key in ('app_lifecycle_preflight_passed', 'p23_complete', 'provider_requests')}))
    if (not report['app_lifecycle_preflight_passed']
            or (mode == 'reply-latency' and not report.get('first_sentence_acceptance_passed'))
            or (mode in ('worker-smoke', 'worker-extraction', 'worker-faults', 'worker-fairness', 'worker-fairness-diagnostic', 'worker-shutdown') and not report.get('worker_contract_passed'))):
        raise SystemExit(1)


def finalize():
    """Called outside the measured group after Java/databases/harness stop."""
    path = ROOT / 'app-lifecycle-results.json'
    report = json.loads(path.read_text())
    started = report.get('shutdown_signal_monotonic')
    elapsed = time.monotonic() - started if started is not None else None
    still_running = []
    for role, value in report.get('members_ready', {}).items():
        stat = Path('/proc') / str(value['pid']) / 'stat'
        try:
            ticks = stat.read_text().rsplit(')', 1)[1].split()[19]
        except FileNotFoundError:
            continue
        if ticks == value['start_ticks']:
            still_running.append(role)
    stopped = ROOT / 'container-stopped.txt'
    containers_clean = stopped.exists() and set(stopped.read_text().splitlines()) == {
        '/youmip23-mysql 0 false false', '/youmip23-redis 0 false false'}
    passed = (report['app_lifecycle_preflight_passed'] and elapsed is not None and containers_clean
        and elapsed <= 20 and not still_running and len(report.get('members_ready', {})) == 5)
    report['idle_service_group_shutdown'] = {'elapsed_seconds': elapsed,
        'databases_stopped_with_zero_exit_no_oom': containers_clean,
        'remaining_original_processes': still_running, 'passed': passed,
        'scope': 'From SIGTERM to actual app through Java/harness/MySQL/Redis exit; no work in flight, prior accepted jobs already terminal'}
    save(path, report)
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    if '--finalize' in sys.argv:
        finalize()
    elif '--serve' in sys.argv:
        asyncio.run(serve())
    else:
        main()
