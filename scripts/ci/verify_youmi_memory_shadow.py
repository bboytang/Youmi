"""Installed BGE/Tantivy shadow mechanism checks, synthetic data and no network."""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import resource
import tempfile
import time

from youmi_companion import emotional_system
from youmi_companion.authorization import AuthenticatedScope, PrivateAuthorizationRequired
from youmi_companion.embedding import SemanticEncoder
from youmi_companion.memory import MemoryStore
from youmi_companion.jobs import JobStore, MemoryWorker, TurnCompleted
from youmi_companion.memory_runtime import YoumiMemoryRuntime
from youmi_companion.semantic import SemanticMemoryIndex


class FixturePolicy:
    def __init__(self, scope):
        self.scope = scope

    async def require(self, scope, permission):
        if scope != self.scope or permission not in scope.permissions:
            raise PrivateAuthorizationRequired()

    async def restore(self, snapshot):
        if snapshot != self.scope.snapshot():
            raise PrivateAuthorizationRequired()
        return self.scope


async def verify(directory, report):
    scope = AuthenticatedScope('user:1:agent:shadow-ci', 1, 1, 'shadow-ci', 'mia',
        'fixture-device', 'fixture-client', 0, 1, datetime.now(timezone.utc) + timedelta(days=1),
        frozenset({'memory:read', 'memory:write', 'memory:manage'}))
    policy, encoder = FixturePolicy(scope), SemanticEncoder(directory)
    with tempfile.TemporaryDirectory() as folder:
        store = MemoryStore(Path(folder) / 'canonical.sqlite3', clock=lambda: 1800000000)
        try:
            def remember(owner, text, marker):
                return store.apply_extraction(owner, marker, store.snapshot(owner)['revision'], [text],
                    {'memories': [{'category': 'fact', 'content': text, 'source_index': 0}]}, marker)['memories'][0]
            target = remember(scope.owner, '我旅行时喜欢带一壶热茶', 'tea')
            remember(scope.owner, '我练舞时喜欢听轻音乐', 'music')
            foreign = remember('user:1:agent:foreign-ci', '我旅行时喜欢带一壶热茶', 'foreign')
            state = emotional_system.status(store, scope.owner)
            emotional_system.manage(store, scope.owner, dict(action='configure', version=0,
                enabled=True, dimensions=state['dimensions']))
            index = SemanticMemoryIndex(store, encoder)
            await index.rebuild_batch(scope, policy)
            runtime = YoumiMemoryRuntime(index)
            baseline = await runtime.retrieve(scope, policy, '旅行热茶')
            database = list(store.db.iterdump())
            emotion = emotional_system.status(store, scope.owner)
            report['cases'] = []
            for query, use_vectors in [('旅行热茶', False), ('旅行热茶', True), ('出门游玩会随身带什么饮品', True)]:
                started = time.perf_counter()
                result = await runtime.shadow_retrieve(scope, policy, query, use_vectors=use_vectors)
                assert target in [row['id'] for row in result['results']]
                assert foreign not in [row['id'] for row in result['results']]
                assert all(row['source'] is not None for row in result['results'])
                assert result['shadow_only']
                if use_vectors:
                    assert result['vector_status'] == 'ready'
                    assert all(row['retrieval']['score_kind'] == 'rrf' for row in result['results'])
                report['cases'].append(dict(query=query, use_vectors=use_vectors,
                    elapsed_ms=round(1000 * (time.perf_counter() - started), 2), result=result))
            assert list(store.db.iterdump()) == database
            assert emotional_system.status(store, scope.owner) == emotion
            assert len(emotion['values']) == 51
            assert await runtime.retrieve(scope, policy, '旅行热茶') == baseline
            store.edit_memory(scope.owner, target, '我旅行时喜欢带温水')
            corrected = await runtime.shadow_retrieve(scope, policy, '热茶')
            assert '我旅行时喜欢带一壶热茶' not in json.dumps(corrected, ensure_ascii=False)
            assert corrected['vector_status'] == 'unavailable'
            report['checks'] = dict(real_bge=True, real_tantivy=True, canonical_sources=True,
                owner_isolation=True, read_only_shadow=True, all_51_emotions_preserved=True,
                default_engine_unchanged=True, old_content_not_resurrected=True)
            # Installed-package boundary: synthetic reflection, actual worker/DB,
            # no new model call. Default extraction and recall remain unchanged.
            fact = dict(store.db.execute('SELECT * FROM memories WHERE owner=? AND content=?',
                (scope.owner, '我练舞时喜欢听轻音乐')).fetchone())
            async def resolve(verified):
                return lambda sources, context: dict(reflection_version=1, memory_actions=[
                    dict(action=action, key=key, title='练舞', evidence=[dict(id=fact['id'], quote=fact['content'])])
                    for action, key in [('profile', 'attribute'), ('note', 'practice'), ('propose_retirement', 'practice')]])
            jobs = JobStore(store, policy_version=1)
            worker = MemoryWorker(jobs, policy, resolve, encoder=encoder)
            event = TurnCompleted(scope, 'reflection-fixture', 'completed-fixture', 'fixture-message',
                '今天继续聊聊', store.snapshot(scope.owner)['revision'])
            admitted = await worker.submit(event)
            assert await worker.run_once()
            assert jobs.current(admitted)['state'] == 'committed'
            artifacts = await runtime.list_artifacts(scope, policy)
            assert len(artifacts) == 3
            assert all(item['body'] == fact['content'] for item in artifacts)
            assert all(item['preview_only'] for item in artifacts if item['kind'] == 'retirement')
            assert emotional_system.status(store, scope.owner) == emotion
            store.edit_memory(scope.owner, fact['id'], '我练舞时喜欢听节奏音乐')
            assert await runtime.list_artifacts(scope, policy) == []
            report['checks'].update(installed_reflection_artifacts=True, artifact_source_invalidation=True,
                maintenance_preview_only=True)
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = dict(commit=os.environ.get('GITHUB_SHA'), passed=False,
        scope='installed search mechanisms, tiny synthetic corpus; not a quality or production benchmark')
    try:
        asyncio.run(verify(args.model_dir, report))
        report['passed'] = True
    finally:
        report['peak_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
