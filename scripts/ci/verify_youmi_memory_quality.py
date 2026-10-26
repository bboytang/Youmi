"""Frozen old/new installed retrieval comparison; synthetic canonical data only."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import resource
import tempfile
import time

import numpy as np

from youmi_companion.embedding import SemanticEncoder
from youmi_companion.memory import MemoryStore
from youmi_companion.memory_runtime import YoumiMemoryRuntime
from youmi_companion.semantic import SemanticMemoryIndex
from verify_youmi_retrieval import FixturePolicy, metrics, meets_quality, scope


async def evaluate(directory, report):
    fixtures = Path('/experiment/server/tests/fixtures')
    paths = [fixtures / 'persona_memory_retrieval_zh_v5.json', fixtures / 'persona_memory_retrieval_p13_holdout.json']
    baseline, holdout = [json.loads(path.read_text()) for path in paths]
    report['datasets'] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    facts = {item['id']: item for item in baseline['memories'] if scope(item['scope']).persona_id == 'mia'}
    policy, encoder = FixturePolicy(), SemanticEncoder(directory)
    report.update(cases=[], violations=[], quality={}, timing_ms={})
    elapsed = {'legacy': [], 'ported': []}
    with tempfile.TemporaryDirectory() as folder:
        store = MemoryStore(Path(folder) / 'comparison.sqlite3')
        try:
            for item in facts.values():
                current = scope(item['scope'])
                store.snapshot(current.owner)
                with store.db:
                    store.db.execute('INSERT INTO memories(id,owner,category,content,confirmed,created_at,updated_at) '
                        'VALUES (?,?,?,?,?,1,1)', (item['id'], current.owner, 'fact', item['content'], item['state'] != 'pending'))
                    store.db.execute('INSERT INTO sources(memory_id,session_id,fingerprint,excerpt,created_at) VALUES (?,?,?,?,1)',
                        (item['id'], 'frozen-synthetic-user', item['id'], item['content']))
                    store.db.execute('UPDATE mia_memory_meta SET entities=?,keywords=?,trigger_terms=?,emotional_weight=?,state=? '
                        'WHERE memory_id=?', (json.dumps([item['entity']], ensure_ascii=False), json.dumps(item['keywords'], ensure_ascii=False),
                        json.dumps(item['triggers'], ensure_ascii=False), item['weight'] / 10, item['state'], item['id']))
            index = SemanticMemoryIndex(store, encoder)
            for name in sorted({item['scope'] for item in facts.values()}):
                while (await index.rebuild_batch(scope(name), policy))['has_more']:
                    pass
            runtime = YoumiMemoryRuntime(index)
            before = '\n'.join(store.db.iterdump())
            for suite, cases in [('regression', baseline['queries']), ('independent', holdout['queries'])]:
                for case in cases:
                    current = scope(case.get('scope', 'J/daily/mia'))
                    if current.persona_id != 'mia':
                        continue
                    outputs = {}
                    for engine, retrieve in [('legacy', index.retrieve), ('ported', runtime.shadow_retrieve)]:
                        started = time.perf_counter()
                        result = await retrieve(current, policy, case['query'])
                        elapsed[engine].append((time.perf_counter() - started) * 1000)
                        rows = result['results']
                        outputs[engine] = [row['id'] for row in rows]
                        if len(rows) > 4:
                            report['violations'].append(dict(case=case['id'], engine=engine, reason='result_budget'))
                        for row in rows:
                            fact = facts.get(row['id'])
                            if not fact or fact['scope'] != case.get('scope', 'J/daily/mia') or fact['state'] != 'active':
                                report['violations'].append(dict(case=case['id'], engine=engine, reason='scope_or_state'))
                            if fact and row['content'] != fact['content']:
                                report['violations'].append(dict(case=case['id'], engine=engine, reason='rewritten_fact'))
                        if result['snapshot'] != dict(zip(('revision', 'content_epoch'), index._snapshot(current))):
                            report['violations'].append(dict(case=case['id'], engine=engine, reason='snapshot'))
                    report['cases'].append(dict(case, suite=suite, **outputs))
            report['canonical_unchanged'] = before == '\n'.join(store.db.iterdump())
        finally:
            store.close()
    quality_gates = []
    for suite in ('regression', 'independent'):
        report['quality'][suite] = {}
        for engine in ('legacy', 'ported'):
            cases = [dict(case, retrieved=case[engine]) for case in report['cases']
                if case['suite'] == suite and case.get('split', 'holdout') == 'holdout']
            report['quality'][suite][engine] = metrics(cases)
        old, new = report['quality'][suite]['legacy'], report['quality'][suite]['ported']
        quality_gates.append(meets_quality(new) and all(new[key] >= old[key]
            for key in ('complete_recall_at_4', 'top1', 'empty_recall')))
    for engine, times in elapsed.items():
        report['timing_ms'][engine] = dict(count=len(times), p50=float(np.percentile(times, 50)),
            p95=float(np.percentile(times, 95)))
    report['gates'] = dict(quality=all(quality_gates), isolation_and_state=not report['violations'],
        canonical_unchanged=report['canonical_unchanged'])
    report['passed'] = all(report['gates'].values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = dict(commit=os.environ.get('GITHUB_SHA'), passed=False,
        scope='frozen synthetic retrieval quality; no production data, chat model or answer-quality claim',
        resource_limit=dict(cpus=2, memory_mib=2048, network='none'))
    try:
        asyncio.run(evaluate(args.model_dir, report))
    finally:
        report['peak_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['passed']:
        raise SystemExit('Frozen memory quality gate failed; inspect preserved case results')


if __name__ == '__main__':
    main()
