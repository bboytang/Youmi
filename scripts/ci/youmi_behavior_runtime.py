"""Actual installed app/services; only CI configuration and native hold are controlled."""

import asyncio
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import time


def configuration():
    ports = []
    for _ in range(2):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            ports.append(listener.getsockname()[1])
    assert ports[0] != ports[1]
    return {'read_config_from_api': True, 'selected_module': {},
            'server': {'ip': '127.0.0.1', 'port': ports[0], 'http_port': ports[1],
                       'auth_key': 'synthetic-lifecycle-transport', 'auth': {'enabled': True}},
            'manager-api': {'url': os.environ['MIA_SCOPE_MANAGER_URL'],
                            'secret': os.environ['MIA_SCOPE_SERVER_SECRET']},
            'log': {'log_level': 'CRITICAL', 'log_dir': '/tmp/mia-runtime-logs',
                    'data_dir': '/tmp/mia-runtime-data'}}


def bootstrap():
    # Select synthetic CI configuration through the original configuration cache.
    # No fake config.logger/util modules, no production file or secret lookup.
    from core.utils.cache.manager import cache_manager, CacheType
    from config import settings
    config = configuration()
    cache_manager.set(CacheType.CONFIG, 'main_config', config)
    settings.config_file_valid = True  # No production/local config file in this fixture.
    from config.logger import setup_logging
    logger = setup_logging(config)
    logger.remove()  # Do not retain exception-local diagnostics containing CI credentials.
    logger.add(lambda message: None, level='CRITICAL', diagnose=False, backtrace=False)
    return config, logger


async def launch(config, root, report, lock, save):
    from youmi_companion import youmi_adapter as adapter
    app = importlib.import_module('app')
    from core.utils.gc_manager import get_gc_manager
    loop = asyncio.get_running_loop()
    captured = loop.create_future()
    original_start, original_stop = adapter.start_worker, adapter.stop_service

    async def start(server):
        await original_start(server)
        assert server.config is config and server._youmi_worker is not None
        captured.set_result(server)

    async def stop(server, gc_manager, tasks, timeout=20):
        started = time.monotonic()
        drained = await original_stop(server, gc_manager, tasks, timeout=timeout)
        with lock:
            report.setdefault('app_shutdowns', []).append({
                'pid': os.getpid(), 'budget_seconds': timeout,
                'cleanup_seconds': time.monotonic() - started, 'drained': drained,
                'worker_closing': server._youmi_worker.closing,
                'worker_task_done': server._youmi_worker.task.done(),
                'gc_stopped': gc_manager._task is None,
                'service_tasks_done': [task.done() for task in tasks if task is not None],
                'native_finished': server._youmi_worker.lane.done.is_set()})
            save(report)
        assert server._youmi_worker.closing and server._youmi_worker.task.done()
        assert gc_manager._task is None and all(task.done() for task in tasks if task is not None)
        return drained

    adapter.start_worker, adapter.stop_service = start, stop
    task = asyncio.create_task(app.main(), name='actual-installed-app-main')
    try:
        done, _ = await asyncio.wait({task, captured}, timeout=30, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task
            raise AssertionError('Actual app exited before Worker startup')
        assert captured in done, 'Actual app Worker startup timed out'
        server = captured.result()
        deadline = time.monotonic() + 15
        ports = [config['server']['port'], config['server']['http_port']]
        while True:
            assert not task.done(), 'Actual app exited before sockets became ready'
            connected = []
            for port in ports:
                try:
                    reader, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', port), 1)
                    writer.close()
                    await writer.wait_closed()
                    connected.append(port)
                except (OSError, asyncio.TimeoutError):
                    pass
            if connected == ports:
                break
            assert time.monotonic() < deadline, 'Actual WS/HTTP sockets did not start'
            await asyncio.sleep(.05)
        gc = get_gc_manager()
        assert gc._task is not None and not gc._task.done()
        service_tasks = {task.get_coro().__qualname__ for task in asyncio.all_tasks() if not task.done()}
        assert {'WebSocketServer.start', 'SimpleHttpServer.start'}.issubset(service_tasks)
        with lock:
            report.setdefault('app_lifecycles', []).append({
                'pid': os.getpid(), 'ports': ports, 'gc_running': True,
                'actual_service_tasks': sorted(name for name in service_tasks if name in {'WebSocketServer.start', 'SimpleHttpServer.start'}),
                'app_sha256': hashlib.sha256(Path(app.__file__).read_bytes()).hexdigest(),
                'server_class': type(server).__module__ + '.' + type(server).__name__,
                'worker_class': type(server._youmi_worker).__module__ + '.' + type(server._youmi_worker).__name__,
                'scope': 'Actual app.main, WebSocketServer, SimpleHttpServer, GC and original Worker; separate synthetic WSS text driver; audio consumers omitted'})
            save(report)
        return server, task
    except BaseException:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise


def observe_hold(worker, control, root, report, lock, save):
    from youmi_companion.extraction import normalize, validate_output, MemoryError
    original = worker.resolve

    async def resolve(scope):
        extractor = await original(scope)

        def extract(sources, context):
            value = extractor(sources, context)
            if control.get('stage', 'raw') != 'raw' or sources != [control.get('source')]:
                return value
            try:
                result = validate_output(value, sources, context)
            except MemoryError:
                # The production Worker must see the original invalid proposal
                # and produce its own validation feedback, not an extractor error.
                return value
            if not any(row['content'] == normalize(sources[0]) and row['kind'] == 'explicit'
                       and not row['sensitive'] for row in result['memories']):
                return value
            with worker.jobs.store.lock:
                rows = worker.jobs.store.db.execute(
                    "SELECT id,source_payload FROM mia_jobs WHERE owner=? AND state='extracting'", (scope.owner,)).fetchall()
            job_ids = [row['id'] for row in rows if json.loads(row['source_payload'])['text'] == normalize(sources[0])]
            assert len(job_ids) == 1, 'Held proposal is not bound to one actual active job'
            evidence = {'source_sha256': hashlib.sha256(sources[0].encode()).hexdigest(),
                        'validated_actual_output': True, 'pid': os.getpid(), 'job_id': job_ids[0], 'stage': 'raw'}
            temporary = root / 'shutdown-held.tmp'
            temporary.write_text(json.dumps(evidence))
            temporary.replace(root / 'shutdown-held.json')
            with lock:
                report.setdefault('shutdown_held_outputs', []).append(evidence)
                save(report)
            deadline = time.monotonic() + 45
            while not (root / 'shutdown-release.json').exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError('Controlled CI native hold expired')
                time.sleep(.02)
            return value

        return extract

    worker.resolve = resolve
    original_encoder = worker.index.encoder

    class HeldEncoder:
        def __getattr__(self, name):
            return getattr(original_encoder, name)

        def encode(self, text, query=False):
            vector = original_encoder.encode(text, query)
            if control.get('stage') != 'validated_vector' or query or text != normalize(control.get('source', '')):
                return vector
            with worker.jobs.store.lock:
                rows = worker.jobs.store.db.execute(
                    "SELECT id,source_payload,validated_result,attempts FROM mia_jobs WHERE owner=? AND state='validated'",
                    (control['owner'],)).fetchall()
            rows = [row for row in rows if json.loads(row['source_payload'])['text'] == text]
            assert len(rows) == 1
            persisted = json.loads(rows[0]['validated_result'])
            assert persisted['_schema'] == 1 and any(row['content'] == text and row['kind'] == 'explicit'
                and not row['sensitive'] for row in persisted['memories'])
            evidence = {'source_sha256': hashlib.sha256(control['source'].encode()).hexdigest(),
                        'validated_actual_output': True, 'pid': os.getpid(), 'job_id': rows[0]['id'],
                        'stage': 'validated_vector', 'actual_vector_sha256': hashlib.sha256(vector.tobytes()).hexdigest(),
                        'persisted_attempts': rows[0]['attempts']}
            temporary = root / 'shutdown-held.tmp'
            temporary.write_text(json.dumps(evidence))
            temporary.replace(root / 'shutdown-held.json')
            with lock:
                report.setdefault('shutdown_held_outputs', []).append(evidence)
                save(report)
            deadline = time.monotonic() + 45
            while not (root / 'shutdown-release.json').exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError('Controlled CI validated-vector hold expired')
                time.sleep(.02)
            return vector

    worker.index.encoder = HeldEncoder()


async def drive_shutdown(sample, endpoint_state, tls, headers, root, ask, start, stop, first_pid):
    from websockets.asyncio.client import connect
    import sqlite3

    def source_calls(report, source):
        return sum(json.loads(row['messages'][-1]['content'])['user_excerpts'] == [source]
                   for row in report.get('extraction_sdk_requests', []))

    async def held(source, release, stage='raw'):
        for name in ('shutdown-held.json', 'shutdown-release.json'):
            (root / name).unlink(missing_ok=True)
        async with connect(endpoint_state(), ssl=tls, additional_headers=headers, open_timeout=10) as socket:
            hello = json.loads(await socket.recv())
            assert hello['type'] == 'hello'
            await socket.send(json.dumps({'type': 'test_chat', 'query': source, 'shutdown_hold': True, 'shutdown_stage': stage}))
            answer = None
            deadline = time.monotonic() + 65
            while answer is None or not (root / 'shutdown-held.json').exists():
                assert time.monotonic() < deadline, 'No genuine held extraction'
                if answer is None:
                    value = json.loads(await asyncio.wait_for(socket.recv(), 65))
                    assert value['type'] != 'test_error'
                    if value['type'] == 'test_reply':
                        answer = value
                else:
                    await asyncio.sleep(.05)
            evidence = json.loads((root / 'shutdown-held.json').read_text())
            assert evidence['source_sha256'] == hashlib.sha256(source.encode()).hexdigest()
            assert evidence['stage'] == stage
            job_id = answer['admission_status']['id']
            assert evidence['job_id'] == job_id and evidence['pid'] == hello['pid']

            async def release_later():
                await asyncio.sleep(1)
                (root / 'shutdown-release.json').write_text('{}')

            releasing = asyncio.create_task(release_later()) if release else None
            started = time.monotonic()
            try:
                await stop()
            finally:
                if releasing:
                    await releasing
            duration = time.monotonic() - started
            assert duration <= 20, 'Actual entire process exceeded twenty-second shutdown'
        with sqlite3.connect('file:' + (root / 'state.sqlite3').as_posix() + '?mode=ro', uri=True) as db:
            state, attempts, validated = db.execute('SELECT state,attempts,validated_result FROM mia_jobs WHERE id=?', (job_id,)).fetchone()
        assert 1 <= attempts <= 2
        if release:
            assert state == 'committed'
        else:
            assert state in {'extracting', 'pending', 'retry_wait', 'validated'}
            if stage == 'validated_vector':
                assert validated is not None, 'Genuine persisted proposal lost during shutdown'
        return {'job_id': job_id, 'pid': evidence['pid'], 'release_during_shutdown': release,
                'shutdown_seconds': duration, 'state_after_exit': state, 'attempts_after_exit': attempts,
                'has_persisted_result': validated is not None, 'stage': stage, 'answer': answer['tts_text']}

    drained = await held(sample['drain_source'], True)
    second_pid = await start()
    assert second_pid != first_pid
    recalled, saved = await ask(sample['drain_query'])
    assert sample['drain_term'] in recalled['tts_text'] and len(saved['memories']) == 1
    blocked = await held(sample['blocked_source'], False)
    third_pid = await start()
    assert len({first_pid, second_pid, third_pid}) == 3
    deadline = time.monotonic() + 55
    while True:
        with sqlite3.connect('file:' + (root / 'state.sqlite3').as_posix() + '?mode=ro', uri=True) as db:
            state = db.execute('SELECT state FROM mia_jobs WHERE id=?', (blocked['job_id'],)).fetchone()[0]
        if state in {'committed', 'failed'}:
            break
        assert state != 'cancelled' and time.monotonic() < deadline, 'Original raw-result task did not reach a terminal state'
        await asyncio.sleep(.1)
    raw_state = state
    recalled, saved = await ask(sample['blocked_query'])
    if raw_state == 'committed':
        assert sample['blocked_term'] in recalled['tts_text'] and len(saved['memories']) == 2
    else:
        assert len(saved['memories']) == 1 and all(row['content'] != sample['blocked_source'] for row in saved['memories'])
    with sqlite3.connect('file:' + (root / 'state.sqlite3').as_posix() + '?mode=ro', uri=True) as db:
        raw_attempts = db.execute('SELECT attempts FROM mia_jobs WHERE id=?', (blocked['job_id'],)).fetchone()[0]
    assert blocked['attempts_after_exit'] <= raw_attempts <= 2
    raw_calls = source_calls(json.loads((root / 'results.json').read_text()), sample['blocked_source'])
    assert raw_calls == raw_attempts and raw_calls <= 2, 'Raw restart performed an uncounted or third model call'
    durable = await held(sample['durable_source'], False, 'validated_vector')
    before = json.loads((root / 'results.json').read_text())
    calls_before = source_calls(before, sample['durable_source'])
    assert calls_before == durable['attempts_after_exit']
    fourth_pid = await start()
    assert len({first_pid, second_pid, third_pid, fourth_pid}) == 4
    deadline = time.monotonic() + 55
    while True:
        with sqlite3.connect('file:' + (root / 'state.sqlite3').as_posix() + '?mode=ro', uri=True) as db:
            state, attempts = db.execute('SELECT state,attempts FROM mia_jobs WHERE id=?', (durable['job_id'],)).fetchone()
        if state == 'committed':
            break
        assert state not in {'failed', 'cancelled'} and time.monotonic() < deadline, 'Original persisted-result task did not commit'
        await asyncio.sleep(.1)
    assert attempts == durable['attempts_after_exit'] and source_calls(
        json.loads((root / 'results.json').read_text()), sample['durable_source']) == calls_before
    recalled, saved = await ask(sample['durable_query'])
    assert sample['durable_term'] in recalled['tts_text']
    expected = 3 if raw_state == 'committed' else 2
    with sqlite3.connect('file:' + (root / 'state.sqlite3').as_posix() + '?mode=ro', uri=True) as db:
        assert db.execute('SELECT COUNT(*) FROM memories').fetchone()[0] == expected
        assert db.execute('SELECT COUNT(*) FROM mia_vectors').fetchone()[0] == expected
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    return {'drained': drained, 'blocked': blocked, 'raw_recovered_job_state': raw_state,
            'raw_recovered_attempts': raw_attempts, 'durable': durable, 'recovered_job_state': state,
            'raw_model_calls': raw_calls, 'durable_model_calls': calls_before,
            'recovered_attempts': attempts, 'durable_recovery_repeated_model_calls': False,
            'server_pids': [first_pid, second_pid, third_pid, fourth_pid],
            'scope': 'Actual installed app/service lifecycle with real manager, model, BGE and SQLite; controlled native hold; no audio consumers or P23 resource claim'}
