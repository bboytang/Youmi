"""P23 numerical capacity gate; not manager/provider/full-service acceptance."""

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import tempfile
import time
import traceback

import numpy as np
from youmi_performance_budgets import LIMITS, OPTIMIZATION_TARGETS, REVISION as ACCEPTANCE_REVISION, within_budgets

from youmi_companion.authorization import AuthenticatedScope, PrivateAuthorizationRequired
from youmi_companion.embedding import SemanticEncoder, MODEL_KEY, REVISION, DIMENSION
from youmi_companion.memory import MemoryStore
from youmi_companion.semantic import SemanticMemoryIndex


class FixtureAuthorization:
    async def require(self, scope, permission):
        if permission not in scope.permissions:
            raise PrivateAuthorizationRequired()


def summary(samples):
    return {'count': len(samples), 'p50_ms': float(np.percentile(samples, 50)),
            'p95_ms': float(np.percentile(samples, 95)), 'samples_ms': samples}


def cgroup():
    root = Path('/sys/fs/cgroup')
    result = {name: (root / name).read_text().strip() for name in
              ('cpu.max', 'memory.max', 'memory.peak', 'memory.events', 'cpu.stat')}
    quota, period = result['cpu.max'].split()
    assert quota != 'max' and 0 < int(quota) / int(period) <= 2
    assert result['memory.max'] != 'max' and int(result['memory.max']) <= 2 * 1024 ** 3
    return result


def save(path, report):
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


def scope(owner='user:941030001:agent:p23'):
    return AuthenticatedScope(owner, 941030001, 1, 'p23', 'mia', 'synthetic-p23-device',
        'synthetic-p23-client', 0, 1, datetime.now(timezone.utc) + timedelta(days=1),
        frozenset({'memory:read', 'memory:write', 'memory:manage'}))


def populate(store, current, size, profile, fixture, vectors):
    store.snapshot(current.owner)
    with store.lock, store.db:
        for number in range(size):
            seed = fixture['records'][number % len(fixture['records'])]
            memory_id = f'{current.owner}-{number:05}'
            # Production deduplicates identical facts. The sequence label makes
            # synthetic capacity rows distinct; vectors remain real seed copies.
            content = seed['short' if profile == 'short' else 'dense'] + f'（容量编号{number:05}）'
            store.db.execute('INSERT INTO memories(id,owner,category,content,confirmed,created_at,updated_at) '
                'VALUES (?,?,?,?,1,1,1)', (memory_id, current.owner, 'fact', content))
            store.db.execute('INSERT INTO sources(memory_id,session_id,fingerprint,excerpt,created_at) '
                'VALUES (?,?,?,?,1)', (memory_id, 'synthetic-capacity', memory_id, content))
            store.db.execute('UPDATE mia_memory_meta SET entities=?,keywords=?,trigger_terms=?,emotional_weight=? '
                'WHERE memory_id=?', (*[json.dumps(seed[name], ensure_ascii=False) for name in
                ('entities', 'keywords', 'trigger_terms')], seed['importance'], memory_id))
            vector = vectors[(number % len(fixture['records']), profile)]
            store.db.execute('INSERT INTO mia_vectors(memory_id,model_id,model_revision,dimension,content_version,'
                'normalized_vector,indexed_at) VALUES (?,?,?,?,1,?,1)',
                (memory_id, MODEL_KEY, REVISION, DIMENSION, vector.tobytes()))


async def measure(index, current, auth, queries):
    sql, pipeline, cases = [], [], []
    for query in queries:
        sql_case, pipeline_case = [], []
        vector = index.encoder.encode(query, query=True)
        state = index._snapshot(current)
        index._scan(current, state, vector, 12, None)
        await index.retrieve(current, auth, query)
        for _ in range(5):
            started = time.perf_counter()
            index._scan(current, state, vector, 12, None)
            sql_case.append((time.perf_counter() - started) * 1000)
            started = time.perf_counter()
            result = await index.retrieve(current, auth, query)
            pipeline_case.append((time.perf_counter() - started) * 1000)
            assert all(row['id'].startswith(current.owner + '-') for row in result['candidates'])
        sql.extend(sql_case)
        pipeline.extend(pipeline_case)
        cases.append({'query': query, 'sqlite_exact': summary(sql_case), 'encode_hybrid': summary(pipeline_case)})
    return {'sqlite_exact': summary(sql), 'encode_hybrid': summary(pipeline), 'queries': cases}


async def competing(index, current, background, auth, queries):
    # Reindex a different Scope so content-version invalidation is not hidden.
    # Both scans and rebuild use the actual shared encoder, store and locks.
    store = index.store
    with store.lock, store.db:
        store.db.execute('DELETE FROM mia_vectors WHERE memory_id IN '
            '(SELECT id FROM memories WHERE owner=?)', (background.owner,))
    started = time.perf_counter()
    rebuilding = asyncio.create_task(index.rebuild_batch(background, auth, limit=32))

    async def foreground():
        times = []
        for query in queries:
            for _ in range(5):
                begin = time.perf_counter()
                await index.retrieve(current, auth, query)
                times.append((time.perf_counter() - begin) * 1000)
        return summary(times)

    values = await asyncio.gather(foreground(), foreground(), rebuilding, return_exceptions=True)
    return {'elapsed_ms': (time.perf_counter() - started) * 1000,
        'foreground': [({'error_class': type(value).__name__, 'reason': str(value)}
            if isinstance(value, Exception) else value) for value in values[:2]],
        'rebuild': ({'error_class': type(values[2]).__name__, 'reason': str(values[2])}
            if isinstance(values[2], Exception) else values[2]),
        'scope': 'Module concurrency only; no Provider, manager or Worker fairness claim'}


async def evaluate(args, report):
    report['cgroup_before'] = cgroup()
    started = time.perf_counter()
    encoder = SemanticEncoder(args.model_dir)
    report['encoder_cold_load_ms'] = (time.perf_counter() - started) * 1000
    options = encoder._session.get_session_options()
    report['encoder_session_options'] = {'intra_op_num_threads': options.intra_op_num_threads,
        'inter_op_num_threads': options.inter_op_num_threads, 'providers': encoder._session.get_providers()}
    fixture = json.loads(args.dataset.read_text())
    report['dataset_sha256'] = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    report['embedding'] = {'model_id': MODEL_KEY, 'revision': REVISION, 'dimension': DIMENSION,
        'tokenizer_sha256': hashlib.sha256((args.model_dir / 'tokenizer.json').read_bytes()).hexdigest()}
    vectors = {(i, profile): encoder.encode(seed[profile]).astype('<f4')
        for i, seed in enumerate(fixture['records']) for profile in ('short', 'dense')}
    report['seed_vectors_sha256'] = hashlib.sha256(b''.join(v.tobytes() for v in vectors.values())).hexdigest()
    report['capacity'] = []
    auth, current = FixtureAuthorization(), scope()
    cases = ([(10000, 'dense')] if args.mode == 'diagnostic' else
             [(1000, 'short'), (1000, 'dense'), (10000, 'short'), (10000, 'dense'), (50000, 'dense')])
    for size, profile in cases:
        row = {'size_per_scope': size, 'profile': profile, 'required': size != 50000}
        report['capacity'].append(row)
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'capacity.sqlite3'
            store = MemoryStore(database)
            try:
                populate(store, current, size, profile, fixture, vectors)
                # An equally large foreign Scope makes SQL Scope filtering measurable.
                foreign = replace(current, owner='user:941030002:agent:p23')
                populate(store, foreign, size, profile, fixture, vectors)
                background = replace(current, owner='user:941030003:agent:p23')
                populate(store, background, 32, profile, fixture, vectors)
                index = SemanticMemoryIndex(store, encoder)
                if args.mode == 'diagnostic':
                    from youmi_performance_diagnostics import diagnose
                    row['diagnostics'] = await diagnose(index, current, auth, fixture['queries'], summary,
                        lambda: competing(index, current, background, auth, fixture['queries']))
                    row['diagnostic_complete'] = True
                    continue
                cpu = time.process_time()
                wall = time.perf_counter()
                row.update(await measure(index, current, auth, fixture['queries']))
                row['competition'] = await competing(index, current, background, auth, fixture['queries'])
                row['cpu_seconds'] = time.process_time() - cpu
                row['wall_seconds'] = time.perf_counter() - wall
                row['database_bytes'] = sum(p.stat().st_size for p in Path(folder).glob('capacity.sqlite3*'))
                row['rss_peak_mib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
                if hasattr(index, '_features'):
                    row['static_features'] = {'entries': len(index._features.entries),
                                              'accounted_bytes': index._features.bytes}
                row['integrity'] = store.db.execute('PRAGMA integrity_check').fetchone()[0]
                row['within_numeric_budgets'] = within_budgets(row)
                row['optimization_targets_met'] = within_budgets(row, limits=OPTIMIZATION_TARGETS)
            except Exception as error:
                row.update(within_numeric_budgets=False, error_class=type(error).__name__, reason=str(error))
            finally:
                store.close()
                save(args.output, report)
    report['cgroup_after'] = cgroup()
    if args.mode == 'diagnostic':
        report['diagnostic_complete'] = all(row.get('diagnostic_complete', False) for row in report['capacity'])
        return
    report['numeric_gate_passed'] = all(row['within_numeric_budgets'] for row in report['capacity'] if row['required'])
    report['optimization_targets_met'] = all(row.get('optimization_targets_met', False)
        for row in report['capacity'] if row['required'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=('capacity', 'diagnostic'), default='capacity')
    args = parser.parse_args()
    report = {'code_sha': os.environ['GITHUB_SHA'], 'mode': args.mode, 'numeric_gate_passed': False,
        'acceptance_revision': ACCEPTANCE_REVISION, 'limits': LIMITS, 'optimization_targets': OPTIMIZATION_TARGETS,
        'p23_complete': False, 'scope': 'P23 numerical module capacity, not full-service acceptance',
        'fixture_kind': 'Synthetic records and explicit synthetic metadata; replicated real BGE seed vectors; IO/CPU only, no semantic-quality claim',
        'remaining': ['shared full-service resource envelope', 'same-Provider paired first-sentence delta',
            'real Worker task latency/fairness/faults', 'inflight full-service shutdown under contention']}
    try:
        asyncio.run(evaluate(args, report))
    except Exception as error:
        report.update(error_class=type(error).__name__, reason=str(error), traceback=traceback.format_exc())
    finally:
        save(args.output, report)
    print(json.dumps({'numeric_gate_passed': report['numeric_gate_passed'], 'p23_complete': False}))
    if not report.get('diagnostic_complete' if args.mode == 'diagnostic' else 'numeric_gate_passed', False):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
