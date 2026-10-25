"""Actual ORT thread/query scheduling pairs in fresh processes, never acceptance."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


def encoder_for_arm(directory, threads, encoder_class, report):
    import onnxruntime as ort

    original = ort.InferenceSession

    def session(path, options, **kwargs):
        assert options.intra_op_num_threads == 1
        options.intra_op_num_threads = threads
        value = original(path, options, **kwargs)
        actual = value.get_session_options()
        assert actual.intra_op_num_threads == threads and actual.inter_op_num_threads == 1
        report['trial_session_options'] = {'intra_op_num_threads': actual.intra_op_num_threads,
            'inter_op_num_threads': actual.inter_op_num_threads,
            'providers': value.get_providers()}
        return value

    # Scope the experiment to construction of this real session. The runtime
    # encoder, tokenizer, model, normalization and invocation remain original.
    with patch.object(ort, 'InferenceSession', session):
        return encoder_class(directory)


def check_vectors(encoder, fixture, vectors):
    values = {f'document-{i}-{profile}': vector for (i, profile), vector in vectors.items()}
    values.update({f'query-{i}': encoder.encode(query, query=True)
        for i, query in enumerate(fixture['queries'])})
    return {key: {'sha256': hashlib.sha256(value.tobytes()).hexdigest(),
        'float32_le_hex': value.astype('<f4').tobytes().hex()} for key, value in values.items()}


async def run_pairs(report, save, group_state, members):
    import numpy as np

    root = Path(os.environ['MIA_P23_REPORT_DIR'])
    scheduling = report['mode'] == 'scheduling'
    if scheduling:
        import inspect
        from youmi_companion.semantic import SemanticMemoryIndex
        report['scheduling_runtime_retrieve_sha256'] = hashlib.sha256(
            inspect.getsource(SemanticMemoryIndex.retrieve).encode()).hexdigest()
        # This ABBA compares the frozen unconditional-pair implementation. A
        # later adaptive/serial implementation must not be mislabeled "paired".
        assert report['scheduling_runtime_retrieve_sha256'] == '4e14b2dc275a7355975a3b540b550f0c378855fd3c97274013b4c8993fbd55e7', 'Scheduling experiment is frozen at observer 48aad92; use that ref'
    order_key, arms_key = ('scheduling_order', 'scheduling_arms') if scheduling else ('thread_order', 'thread_arms')
    report.update(cgroup_before=group_state(), members_before=members(),
        trial_scope='Fresh Python processes in ABBA order; actual manager/databases stay in same parent; not acceptance')
    report[order_key] = ['paired', 'serial', 'serial', 'paired'] if scheduling else [2, 1, 1, 2]
    report[arms_key] = []
    for number, setting in enumerate(report[order_key]):
        directory = root / f'{"scheduling" if scheduling else "thread"}-arm-{number}-{setting}'
        directory.mkdir()
        arm = {'number': number, 'directory': directory.name,
            ('query_scheduling' if scheduling else 'intra_op_num_threads'): setting}
        report[arms_key].append(arm)
        save(report)
        environment = dict(os.environ, MIA_P23_REPORT_DIR=str(directory),
            MIA_P23_MODE='scheduling-arm' if scheduling else 'thread-arm')
        environment['MIA_P23_QUERY_SCHEDULING' if scheduling else 'MIA_P23_THREADS'] = str(setting)
        with (directory / 'python.log').open('wb') as output:
            process = await asyncio.create_subprocess_exec(sys.executable,
                str(Path(__file__).with_name('verify_youmi_shared_envelope.py')),
                env=environment, stdout=output, stderr=asyncio.subprocess.STDOUT)
            arm['pid'] = process.pid
            save(report)
            try:
                arm['returncode'] = await asyncio.wait_for(process.wait(), 180)
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        assert arm['returncode'] == 0, 'Diagnostic arm failed; preserve its original report/log'
        result = json.loads((directory / 'shared-results.json').read_text())
        assert result['code_sha'] == report['code_sha'] and result['diagnostic_complete']
        if scheduling:
            assert result['query_scheduling'] == setting
            assert result['encoder_session_options']['intra_op_num_threads'] == 1
        else:
            assert result['trial_session_options']['intra_op_num_threads'] == setting
        assert result['members_before']['python']['pid'] == arm['pid']
        row = result['capacity'][0]
        assert row['integrity'] == 'ok' and row['diagnostics']['competition']['rebuild']['indexed'] == 32
        assert row['static_features']['entries'] == 20000
        assert all(value['count'] == len(value['samples_ms']) == 15
            for value in row['diagnostics']['competition']['foreground'])
        arm.update(diagnostic_complete=True, rss_peak_mib=row['rss_peak_mib'],
            python_cpu_seconds=row['python_cpu_seconds'], wall_seconds=row['wall_seconds'],
            sequential_pipeline=row['diagnostics']['sequential_pipeline'],
            competition=row['diagnostics']['competition'], cgroup_after=row['cgroup_after'])
        save(report)

    results = [json.loads((root / arm['directory'] / 'shared-results.json').read_text())
        for arm in report[arms_key]]
    baseline = results[0]
    comparisons = []
    for number, result in enumerate(results):
        vectors = result['encoder_check_vectors']
        assert vectors.keys() == baseline['encoder_check_vectors'].keys()
        maximum = max(float(np.max(np.abs(
            np.frombuffer(bytes.fromhex(value['float32_le_hex']), dtype='<f4') -
            np.frombuffer(bytes.fromhex(baseline['encoder_check_vectors'][key]['float32_le_hex']), dtype='<f4'))))
            for key, value in vectors.items())
        original = baseline['capacity'][0]['result_checks']
        current = result['capacity'][0]['result_checks']
        selected = lambda checks: [[row['id'] for row in check['retrieval']['results']] for check in checks]
        candidates = lambda checks: [[(row['id'], row['candidate_sources'])
            for row in check['retrieval']['candidates']] for check in checks]
        comparisons.append({'arm_number': number,
            'vectors_bitwise_equal': vectors == baseline['encoder_check_vectors'],
            'vector_max_abs_difference': maximum,
            'retrievals_exact_equal': current == original,
            'selected_ids_and_order_equal': selected(current) == selected(original),
            'candidate_ids_order_sources_equal': candidates(current) == candidates(original)})
    report['scheduling_consistency' if scheduling else 'thread_consistency'] = comparisons
    report['all_checks_exact_equal'] = all(value['vectors_bitwise_equal']
        and value['retrievals_exact_equal'] for value in comparisons)
    events = dict(line.split() for line in group_state()['memory.events'].splitlines())
    assert events['oom'] == events['oom_kill'] == '0'
    if scheduling:
        assert report['all_checks_exact_equal'], 'Scheduling changed actual vectors or complete retrieval results'
    report['diagnostic_complete'] = True
