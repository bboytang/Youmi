"""Hold one genuine, validated extraction in CI; never fabricate its output."""

import asyncio
import hashlib
import json
import time


def observe_worker(worker, control, root, report, lock, save):
    from youmi_companion.extraction import normalize, validate_output

    ready, release, finished = [root / ('inflight-' + name + '.json')
        for name in ('ready', 'release', 'finished')]
    for path in (ready, release, finished):
        path.unlink(missing_ok=True)
    resolve, run_once = worker.resolve, worker.run_once

    async def observed_resolve(scope):
        extractor = await resolve(scope)

        def extract(sources, context):
            active = control.get('owner') == scope.owner and sources == [control.get('source')]
            before = len(report['extraction_outputs'])
            value = extractor(sources, context)
            if not active:
                return value
            result = validate_output(value, sources, context)
            expected = normalize(control['source'].removeprefix('请记住'))
            assert any(row['content'] == expected and row['kind'] == 'explicit' and not row['sensitive']
                for row in result['memories']), 'No genuine potentially writable fact to hold'
            assert len(report['extraction_outputs']) == before + 1, 'Held result was not a real model output'
            with worker.jobs.store.lock:
                rows = worker.jobs.store.db.execute("SELECT id,source_payload FROM mia_jobs WHERE owner=? AND state='extracting'",
                    (scope.owner,)).fetchall()
            ids = [row['id'] for row in rows if json.loads(row['source_payload'])['text'] == normalize(sources[0])]
            assert len(ids) == 1
            control['job_id'] = ids[0]
            evidence = {'job_id': ids[0], 'source_sha256': hashlib.sha256(sources[0].encode()).hexdigest(),
                'real_model_outputs': 1, 'validated_fact': True}
            temporary = ready.with_suffix('.tmp')
            temporary.write_text(json.dumps(evidence))
            temporary.replace(ready)
            with lock:
                report['inflight_extraction'] = dict(evidence, phase='real_validated_output_held')
                save(report)
            deadline = time.monotonic() + 20
            while not release.exists():
                assert time.monotonic() < deadline, 'CI extraction hold was not released'
                time.sleep(.02)
            return value

        return extract

    async def observed_once():
        completed = False
        try:
            value = await run_once()
            completed = True
            return value
        finally:
            if ready.exists() and release.exists() and not control.get('finished'):
                current = worker.jobs.current({'id': control['job_id']})
                evidence = {'job_id': current['id'], 'state': current['state'],
                    'original_worker_returned': completed, 'native_finished': worker.lane.done.is_set()}
                control['finished'] = True
                temporary = finished.with_suffix('.tmp')
                temporary.write_text(json.dumps(evidence))
                temporary.replace(finished)
                with lock:
                    report['inflight_extraction'].update(evidence, phase='original_worker_processed_late_output')
                    save(report)

    worker.resolve, worker.run_once = observed_resolve, observed_once


async def delete_while_extraction_held(endpoint, tls, headers, root, source, target, manage):
    from websockets.asyncio.client import connect

    async def read_file(path, timeout):
        deadline = time.monotonic() + timeout
        while not path.exists():
            assert time.monotonic() < deadline, 'CI late-result observation timed out'
            await asyncio.sleep(.05)
        return json.loads(path.read_text())

    release = root / 'inflight-release.json'
    async with connect(endpoint, ssl=tls, additional_headers=headers, open_timeout=10) as socket:
        assert json.loads(await socket.recv())['type'] == 'hello'
        await socket.send(json.dumps({'type': 'test_chat', 'query': source, 'hold_extraction': True}))
        answer = None
        try:
            while answer is None:
                value = json.loads(await asyncio.wait_for(socket.recv(), timeout=65))
                assert value['type'] != 'test_error'
                if value['type'] == 'test_reply':
                    answer = value
            ready = await read_file(root / 'inflight-ready.json', 45)
            assert ready['job_id'] == answer['admission_status']['id']
            assert ready['source_sha256'] == hashlib.sha256(source.encode()).hexdigest()
            assert ready['validated_fact'] and ready['real_model_outputs'] == 1
            await manage('delete', id=target)
            assert not (await manage('list'))['memories']
            release.write_text('{}')
            while True:
                value = json.loads(await asyncio.wait_for(socket.recv(), timeout=20))
                assert value['type'] != 'test_error'
                if value['type'] == 'test_cancelled':
                    assert value['job_id'] == ready['job_id'] and value['state'] == 'cancelled'
                    break
            finished = await read_file(root / 'inflight-finished.json', 20)
            assert finished['job_id'] == ready['job_id'] and finished['state'] == 'cancelled'
            assert finished['original_worker_returned'] and finished['native_finished']
            assert not (await manage('list'))['memories']
            return {'ready': ready, 'finished': finished, 'session_id': answer['session_id'],
                'response': answer['tts_text'], 'deleted_before_late_output_returned': True}
        finally:
            release.write_text('{}')
