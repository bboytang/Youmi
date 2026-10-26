"""Installed 10..10000-row capacity and cache lifecycle; no chat model/data."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import resource
import tempfile
import time

import numpy as np

from youmi_companion.embedding import DIMENSION, MODEL_KEY, REVISION, EmbeddingUnavailable, SemanticEncoder
from youmi_companion.memory import MemoryStore
from youmi_companion.memory_repository import YoumiMemoryRepository
from youmi_companion.memory_runtime import YoumiMemoryRuntime
from youmi_companion.semantic import SemanticMemoryIndex
from verify_youmi_retrieval import FixturePolicy, scope


async def evaluate(directory, report):
    encoder, policy, current = SemanticEncoder(directory), FixturePolicy(), scope('J/capacity/mia')
    text = '我喜欢在阳台种薄荷'
    seed = encoder.encode(text).astype('<f4').tobytes()
    report.update(sizes={}, gates={})
    for size in (10, 100, 1000, 10000):
        with tempfile.TemporaryDirectory() as folder:
            store = MemoryStore(Path(folder) / 'capacity.sqlite3')
            try:
                store.snapshot(current.owner)
                with store.db:
                    store.db.executemany('INSERT INTO memories(id,owner,category,content,confirmed,created_at,updated_at) '
                        'VALUES (?,?,?,?,1,1,1)', [(f'capacity-{i:05}', current.owner, 'fact', text + str(i)) for i in range(size)])
                    store.db.executemany('INSERT INTO sources VALUES (?,?,?,?,1)',
                        [(f'capacity-{i:05}', 'synthetic-capacity', str(i), text + str(i)) for i in range(size)])
                    store.db.executemany('INSERT INTO mia_vectors(memory_id,model_id,model_revision,dimension,content_version,normalized_vector,indexed_at) '
                        'VALUES (?,?,?,?,1,?,1)', [(f'capacity-{i:05}', MODEL_KEY, REVISION, DIMENSION, seed) for i in range(size)])
                index = SemanticMemoryIndex(store, encoder)
                runtime = YoumiMemoryRuntime(index, managed_shadow_cache=True)
                measurements = report['sizes'][str(size)] = {}
                async def measure(name, retrieve, count=1):
                    elapsed = []
                    for _ in range(count):
                        started = time.perf_counter()
                        result = await retrieve(current, policy, text)
                        elapsed.append((time.perf_counter() - started) * 1000)
                        assert len(result['results']) <= 4
                        for row in result['results']:
                            assert row['id'].startswith('capacity-')
                            if 'source' in row:
                                if row['source']:
                                    assert row['source']['excerpt'] == row['content']
                                else:
                                    assert row['content_version'] > 1  # An explicit admin edit clears old sources.
                    measurements[name] = dict(count=count, p50_ms=float(np.percentile(elapsed, 50)),
                        p95_ms=float(np.percentile(elapsed, 95)))
                    return result
                await index.retrieve(current, policy, text)
                await measure('legacy_warm', index.retrieve, 10)
                await measure('ram_cold', YoumiMemoryRuntime(index).shadow_retrieve)
                await measure('managed_cold', runtime.shadow_retrieve)
                reopened = YoumiMemoryRuntime(index, managed_shadow_cache=True)
                await measure('managed_reopen', reopened.shadow_retrieve)
                result = await measure('managed_warm', reopened.shadow_retrieve, 10)
                target = result['results'][0]['id']
                store.edit_memory(current.owner, target, '我喜欢在阳台种薄荷，也种罗勒')
                assert store.cache_cleanup_status(current.owner)['state'] == 'complete'
                await index.index_memory(current, policy, target)
                await measure('managed_edit_rebuild', reopened.shadow_retrieve)
                started = time.perf_counter()
                store.delete_memory(current.owner, target)
                assert store.cache_cleanup_status(current.owner)['state'] == 'complete'
                path = reopened._shadow.managed_cache.directory(current.owner)
                assert sorted(p.name for p in path.iterdir()) == ['writer.lock']
                measurements['delete_purge_ms'] = (time.perf_counter() - started) * 1000
                after = await measure('managed_delete_rebuild', reopened.shadow_retrieve)
                assert target not in [row['id'] for row in after['results']]
                report['gates'][str(size)] = (measurements['legacy_warm']['p95_ms'] <= 400
                    and measurements['managed_warm']['p95_ms'] <= 400)
                if size == 10000:
                    # Restore deleted row and add one: the count bound is checked
                    # before a search can consume additional native resources.
                    with store.db:
                        for identifier in ('boundary-first', 'boundary-extra'):
                            store.db.execute('INSERT INTO memories VALUES (?,?,?,?,1,1,1)',
                                (identifier, current.owner, 'fact', text + identifier))
                    repository = YoumiMemoryRepository(store)
                    try:
                        await repository.capture(current, policy)
                    except EmbeddingUnavailable as error:
                        assert str(error) == 'memory_shadow_budget_exceeded'
                    else:
                        raise AssertionError('10001 records must exceed the capture bound')
                    with store.db:
                        store.db.execute("DELETE FROM memories WHERE id='boundary-extra'")
                        store.db.execute('UPDATE memories SET content=? || id', ('界' * 300,))
                    try:
                        await repository.capture(current, policy)
                    except EmbeddingUnavailable as error:
                        assert str(error) == 'memory_shadow_budget_exceeded'
                    else:
                        raise AssertionError('More than 8 MiB must exceed the capture bound')
                    report['gates']['count_and_bytes_bounded'] = True
            finally:
                store.close()
    report['passed'] = all(report['gates'].values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = dict(commit=os.environ.get('GITHUB_SHA'), passed=False,
        scope='synthetic capacity with repeated real seed vectors, not semantic quality or production acceptance',
        resource_limit=dict(cpus=2, memory_mib=2048, network='none'))
    try:
        asyncio.run(evaluate(args.model_dir, report))
    finally:
        report['peak_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['passed']:
        raise SystemExit('Memory capacity gate failed; inspect preserved timings')


if __name__ == '__main__':
    main()
