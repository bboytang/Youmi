"""P13 installed hybrid retrieval with fixed real BGE; no provider or production data."""

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

import numpy as np

from youmi_companion.authorization import AuthenticatedScope, PrivateAuthorizationRequired
from youmi_companion.embedding import DIMENSION, MODEL_KEY, REVISION, SemanticEncoder
from youmi_companion.memory import MemoryStore
from youmi_companion.retrieval import WEIGHTS, THRESHOLD, SEMANTIC_FLOOR, rank
from youmi_companion.semantic import SemanticMemoryIndex


class FixturePolicy:
    """Only a policy fixture. Actual manager identity runs separately."""
    async def require(self, scope, permission):
        if permission not in scope.permissions:
            raise PrivateAuthorizationRequired()


def scope(name):
    user, agent, persona = name.split('/')
    user_id = 1 if user == 'J' else 2
    return AuthenticatedScope(f'user:{user_id}:agent:{agent}', user_id, user_id, agent, persona,
        'fixture-device', 'fixture-client', 0, 1, datetime.now(timezone.utc) + timedelta(days=1),
        frozenset({'memory:read', 'memory:write', 'memory:manage'}))


def metrics(cases):
    positive = [c for c in cases if c['expected']]
    negative = [c for c in cases if not c['expected'] and c.get('type') != 'related_unanswerable']
    related = [c for c in cases if c.get('type') == 'related_unanswerable']
    return {'positive_count': len(positive), 'negative_count': len(negative),
        'complete_recall_at_4': sum(set(c['expected']) <= set(c['retrieved']) for c in positive) / max(1, len(positive)),
        'top1': sum(bool(c['retrieved']) and c['retrieved'][0] in c['expected'] for c in positive) / max(1, len(positive)),
        'empty_recall': sum(not c['retrieved'] for c in negative) / max(1, len(negative)),
        'related_unanswerable_count': len(related),
        'related_expected_fact_recalled': sum(bool(set(c['retrieved']) & set(c['allowed_related'])) for c in related),
        'provider_answer_abstention': 'not_executed_P15_and_P22'}


def meets_quality(value):
    return value['complete_recall_at_4'] >= 0.90 and value['top1'] >= 0.80 and value['empty_recall'] >= 0.95


async def association_control(encoder, policy, path):
    fixture = json.loads(path.read_text())
    current = scope('K/association-control/mia')
    with tempfile.TemporaryDirectory() as folder:
        store = MemoryStore(Path(folder) / 'association.sqlite3')
        try:
            store.snapshot(current.owner)
            target = fixture['target']
            records = [(target['id'], target['content'])] + [(f'distractor-{i:02}', text) for i, text in enumerate(fixture['distractors'])]
            with store.db:
                for memory_id, content in records:
                    store.db.execute('INSERT INTO memories(id,owner,category,content,confirmed,created_at,updated_at) '
                        'VALUES (?,?,?,?,1,1,1)', (memory_id, current.owner, 'fact', content))
                    store.db.execute('INSERT INTO sources(memory_id,session_id,fingerprint,excerpt,created_at) VALUES (?,?,?,?,1)',
                        (memory_id, 'controlled-synthetic-user', memory_id, content))
                store.db.execute('UPDATE mia_memory_meta SET entities=?,keywords=?,trigger_terms=?,emotional_weight=? WHERE memory_id=?',
                    (json.dumps([target['entity']], ensure_ascii=False), json.dumps(target['keywords'], ensure_ascii=False),
                     json.dumps(target['triggers'], ensure_ascii=False), target['weight'] / 10, target['id']))
            index = SemanticMemoryIndex(store, encoder)
            await index.rebuild_batch(current, policy)
            original = (await index.candidates(current, policy, fixture['query']))['candidates']
            result = await index.retrieve(current, policy, fixture['query'])
            target_row = next((r for r in result['candidates'] if r['id'] == target['id']), None)
            plain = [dict(row, signals=dict(row['signals'], semantic=max(0, row['original_cosine'])))
                     for row in result['candidates'] if 'original' in row['candidate_sources']]
            without = [row['id'] for row in rank(plain)]
            original_ids = [row['id'] for row in original]
            recovered = [row['id'] for row in result['results']]
            passed = (target['id'] not in original_ids and target['id'] not in without and target['id'] in recovered
                and target_row is not None and 'expanded' in target_row['candidate_sources']
                and target_row['expanded_cosine'] > target_row['original_cosine'])
            return {'passed': passed, 'kind': 'controlled_real_BGE_not_independent_quality_holdout',
                'floor_rejected': target_row is not None and target_row['original_cosine'] < SEMANTIC_FLOOR and target['id'] not in recovered,
                'fixture_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'original_top12': original_ids,
                'without_expansion': without, 'retrieved': recovered, 'diagnostics': result}
        finally:
            store.close()


async def evaluate(directory, report):
    encoder, policy = SemanticEncoder(directory), FixturePolicy()
    fixtures = Path('/experiment/server/tests/fixtures')
    baseline_path = fixtures / 'persona_memory_retrieval_zh_v5.json'
    independent_path = fixtures / 'persona_memory_retrieval_p13_holdout.json'
    baseline, independent = [json.loads(p.read_text()) for p in (baseline_path, independent_path)]
    report['datasets'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (baseline_path, independent_path)}
    facts = {m['id']: m for m in baseline['memories']}
    with tempfile.TemporaryDirectory() as folder:
        store = MemoryStore(Path(folder) / 'retrieval.sqlite3')
        try:
            report['unsupported_persona_fixture_ids'] = []
            for item in facts.values():
                current = scope(item['scope'])
                if current.persona_id != 'mia':
                    report['unsupported_persona_fixture_ids'].append(item['id'])
                    continue
                store.snapshot(current.owner)
                with store.db:
                    store.db.execute('INSERT INTO memories(id,owner,category,content,confirmed,created_at,updated_at) '
                        'VALUES (?,?,?,?,?,1,1)', (item['id'], current.owner, 'fact', item['content'], item['state'] != 'pending'))
                    store.db.execute('INSERT INTO sources(memory_id,session_id,fingerprint,excerpt,created_at) VALUES (?,?,?,?,1)',
                        (item['id'], 'synthetic-user-fixture', item['id'], item['content']))
                    store.db.execute('UPDATE mia_memory_meta SET entities=?,keywords=?,trigger_terms=?,emotional_weight=?,state=? '
                        'WHERE memory_id=?', (json.dumps([item['entity']], ensure_ascii=False), json.dumps(item['keywords'], ensure_ascii=False),
                        json.dumps(item['triggers'], ensure_ascii=False), item['weight'] / 10, item['state'], item['id']))
            index = SemanticMemoryIndex(store, encoder)
            for name in sorted({m['scope'] for m in facts.values() if scope(m['scope']).persona_id == 'mia'}):
                while (await index.rebuild_batch(scope(name), policy))['has_more']:
                    pass
            # No lexical fallback is possible in this actual installed path.
            with store.db:
                store.db.execute('DROP TABLE memory_search')
            report['legacy_fts_absent'] = True
            try:
                await index.retrieve(replace(scope('J/daily/mia'), persona_id='tutor'), policy, '乐器')
            except PrivateAuthorizationRequired:
                report['unsupported_persona_rejected'] = True
            else:
                raise AssertionError('Unsupported persona authorized')
            report['cases'], times = [], []
            maximum_contributions = {key: 0 for key in WEIGHTS}
            expansion_changes = supplements = 0
            for suite, queries in (('P06_v5_regression', baseline['queries']), ('P13_independent_v1', independent['queries'])):
                for case in queries:
                    current = scope(case.get('scope', 'J/daily/mia'))
                    await index.retrieve(current, policy, case['query'])  # warmup
                    for _ in range(5):
                        started = time.perf_counter()
                        result = await index.retrieve(current, policy, case['query'])
                        times.append((time.perf_counter() - started) * 1000)
                    for row in result['candidates']:
                        fact = facts[row['id']]
                        assert fact['state'] == 'active' and fact['scope'] == case.get('scope', 'J/daily/mia')
                        for key in WEIGHTS:
                            maximum_contributions[key] = max(maximum_contributions[key], row['signals'][key] * WEIGHTS[key])
                    for clue in result['expansion']:
                        fact = facts[clue['memory_id']]
                        assert fact['state'] == 'active' and fact['scope'] == case.get('scope', 'J/daily/mia')
                    original = [dict(row, signals=dict(row['signals'], semantic=max(0, row['original_cosine'])))
                                for row in result['candidates'] if 'original' in row['candidate_sources']]
                    plain = [r['id'] for r in rank(original)]
                    retrieved = [r['id'] for r in result['results']]
                    expansion_changes += bool(result['expansion']) and retrieved != plain
                    supplements += any('original' not in row['candidate_sources'] and 'expanded' not in row['candidate_sources']
                                       for row in result['results'])
                    ablation = {}
                    for key in WEIGHTS:
                        disabled = [dict(row, signals=dict(row['signals'], **{key: 0})) for row in result['candidates']]
                        ablation[key] = [r['id'] for r in rank(disabled)]
                    report['cases'].append({**case, 'suite': suite, 'retrieved': retrieved, 'without_expansion': plain,
                        'expansion': result['expansion'], 'snapshot': result['snapshot'], 'ablation': ablation,
                        'candidates': [{key: row[key] for key in ('id', 'content_version', 'evidence_version', 'original_cosine',
                            'expanded_cosine', 'signals', 'score', 'accepted', 'candidate_sources', 'lexical_hits', 'association_hits')}
                            for row in result['candidates']]})
            report['quality'] = {}
            for name in ('P06_v5_regression', 'P13_independent_v1'):
                cases = [c for c in report['cases'] if c['suite'] == name and c.get('split', 'holdout') == 'holdout']
                report['quality'][name] = metrics(cases)
            report['signal_max_weighted_contributions'] = maximum_contributions
            report['expansion_changes_vs_original_top12'] = expansion_changes
            report['results_only_from_bounded_supplement'] = supplements
            report['ablation_ranking_changes'] = {key: sum(c['ablation'][key] != c['retrieved'] for c in report['cases']) for key in WEIGHTS}
            report['query_pipeline_ms'] = {'count': len(times), 'p50': float(np.percentile(times, 50)), 'p95': float(np.percentile(times, 95))}
        finally:
            store.close()
    report['association_floor_control'] = await association_control(encoder, policy, fixtures / 'persona_memory_association_control.json')
    report['association_control'] = await association_control(encoder, policy, fixtures / 'persona_memory_association_control_v2.json')
    report['capacity'] = {}
    current = scope('J/daily/mia')
    seed = encoder.encode('我在阳台种薄荷。').astype('<f4').tobytes()
    for size in (1000, 10000):
        with tempfile.TemporaryDirectory() as folder:
            store = MemoryStore(Path(folder) / 'capacity.sqlite3')
            try:
                store.snapshot(current.owner)
                with store.db:
                    store.db.executemany('INSERT INTO memories(id,owner,category,content,confirmed,created_at,updated_at) '
                        'VALUES (?,?,?, ?,1,1,1)', [(f'capacity-{i:05}', current.owner, 'fact', f'合成容量记录{i}') for i in range(size)])
                    store.db.executemany('INSERT INTO mia_vectors(memory_id,model_id,model_revision,dimension,content_version,normalized_vector,indexed_at) '
                        'VALUES (?,?,?,?,1,?,1)', [(f'capacity-{i:05}', MODEL_KEY, REVISION, DIMENSION, seed) for i in range(size)])
                index = SemanticMemoryIndex(store, encoder)
                await index.retrieve(current, policy, '薄荷应该放在哪里？')
                elapsed = []
                for _ in range(10):
                    started = time.perf_counter()
                    await index.retrieve(current, policy, '薄荷应该放在哪里？')
                    elapsed.append((time.perf_counter() - started) * 1000)
                report['capacity'][str(size)] = {'count': len(elapsed), 'pipeline_p95_ms': float(np.percentile(elapsed, 95)),
                    'fixture': 'synthetic short records without metadata; repeated real seed vector; capacity only'}
            finally:
                store.close()
    report['peak_rss_mib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    report['passed'] = (all(meets_quality(q) for q in report['quality'].values()) and report['unsupported_persona_rejected']
        and all(value > 0 for value in maximum_contributions.values())
        and report['association_control']['passed']
        and report['association_floor_control']['floor_rejected']
        and report['query_pipeline_ms']['p95'] <= 400 and report['capacity']['10000']['pipeline_p95_ms'] <= 400
        and report['peak_rss_mib'] <= 512)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = {'code_sha': os.environ['GITHUB_SHA'], 'kind': 'P13_real_BGE_installed_SQLite_hybrid', 'passed': False,
              'weights': WEIGHTS, 'threshold': THRESHOLD, 'semantic_floor': SEMANTIC_FLOOR,
              'model_id': MODEL_KEY, 'model_revision': REVISION, 'resource_limit': {'cpu': 2, 'memory_mib': 2048, 'network': 'none'},
              'manager': 'fixture_only_actual_manager_separate', 'full_behavior': 'not_accepted'}
    try:
        asyncio.run(evaluate(args.model_dir, report))
    except Exception as error:
        report['passed'] = False
        report['error_class'] = type(error).__name__
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key not in ('cases',)}, ensure_ascii=False))
    if not report['passed']:
        raise SystemExit('P13 retrieval verification failed; inspect preserved diagnostics')


if __name__ == '__main__':
    main()
