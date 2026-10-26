"""Installed SDK extraction -> real BGE -> atomic SQLite -> scoped context; fixture SSE/JSON."""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import sqlite3
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import httpx
import openai

logger = SimpleNamespace()
logger.bind = lambda *args, **kwargs: logger
for level in ('debug', 'info', 'warning', 'error'):
    setattr(logger, level, lambda *args, **kwargs: None)
logging = ModuleType('config.logger')
logging.setup_logging = lambda: logger
sys.modules['config.logger'] = logging
util = ModuleType('core.utils.util')
util.check_model_key = lambda *args, **kwargs: None
sys.modules['core.utils.util'] = util

from core.providers.llm.openai.openai import LLMProvider
from youmi_companion.authorization import AuthenticatedScope, PrivateAuthorizationRequired
from youmi_companion.context import ContextBuilder, ContextError, ContextLimits
from youmi_companion.embedding import SemanticEncoder
from youmi_companion.extraction import LLMMemoryExtractor, normalize
from youmi_companion.jobs import JobStore, MemoryWorker, TurnCompleted
from youmi_companion.memory import MemoryError, MemoryStore
from youmi_companion.semantic import SemanticMemoryIndex


class Policy:
    async def restore(self, snapshot):
        if snapshot != self.scope.snapshot():
            raise PrivateAuthorizationRequired()
        return self.scope

    async def require(self, scope, permission):
        if scope != self.scope or permission not in scope.permissions:
            raise PrivateAuthorizationRequired()


async def evaluate(args, report):
    import tempfile
    encoder = SemanticEncoder(args.model_dir)
    dataset = json.loads(args.dataset.read_text())
    report['dataset_sha256'] = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    policy = Policy()
    scope = policy.scope = AuthenticatedScope('user:1:agent:extraction', 1, 1, 'extraction', 'mia', 'device', 'client', 0, 1,
        datetime.now(timezone.utc) + timedelta(days=1), frozenset({'memory:read', 'memory:write', 'memory:manage'}))
    current, requests = None, []
    with tempfile.TemporaryDirectory() as folder:
        store = MemoryStore(Path(folder) / 'facts.sqlite3')
        index = SemanticMemoryIndex(store, encoder)
        def transport(request):
            body = json.loads(request.content)
            context = json.loads(body['messages'][1]['content'])
            assert 'FOREIGN_PRIVATE' not in request.content.decode()
            assert context['user_excerpts'] == [current['source']]
            requests.append({'case': current['id'], 'model': body['model']})
            assert body['stream'] is True
            chunk = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 0,
                'model': 'synthetic', 'choices': [{'index': 0, 'finish_reason': 'stop',
                    'delta': {'content': json.dumps(current['output'], ensure_ascii=False)}}]}
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                content=('data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\ndata: [DONE]\n\n').encode())
        def factory():
            provider = LLMProvider({'api_key': 'synthetic-only', 'model_name': 'synthetic',
                'base_url': 'https://ci.invalid/v4', 'max_tokens': 768, 'timeout': 5})
            provider.client.close()
            provider.client = openai.OpenAI(api_key='synthetic-only', base_url=provider.base_url, max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(transport)))
            return provider
        async def resolve(restored):
            assert restored == scope
            return LLMMemoryExtractor(factory)
        jobs = JobStore(store)
        worker = MemoryWorker(jobs, policy, resolve, encoder=encoder)
        # Foreign facts exist, but never enter extraction context or own RAG.
        store.apply_extraction('user:2:agent:other', 'other', 0, ['FOREIGN_PRIVATE'],
            {'memories': [{'category': 'fact', 'content': 'FOREIGN_PRIVATE', 'source_index': 0}]}, 'other')
        report['cases'] = []
        targets = {}
        for row in dataset['cases']:
            current = json.loads(json.dumps(row))
            if row['id'] == 'relation':
                current['output']['memories'][0]['relations'] = [{'target_id': targets['neutral'],
                    'relation': 'shared_term', 'anchor': '吃素', 'evidence': row['source']}]
            event = TurnCompleted(scope, 'ci-session', row['id'], row['id'], row['source'], store.snapshot(scope.owner)['revision'])
            start = len(requests)
            if row['expected'] == 'credential_rejected':
                try:
                    await worker.submit(event)
                except MemoryError as error:
                    assert error.code == 'credentials_forbidden'
                else:
                    raise AssertionError('Credential source persisted')
                report['cases'].append({'id': row['id'], 'state': 'credential_rejected', 'sdk_calls': 0})
                continue
            accepted = await worker.submit(event)
            assert accepted['status'] == 'accepted'
            if row['id'] == 'neutral':
                with patch.object(jobs, 'commit', side_effect=sqlite3.OperationalError('fixture rollback')):
                    await worker.run_once()
                assert jobs.current(accepted)['state'] == 'retry_wait'
                assert len(requests) - start == 1
                assert store.list_memories(scope.owner) == []
                with store.db:
                    store.db.execute('UPDATE mia_jobs SET next_attempt_at=0 WHERE id=?', (accepted['id'],))
            await worker.run_once()
            if row['expected'] == 'invalid':
                with store.db:
                    store.db.execute('UPDATE mia_jobs SET next_attempt_at=0 WHERE id=?', (accepted['id'],))
                await worker.run_once()
            task = jobs.current(accepted)
            expected = {'active': 'committed', 'pending': 'committed', 'empty': 'empty', 'invalid': 'failed'}[row['expected']]
            assert task['state'] == expected, (row['id'], task['state'], task['error_category'])
            details = {'id': row['id'], 'state': task['state'], 'sdk_calls': len(requests) - start}
            if expected == 'committed':
                source = store.db.execute('SELECT sm.memory_id,sm.source_type,m.confirmed,x.confirmation_origin '
                    'FROM mia_source_meta sm JOIN memories m ON m.id=sm.memory_id JOIN mia_memory_meta x ON x.memory_id=m.id '
                    'WHERE m.owner=? AND sm.turn_id=? AND sm.message_id=?', (scope.owner, row['id'], row['id'])).fetchone()
                assert source is not None
                targets[row['id']] = source['memory_id']
                assert bool(source['confirmed']) == (row['expected'] == 'active')
                indexed = store.db.execute('SELECT COUNT(*) FROM mia_vectors WHERE memory_id=?', (source['memory_id'],)).fetchone()[0]
                assert indexed == int(row['expected'] == 'active')
                if row['expected'] == 'active':
                    query = '周三在食堂怎么吃？' if row['id'] == 'neutral' else row['source']
                    found = await index.retrieve(scope, policy, query)
                    assert source['memory_id'] in [item['id'] for item in found['results']], (row['id'], 'RAG miss')
                    bundle = await ContextBuilder(index).build(scope, policy, 'context-' + row['id'], query,
                        limits=ContextLimits('synthetic', 16384, 768, 'CI synthetic capacity; not production'))
                    assert source['memory_id'] in bundle.diagnostics()['selected_fact_ids']
                    assert 'FOREIGN_PRIVATE' not in bundle.request_json
                else:
                    assert json.loads(task['validated_result']) == {'confirmation_required': True}
                details.update(source_type=source['source_type'], confirmation_origin=source['confirmation_origin'], indexed=bool(indexed))
            if row['expected'] == 'invalid':
                assert task['error_category'] == 'source_semantic' and task['attempts'] == 2
            assert task['source_payload'] == '{}'
            report['cases'].append(details)
        assert store.db.execute('SELECT COUNT(*) FROM mia_associations').fetchone()[0] == 1
        report['sdk_calls'] = len(requests)
        report['db_retry_did_not_repeat_model'] = report['cases'][0]['sdk_calls'] == 1
        report['confirmed_fact_count'] = store.db.execute('SELECT COUNT(*) FROM memories WHERE owner=? AND confirmed=1', (scope.owner,)).fetchone()[0]
        report['pending_fact_count'] = store.db.execute('SELECT COUNT(*) FROM memories WHERE owner=? AND confirmed=0', (scope.owner,)).fetchone()[0]
        # P18 retains the same Worker and real BGE; the explicit user protocol
        # no longer needs an SDK response. Ordinary extraction above still uses SDK fixtures.
        report['maintenance'] = []
        target = targets['neutral']
        second = MemoryStore(Path(folder) / 'facts.sqlite3')
        other_builder = ContextBuilder(SemanticMemoryIndex(second, encoder))
        limits = ContextLimits('synthetic', 16384, 768, 'CI synthetic capacity; not production')
        for mode, prefix, new in [('correction', '更正：', '我每周四都会在公司食堂吃素'),
                ('time_change', '以前：', '现在：我每周五都会在公司食堂吃素'),
                ('conflict', '不确定：', '我每周六都会在公司食堂吃素')]:
            old = store.db.execute('SELECT content FROM memories WHERE id=?', (target,)).fetchone()[0]
            cached = await other_builder.build(scope, policy, 'before-' + mode, old, limits=limits)
            text = prefix + old + '；' + new
            current = {'id': 'p18-' + mode, 'source': text, 'output': {'memories': [{
                'category': 'fact', 'content': new, 'source_quote': new, 'source_index': 0,
                'kind': 'explicit', 'subject': 'user', 'value': 'durable', 'sensitive': False,
                'reconciliation': {'target_id': target, 'kind': mode, 'old_quote': old, 'evidence': text}}]}}
            event = TurnCompleted(scope, 'p18-session', current['id'], current['id'], text, store.snapshot(scope.owner)['revision'])
            accepted = await worker.submit(event)
            start = len(requests)
            if mode == 'correction':
                with patch.object(store, '_index', side_effect=sqlite3.OperationalError('p18 rollback')):
                    await worker.run_once()
                assert jobs.current(accepted)['state'] == 'retry_wait'
                assert store.db.execute('SELECT content FROM memories WHERE id=?', (target,)).fetchone()[0] == old
                with store.db:
                    store.db.execute('UPDATE mia_jobs SET next_attempt_at=0 WHERE id=?', (accepted['id'],))
            if mode == 'correction':
                assert json.loads(jobs.current(accepted)['validated_result'])['_schema'] == 1
                with patch.object(worker, 'resolve', side_effect=AssertionError('DB retry resolved the extractor again')):
                    await worker.run_once()
            else:
                await worker.run_once()
            assert jobs.current(accepted)['state'] == 'committed', jobs.current(accepted)
            assert len(requests) == start, 'Explicit user reconciliation called the model'
            try:
                await other_builder.validate(cached, policy)
            except ContextError:
                pass
            else:
                raise AssertionError('Other connection retained old context')
            fresh = await other_builder.build(scope, policy, 'after-' + mode, new, limits=limits)
            assert old not in fresh.request_json
            new_id = store.db.execute('SELECT id FROM memories WHERE owner=? AND content=?', (scope.owner, normalize(new))).fetchone()[0]
            if mode != 'conflict':
                assert normalize(new) in fresh.request_json
                target = new_id
                assert target in fresh.diagnostics()['selected_fact_ids']
                assert store.db.execute('SELECT COUNT(*) FROM mia_vectors WHERE memory_id=?', (target,)).fetchone()[0] == 1
            else:
                # The user's current query is retained; only fact selection is restricted.
                assert target not in fresh.diagnostics()['selected_fact_ids']
                assert new_id not in fresh.diagnostics()['selected_fact_ids']
                assert json.loads(jobs.current(accepted)['validated_result']) == {'confirmation_required': True}
            report['maintenance'].append({'kind': mode, 'state': 'committed', 'sdk_calls': len(requests) - start,
                'old_context_rejected': True, 'old_fact_not_current': True,
                'producer': 'validated_explicit_user_protocol', 'retry_reused_validation': mode == 'correction'})
        second.close()
        # P19/P20 installed mechanisms: real BGE, synthetic outputs and an EXPLICIT simulated clock.
        from youmi_companion import emotions
        target = targets['relation']
        old = store.db.execute('SELECT content FROM memories WHERE id=?', (target,)).fetchone()[0]
        builder = ContextBuilder(index)
        query = '你还记得' + old + '吗'
        before = await builder.build(scope, policy, 'p19-first', query, limits=limits, session_id='p19-session')
        assert before.diagnostics()['events'] and before.diagnostics()['events'][-1]['state'] == 'pending'
        assert await builder.complete(before, policy, '😌记得，' + old + '。')
        store.close()
        store = MemoryStore(Path(folder) / 'facts.sqlite3')
        index = SemanticMemoryIndex(store, encoder)
        builder = ContextBuilder(index)
        after = await builder.build(scope, policy, 'p19-restart', query, limits=limits, session_id='different-session')
        assert after.diagnostics()['events'][0]['state'] == 'active'
        assert store.db.execute('SELECT COUNT(*) FROM mia_events').fetchone()[0] == 1
        store.delete_memory(scope.owner, target)
        removed = await builder.build(scope, policy, 'p19-deleted', query, limits=limits, session_id='third-session')
        assert removed.diagnostics()['events'] == []
        report['persona_events'] = {'pending_then_completed': True, 'database_reopened': True,
            'new_session_restored': True, 'repeated_query_one_event': True, 'deleted_evidence_not_used': True,
            'assistant_output': 'synthetic_controlled_fixture'}
        assert emotions.trend(store, scope.owner)['status'] == 'disabled'
        assert store.handle(scope.owner, {'action': 'emotion_status'})['trend'] == {'status': 'disabled'}
        store.handle(scope.owner, {'action': 'emotion_consent', 'enabled': True})
        jobs = JobStore(store)
        worker = MemoryWorker(jobs, policy, resolve, encoder=encoder)
        epoch = int(store.clock()) // 86400 * 86400
        for i, day in enumerate((0, 1, 2, 4, 6)):
            store.clock = lambda day=day: epoch + day * 86400 + 3600
            current = {'id': 'p20-' + str(i), 'source': '我今天很难过', 'output': {'memories': [], 'observations': [{
                'source_index': 0, 'source_quote': '我今天很难过', 'subject': 'user', 'source_kind': 'self_report',
                'tendency': 'negative', 'confidence': 0.9}]}}
            admitted = await worker.submit(TurnCompleted(scope, 'mood-session-' + str(i % 3), current['id'], current['id'],
                current['source'], store.snapshot(scope.owner)['revision']))
            await worker.run_once()
            assert jobs.current(admitted)['state'] == 'committed'
        related = await builder.build(scope, policy, 'p20-related', '最近我的心情怎么样', limits=limits)
        assert related.diagnostics()['trends'] == 'available'
        store.handle(scope.owner, {'action': 'emotion_consent', 'enabled': False})
        try:
            await builder.validate(related, policy)
        except ContextError:
            pass
        else:
            raise AssertionError('Disabled trend snapshot remained usable')
        assert emotions.trend(store, scope.owner)['status'] == 'disabled'
        assert store.handle(scope.owner, {'action': 'emotion_status'})['trend'] == {'status': 'disabled'}
        report['emotion_trend'] = {'clock': 'SIMULATED_UTC_not_real_seven_day_acceptance',
            'observations': 5, 'sessions': 3, 'calendar_span_days': 7, 'related_context': True,
            'disable_retains_five_but_blocks_usage': store.db.execute('SELECT COUNT(*) FROM mia_observations').fetchone()[0] == 5}
        from youmi_companion.extraction import context_for
        from youmi_companion.memory_artifacts import YoumiMemoryArtifacts
        from youmi_companion.memory_reflection_policy import YoumiReflectionPolicy, status as reflection_status
        await YoumiReflectionPolicy(store).manage(scope, policy, dict(action='configure', enabled=True,
            interval_seconds=60, version=0))
        evidence = next(r for r in context_for(store, scope.owner) if r['evidence'])
        current = dict(id='reflection-maintenance', source='你好', output=dict(reflection_version=1, memory_actions=[
            dict(action='note', key='installed-reflection', title=evidence['content'][:8],
                evidence=[dict(id=evidence['id'], quote=evidence['content'])])]))
        start = len(requests)
        admitted = await worker.submit(TurnCompleted(scope, 'reflection-session', current['id'], current['id'],
            current['source'], store.snapshot(scope.owner)['revision']))
        await worker.run_once()
        assert jobs.current(admitted)['state'] == 'committed'
        assert len(requests) == start + 1
        assert store.db.execute('SELECT COUNT(*) FROM mia_model_requests WHERE job_id=?', (admitted['id'],)).fetchone()[0] == 1
        artifacts = await YoumiMemoryArtifacts(store).list(scope, policy)
        assert len(artifacts) == 1 and artifacts[0]['body'] == evidence['content']
        assert reflection_status(store, scope.owner)['last_maintenance_at'] is not None
        report['reflection_generation'] = dict(installed_provider=True, real_sdk_mock_transport=True,
            existing_request_budget=True, completed_job=True, maintenance_clock_committed=True,
            remote_model_calls=0, default_recall_unchanged=True)
        report['sdk_calls'] = len(requests)
        report['peak_rss_mib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        report['passed'] = True
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = {'code_sha': os.environ['GITHUB_SHA'], 'passed': False, 'kind': 'P17_installed_SDK_real_BGE_extraction',
        'chat_provider_calls': 0, 'model_outputs': 'synthetic_controlled_fixture', 'manager': 'policy_fixture_real_manager_separate',
        'resource_limit': {'cpu': 2, 'memory_mib': 2048, 'network': 'none'}, 'full_B03': 'not_accepted'}
    try:
        asyncio.run(evaluate(args, report))
    except Exception as error:
        import traceback
        report.update(error_class=type(error).__name__, failure=str(error), traceback=traceback.format_exc())
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'cases'}))
    if not report['passed']:
        raise SystemExit('P17 controlled extraction verification failed')


if __name__ == '__main__':
    main()
