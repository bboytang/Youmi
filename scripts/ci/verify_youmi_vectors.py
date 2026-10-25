"""P12 actual BGE/SQLite mechanisms on 2 CPUs/2 GiB; not full hybrid RAG."""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import tempfile
import threading
import time

import numpy as np

from youmi_companion.authorization import AuthenticatedScope, PrivateAuthorizationRequired
from youmi_companion.embedding import SemanticEncoder, MODEL_KEY, REVISION, DIMENSION, EmbeddingUnavailable
from youmi_companion.memory import MemoryStore
from youmi_companion.semantic import SemanticMemoryIndex


class SyntheticAuthorization:
    """Policy fixture only; actual manager proof is in the separate identity CI."""
    active = True
    async def require(self, scope, permission):
        if not self.active or permission not in scope.permissions:
            raise PrivateAuthorizationRequired()


def scope():
    return AuthenticatedScope('user:1:agent:vector-ci', 1, 1, 'vector-ci', 'mia', 'synthetic-device',
        'synthetic-client', 0, 1, datetime.now(timezone.utc) + timedelta(days=1),
        frozenset({'memory:read','memory:write','memory:manage'}))


def remember(store, owner, text, marker, sensitive=False):
    return store.apply_extraction(owner, marker, store.snapshot(owner)['revision'], [text],
        {'memories': [{'category': 'fact', 'content': text, 'source_index': 0, 'sensitive': sensitive}]}, marker)['memories'][0]


def concurrent_encoding(encoder, cases, workers):
    expected = [encoder.encode(text, query) for text, query in cases]
    original = encoder._session
    guard, pair = threading.Lock(), threading.Barrier(2, timeout=3)
    active, peak, calls = 0, 0, 0
    intervals = []

    class ObservedSession:
        def run(self, *args, **kwargs):
            nonlocal active, peak, calls
            with guard:
                ticket = calls
                calls += 1
                active += 1
                peak = max(peak, active)
            try:
                if ticket < 2:
                    pair.wait()
                started = time.perf_counter()
                try:
                    return original.run(*args, **kwargs)
                finally:
                    with guard:
                        intervals.append([started, time.perf_counter()])
            finally:
                with guard:
                    active -= 1

    encoder._session = ObservedSession()
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(encoder.encode, *case) for case in cases * 2]
            actual = []
            for future in futures:
                try:
                    actual.append(future.result())
                except Exception as error:
                    actual.append(error)
    finally:
        encoder._session = original
    checks = []
    for number, value in enumerate(actual):
        if isinstance(value, Exception):
            checks.append({'case': number % len(cases), 'bitwise_equal': False,
                'error_class': type(value).__name__, 'reason': str(value)})
            continue
        reference = expected[number % len(cases)]
        equal = value.tobytes() == reference.tobytes()
        checks.append({'case': number % len(cases), 'bitwise_equal': equal,
            'sha256': hashlib.sha256(value.tobytes()).hexdigest(),
            'reference_sha256': hashlib.sha256(reference.tobytes()).hexdigest(),
            'max_abs_difference': float(np.max(np.abs(value - reference)))})
    overlap = any(max(a[0], b[0]) < min(a[1], b[1])
        for i, a in enumerate(intervals) for b in intervals[i + 1:])
    result = {'cases': len(cases), 'calls': calls, 'workers': workers, 'native_peak_inflight': peak,
        'actual_run_windows_overlap': overlap, 'checks': checks,
        'native_call_windows_seconds': intervals,
        'all_bitwise_equal': all(check['bitwise_equal'] for check in checks),
        'rss_peak_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
    return result


async def evaluate(directory, report):
    encoder = SemanticEncoder(directory)
    options = encoder._session.get_session_options()
    report['encoder_session_options'] = {'intra_op_num_threads': options.intra_op_num_threads,
        'inter_op_num_threads': options.inter_op_num_threads, 'providers': encoder._session.get_providers()}
    metadata = json.loads((directory / 'metadata.json').read_text())
    report['asset_sha256'] = metadata['models']['model-uint8.onnx']['sha256']
    report['tokenizer_sha256'] = metadata['tokenizer_sha256']
    fidelity = [float(encoder.encode(row['text'], row['query']) @ np.asarray(row['vector'], dtype=np.float32))
                for row in metadata['reference_vectors']]
    report['reference_cosine_min'] = min(fidelity)
    assert min(fidelity) >= 0.95
    current, auth = scope(), SyntheticAuthorization()
    texts = ['每次喝全脂牛奶，我的肚子都会不舒服。', '周末我喜欢沿着湖边步行看日落。',
             '我目前住在苏州工业园区。', '我星期三晚上会练习钢琴。']
    queries = ['我喝哪类乳品容易闹肚子？', '休息日我一般去哪里散步？', '我现在在哪座城市生活？', '我通常哪天晚上练琴？']
    with tempfile.TemporaryDirectory() as folder:
        store = MemoryStore(Path(folder) / 'real-vectors.sqlite3')
        try:
            ids = [remember(store, current.owner, text, 'fact-' + str(i)) for i, text in enumerate(texts)]
            foreign_id = remember(store, 'user:2:agent:other', texts[0], 'foreign')
            remember(store, current.owner, '这段个人私密经历尚未确认', 'pending', sensitive=True)
            index = SemanticMemoryIndex(store, encoder)
            assert (await index.rebuild_batch(current, auth)) == {'indexed': 4, 'has_more': False}
            foreign = replace(current, owner='user:2:agent:other', user_id=2, agent_id='other')
            assert (await index.rebuild_batch(foreign, auth)) == {'indexed': 1, 'has_more': False}
            inputs = [(current, query) for query in queries] + [(foreign, queries[0])]
            serial = [await index.candidates(owner, auth, query, limit=4, minimum=0.35)
                for owner, query in inputs]
            concurrent = await asyncio.gather(*(index.candidates(owner, auth, query, limit=4, minimum=0.35)
                for owner, query in inputs))
            report['concurrent_scope_retrieval'] = {'exact_equal': serial == concurrent,
                'serial': serial, 'concurrent': concurrent}
            assert serial == concurrent and serial[-1]['candidates'][0]['id'] == foreign_id
            assert all(row['id'] in ids for value in concurrent[:-1] for row in value['candidates'])
            # Prove that actual recall still runs when the legacy FTS is absent.
            fts_rows = [tuple(row) for row in store.db.execute('SELECT memory_id,terms FROM memory_search')]
            with store.db:
                store.db.execute('DROP TABLE memory_search')
            rows = report['real_chinese_cross_phrase'] = []
            for query, expected in zip(queries, ids):
                found = (await index.candidates(current, auth, query, limit=4, minimum=0.35))['candidates']
                rows.append({'query': query, 'expected': expected, 'ids': [item['id'] for item in found],
                             'cosines': [item['cosine'] for item in found]})
                assert found and found[0]['id'] == expected
                assert set(item['id'] for item in found) <= set(ids)
            unrelated = await index.candidates(current, auth, '冥王星的公转周期是多少年？', limit=4, minimum=0.35)
            report['unrelated_empty'] = not unrelated['candidates']
            assert report['unrelated_empty']
            report['legacy_fts_absent_during_recall'] = True
            with store.db:
                store.db.execute('CREATE VIRTUAL TABLE memory_search USING fts5(memory_id UNINDEXED,terms)')
                store.db.executemany('INSERT INTO memory_search(memory_id,terms) VALUES (?,?)', fts_rows)
            store.edit_memory(current.owner, ids[0], '我可以正常喝全脂牛奶，现在更喜欢无糖豆浆。')
            assert store.db.execute('SELECT COUNT(*) FROM mia_vectors WHERE memory_id=?', (ids[0],)).fetchone()[0] == 0
            try:
                await index.candidates(current, auth, queries[0])
            except EmbeddingUnavailable as error:
                assert str(error) == 'embedding_index_not_ready'
            else:
                raise AssertionError('Missing index was disguised as successful recall')
            await index.index_memory(current, auth, ids[0])
            assert (await index.candidates(current, auth, '我现在偏好哪种豆浆？', minimum=0.35))['candidates'][0]['id'] == ids[0]
            store.delete_memory(current.owner, ids[0])
            assert store.db.execute('SELECT COUNT(*) FROM mia_vectors WHERE memory_id=?', (ids[0],)).fetchone()[0] == 0
            auth.active = False
            try:
                await index.candidates(current, auth, queries[1])
            except PrivateAuthorizationRequired:
                report['revoked_scope_rejected'] = True
            else:
                raise AssertionError('Revoked fixture scope was accepted')
        finally:
            store.close()
    auth.active = True
    report['sqlite_capacity'] = {}
    seed = encoder.encode(texts[0]).astype('<f4').tobytes()
    # Capacity fixtures intentionally replicate one real vector, not 10k
    # semantically encoded facts. No accuracy claim uses this fixture.
    for size in (1000, 10000):
        with tempfile.TemporaryDirectory() as folder:
            store = MemoryStore(Path(folder) / 'capacity.sqlite3')
            try:
                store.snapshot(current.owner)
                with store.db:
                    store.db.executemany('INSERT INTO memories(id,owner,category,content,confirmed,created_at,updated_at) '
                        'VALUES (?,?,?, ?,1,1,1)', [(f'capacity-{i:05}', current.owner, 'fact', f'合成容量记录{i}') for i in range(size)])
                    store.db.executemany('INSERT INTO mia_vectors(memory_id,model_id,model_revision,dimension,content_version,'
                        'normalized_vector,indexed_at) VALUES (?,?,?,?,1,?,1)',
                        [(f'capacity-{i:05}', MODEL_KEY, REVISION, DIMENSION, seed) for i in range(size)])
                index = SemanticMemoryIndex(store, encoder)
                query = encoder.encode(queries[0], query=True)
                state = index._snapshot(current)
                sql_times, pipeline = [], []
                for unused in range(10):
                    started = time.perf_counter()
                    assert len(index._scan(current, state, query, 12, None)) == 12
                    sql_times.append((time.perf_counter() - started) * 1000)
                    started = time.perf_counter()
                    assert len((await index.candidates(current, auth, queries[0]))['candidates']) == 12
                    pipeline.append((time.perf_counter() - started) * 1000)
                report['sqlite_capacity'][str(size)] = {'sqlite_p95_ms': float(np.percentile(sql_times, 95)),
                    'encode_plus_query_p95_ms': float(np.percentile(pipeline, 95)),
                    'rss_peak_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
                    'database_bytes': (Path(folder) / 'capacity.sqlite3').stat().st_size,
                    'fixture': 'Synthetic records, replicated real seed vector; capacity only, not accuracy'}
            finally:
                store.close()
    started = time.perf_counter()
    long_vector = encoder.encode('这是关于长输入容量的独立合成观察。' * 100, query=True)
    report['long_input_diagnostic'] = {'chars': len('这是关于长输入容量的独立合成观察。' * 100),
        'model_max_tokens': 512, 'elapsed_ms': (time.perf_counter() - started) * 1000,
        'normalized': bool(abs(np.linalg.norm(long_vector) - 1) < 1e-4),
        'rss_peak_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
    report['concurrent_encoding'] = {
        'short': concurrent_encoding(encoder, [(text, False) for text in texts]
            + [(query, True) for query in queries] + [('薄荷🙂𠮷、ASCII、尾\x00', True)], 4),
        'long': concurrent_encoding(encoder, [('这是关于长输入容量的独立合成观察。' * 100, True),
            ('湖边散步与阳台植物是不同的合成观察。' * 100, False)], 2)}
    assert all(value['native_peak_inflight'] == 2 and value['actual_run_windows_overlap']
        and value['calls'] == value['cases'] * 2 and value['all_bitwise_equal']
        and value['rss_peak_mib'] <= 512 for value in report['concurrent_encoding'].values())
    report['passed'] = (report['sqlite_capacity']['10000']['sqlite_p95_ms'] <= 150
                        and report['sqlite_capacity']['10000']['encode_plus_query_p95_ms'] <= 400
                        and report['long_input_diagnostic']['rss_peak_mib'] <= 512)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = {'code_sha': os.environ.get('GITHUB_SHA'), 'kind': 'P12_real_semantic_vectors_not_full_RAG', 'passed': False,
              'resource_limit': {'cpus': 2, 'memory_mib': 2048, 'network': 'none'},
              'manager_authorization': 'fixture_only_actual_manager_test_separate',
              'association_four_scores': 'not_implemented_P13', 'full_behavior': 'not_accepted'}
    try:
        asyncio.run(evaluate(args.model_dir, report))
    except Exception as error:
        report['error_class'] = type(error).__name__
        if isinstance(error, EmbeddingUnavailable):
            report['error_reason'] = str(error)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'passed': report['passed'], 'kind': report['kind']}))
    if not report['passed']:
        raise SystemExit('P12 real vector verification failed; inspect report')


if __name__ == '__main__':
    main()
