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
            from youmi_companion import affect
            from youmi_companion.memory_emotion import DEFAULTS
            from youmi_companion.memory_emotion_context import YoumiEmotionContextBuilder
            from youmi_companion.context import ContextLimits
            from youmi_companion.memory_state import capture_turn
            await runtime.emotion_linkage(scope, policy, dict(DEFAULTS, action='configure', enabled=True))
            source = '我练舞时喜欢听轻音乐'
            async def resolve_link(verified):
                return lambda sources, context: {'memories': [dict(category='preference', content=source,
                    source_quote=source, source_index=0, kind='explicit', subject='user', value='durable')]}
            worker.resolve = resolve_link
            with store.lock, store.db:
                store.db.execute('BEGIN IMMEDIATE')
                capture_turn(store, scope.owner, 'link-fixture', 'captured', source)
            admitted = await worker.submit(TurnCompleted(scope, 'link-fixture', 'captured', 'new-source',
                source, store.snapshot(scope.owner)['revision']))
            assert await worker.run_once() and jobs.current(admitted)['state'] == 'committed'
            await index.rebuild_batch(scope, policy)
            identifier = store.db.execute('SELECT id FROM memories WHERE owner=? AND content=?', (scope.owner, source)).fetchone()[0]
            metadata = dict(store.db.execute('SELECT content_version,evidence_version FROM mia_memory_meta WHERE memory_id=?', (identifier,)).fetchone())
            query = source + '，谢谢你'
            default_before = await runtime.retrieve(scope, policy, query)
            builder = YoumiEmotionContextBuilder(runtime)
            bundle = await builder.build(scope, policy, 'linked-turn', query,
                limits=ContextLimits('fixture', 32768, 128, 'isolated synthetic capacity; not production'))
            proposal = affect.parse_appraisal(json.dumps(dict(version=2,
                changes=[dict(dimension='joy', delta=2, quote='谢谢你')], memory_changes=[
                    dict(metadata, memory_id=identifier, quote=source, dimension='joy', delta=40)]), ensure_ascii=False), query)
            proposal['system_version'] = emotional_system.status(store, scope.owner)['version']
            await builder.with_affect(bundle, policy, proposal)
            with store.lock, store.db:
                store.db.execute('BEGIN IMMEDIATE')
                assert affect.commit(store, scope.owner, 'link-fixture', 'linked-turn', query, proposal, 0)
                assert not affect.commit(store, scope.owner, 'link-fixture', 'linked-turn', query, proposal, 0)
            assert emotional_system.status(store, scope.owner)['values']['joy'] == 22
            assert store.db.execute('SELECT COUNT(*) FROM youmi_memory_emotion_cooldown').fetchone()[0] == 1
            ranked = await runtime.shadow_retrieve(scope, policy, '练舞轻音乐')
            assert identifier in [r['id'] for r in ranked['results']]
            assert all(1 <= r['retrieval']['emotion_factor'] <= 1.1 for r in ranked['results'])
            assert await runtime.retrieve(scope, policy, query) == default_before
            report['checks'].update(versioned_memory_emotion=True, cooldown_committed_once=True,
                emotion_ranking_bounded=True, legacy_recall_still_unchanged=True)
            # Explicit synthetic-only disk mode; the normal reply factory has
            # no cache directory and remains on the original engine.
            from youmi_companion.memory_index_cache import partition
            disk_root = Path(folder) / 'shadow-evaluation'
            disk = YoumiMemoryRuntime(index, shadow_cache_dir=disk_root)
            before_disk = list(store.db.iterdump())
            initial = await disk.shadow_retrieve(scope, policy, '练舞轻音乐')
            restarted = YoumiMemoryRuntime(index, shadow_cache_dir=disk_root)
            assert await restarted.shadow_retrieve(scope, policy, '练舞轻音乐') == initial
            cache_path = partition(disk_root, Path(folder) / 'canonical.sqlite3', scope.owner, scope.persona_id)
            (cache_path / 'dirty.json').write_text('{}')
            assert await restarted.shadow_retrieve(scope, policy, '练舞轻音乐') == initial
            assert not (cache_path / 'dirty.json').exists()
            assert list(store.db.iterdump()) == before_disk
            assert await runtime.retrieve(scope, policy, query) == default_before
            report['checks'].update(persistent_shadow_reopen=True, persistent_shadow_dirty_recovery=True,
                persistent_shadow_canonical_unchanged=True)
            # V15 persists cleanup intent, not a claim of physical disk erasure.
            from youmi_companion.memory_cache_invalidations import YoumiMemoryCacheInvalidations
            invalidations = YoumiMemoryCacheInvalidations(store)
            generation = next(row['generation'] for row in invalidations.pending() if row['owner'] == scope.owner)
            affect_before_delete = emotional_system.status(store, scope.owner)
            store.delete_memory(scope.owner, identifier)
            assert not invalidations.acknowledge(scope.owner, generation)
            reopened = MemoryStore(Path(folder) / 'canonical.sqlite3')
            try:
                pending = YoumiMemoryCacheInvalidations(reopened).pending()
                assert next(row['generation'] for row in pending if row['owner'] == scope.owner) > generation
                assert emotional_system.status(store, scope.owner) == affect_before_delete
            finally:
                reopened.close()
            report['checks'].update(invalidation_survives_reopen=True, invalidation_stale_ack_refused=True)
            # The managed path is separately opt-in and has a fixed private root.
            from youmi_companion.memory_cache_lifecycle import YoumiManagedMemoryCache
            from youmi_companion.memory_repository import YoumiMemoryRepository
            from youmi_companion.embedding import EmbeddingUnavailable
            import sqlite3
            managed_id = remember(scope.owner, '我周末喜欢看画展', 'managed-fixture')
            managed = YoumiMemoryRuntime(index, managed_shadow_cache=True)
            assert (await managed.shadow_retrieve(scope, policy, '画展', use_vectors=False))['results'][0]['id'] == managed_id
            coordinator = YoumiManagedMemoryCache(Path(folder) / 'canonical.sqlite3')
            managed_path = coordinator.directory(scope.owner)
            stale = await YoumiMemoryRepository(store).capture(scope, policy)
            store.delete_memory(scope.owner, managed_id)
            assert sorted(p.name for p in managed_path.iterdir()) == ['writer.lock']
            assert store.cache_cleanup_status(scope.owner)['state'] == 'complete'
            try:
                coordinator.search(stale, [dict(id=managed_id, judgment='我周末喜欢看画展', tags=[])], '画展', {})
            except EmbeddingUnavailable as error:
                assert str(error) == 'memory_changed'
            else:
                raise AssertionError('Stale managed corpus must not recreate deleted segments')
            (managed_path / 'partial-private-segment').write_text('synthetic interrupted segment')
            with sqlite3.connect(Path(folder) / 'canonical.sqlite3') as raw:
                raw.execute('UPDATE youmi_memory_cache_partitions SET writing=1 WHERE owner=?', (scope.owner,))
            reopened = MemoryStore(Path(folder) / 'canonical.sqlite3')
            reopened.close()
            assert sorted(p.name for p in managed_path.iterdir()) == ['writer.lock']
            report['checks'].update(managed_commit_purge=True, managed_no_stale_rewrite=True, managed_startup_recovery=True)
            from youmi_companion.memory_cache_cleanup import YoumiMemoryCleanupPump
            pending_segment = managed_path / 'idle-private-segment'
            pending_segment.write_text('synthetic idle residue')
            with sqlite3.connect(Path(folder) / 'canonical.sqlite3') as raw:
                raw.execute('UPDATE youmi_memory_cache_partitions SET writing=1 WHERE owner=?', (scope.owner,))
            pump = YoumiMemoryCleanupPump(Path(folder) / 'canonical.sqlite3', interval=.01)
            pump.start()
            try:
                deadline = asyncio.get_running_loop().time() + 3
                while asyncio.get_running_loop().time() < deadline:
                    if not pending_segment.exists() and store.cache_cleanup_status(scope.owner)['state'] == 'complete':
                        break
                    await asyncio.sleep(.01)
                assert not pending_segment.exists()
                assert store.cache_cleanup_status(scope.owner)['state'] == 'complete'
            finally:
                pump.stop()
                assert await pump.wait(1)
            report['checks'].update(managed_idle_cleanup=True)
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
