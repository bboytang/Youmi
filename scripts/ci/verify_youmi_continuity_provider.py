"""Bounded real dialogue and real-date observation evidence, using only CI test data."""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import httpx
import numpy as np

sys.path.insert(0, str(Path(os.environ['MIA_CONTINUITY_UPSTREAM']) / 'main/xiaozhi-server'))
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
from youmi_companion import emotions, reply
from youmi_companion.authorization import AuthenticatedScope
from youmi_companion.context import ContextBuilder, ContextError
from youmi_companion.embedding import DIMENSION, MODEL_KEY, REVISION
from youmi_companion.extraction import LLMMemoryExtractor
from youmi_companion.jobs import JobStore, MemoryWorker, TurnCompleted
from youmi_companion.memory import MemoryStore
from youmi_companion.persona import PersonaGuardrail
from youmi_companion.semantic import SemanticMemoryIndex


class UnitEncoder:
    model_id, revision, dimension = MODEL_KEY, REVISION, DIMENSION
    def encode(self, text, query=False):
        result = np.zeros(DIMENSION, dtype=np.float32)
        result[0] = 1
        return result


class Policy:
    def __init__(self):
        self.scope = AuthenticatedScope('user:1:agent:continuity-ci', 1, 1, 'continuity-ci', 'mia', 'test-device', 'test-client',
            0, 1, datetime.now(timezone.utc) + timedelta(days=1), frozenset({'memory:read', 'memory:write', 'memory:manage'}))
    async def require(self, scope, permission):
        assert scope == self.scope and permission in scope.permissions
    async def restore(self, snapshot):
        assert snapshot == self.scope.snapshot()
        return self.scope


def provider(report, output=768):
    from urllib.parse import urlsplit
    secret, base, model = (os.environ['MIA_TEST_LLM_' + key] for key in ('API_KEY', 'BASE_URL', 'MODEL'))
    url = urlsplit(base)
    assert secret and url.scheme == 'https' and url.hostname and not (url.username or url.password or url.query or url.fragment)
    mode = os.environ.get('MIA_CONTINUITY_MODE')
    assert report['requests'] < {'daily': 14, 'audit': 4}.get(mode, 13)
    report['requests'] += 1
    result = LLMProvider({'api_key': secret, 'base_url': base, 'model_name': model, 'max_tokens': output,
                          'temperature': 0.1, 'timeout': 30})
    original = result.client
    result.client = original.with_options(max_retries=0, http_client=httpx.Client(timeout=30, follow_redirects=False))
    original.close()
    return result


async def extract(store, policy, text, marker, report):
    outputs = []
    def factory():
        result = provider(report)
        original = result.response_no_stream
        def capture(system, user):
            output = original(system, user)
            outputs.append(output)
            return output
        result.response_no_stream = capture
        return result
    async def resolve(scope):
        return LLMMemoryExtractor(factory)
    jobs = JobStore(store)
    worker = MemoryWorker(jobs, policy, resolve, encoder=UnitEncoder())
    task = await worker.submit(TurnCompleted(policy.scope, 'session-' + marker, marker, marker, text,
        store.snapshot(policy.scope.owner)['revision']))
    await worker.run_once()
    row = jobs.current(task)
    report['cases'].append({'phase': marker, 'source': text, 'model_output': outputs[-1] if outputs else None,
        'state': row['state'], 'error_category': row['error_category'], 'server_observed_at': row['created_at']})
    assert row['state'] in {'committed', 'empty'}, row['error_category']


async def converse(store, policy, marker, query, report):
    llm = provider(report, 256)
    profile = Path(os.environ['MIA_CONTINUITY_DIR']) / 'profile.json'
    profile.write_text(json.dumps({'provider_module': type(llm).__module__, 'base_url': llm.base_url,
        'model': llm.model_name, 'context_window': 8192,
        'capacity_source': 'Isolated bounded evaluation envelope, short accepted real requests; not production model capacity evidence'}))
    os.environ['MIA_CONTEXT_PROFILE_FILE'] = str(profile)
    message = SimpleNamespace(role='user', content=query, uniq_id=marker, is_temporary=False, tool_calls=None, tool_call_id=None)
    conn = SimpleNamespace(read_config_from_api=True, _youmi_scope=policy.scope, _youmi_authorization=policy,
        _youmi_context_builder=ContextBuilder(SemanticMemoryIndex(store, UnitEncoder())), llm=llm,
        sentence_id=marker, session_id='session-' + marker, dialogue=SimpleNamespace(dialogue=[message]),
        loop=asyncio.get_running_loop(), client_abort=False)
    try:
        chunks = await asyncio.to_thread(lambda: list(reply.response(conn, None, 0)))
        text = ''.join(chunks)
        turn = conn._youmi_context_turn
        case = {'phase': marker, 'source': query, 'model_output': text,
            'events_before': turn.bundle.diagnostics()['events'], 'events_activated': [],
            'trend_status': turn.bundle.diagnostics()['trends'], 'selected_fact_ids': turn.bundle.diagnostics()['selected_fact_ids'],
            'estimated_input': turn.bundle.diagnostics()['estimated_input'],
            'expression_policy': 'Luyao reference; no additional expression-style gate'}
        report['cases'].append(case)
        assert text.strip() and not PersonaGuardrail().inspect_output(text)[1]
        case['events_activated'] = await turn.builder.complete(turn.bundle, policy, text)
        return text, turn.bundle
    finally:
        llm.client.close()


async def stage(name, report):
    root = Path(os.environ['MIA_CONTINUITY_DIR'])
    path = root / 'state.sqlite3'
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest() if name == 'audit' else None
    if name == 'audit':
        # Never migrate or mutate the original calendar collection.
        audit_path = root / 'audit.sqlite3'
        assert not audit_path.exists(), 'Audit must start from a fresh collection copy'
        source = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        destination = sqlite3.connect(audit_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        path = audit_path
    store = MemoryStore(path)
    policy = Policy()
    owner = policy.scope.owner
    try:
        if name == 'audit':
            baseline = store.handle(owner, {'action': 'emotion_status'})
            report['audit_baseline'] = baseline
            report['audit_gate'] = 'insufficient_real_calendar_evidence'
            assert baseline['trend']['status'] == 'available', 'Real calendar collection is not sufficient yet'
            report['audit_gate'] = 'ready'
            _, original = await converse(store, policy, 'audit-baseline', '结合长期记录，最近我的心情有什么规律？', report)
            assert original.diagnostics()['trends'] == 'available'
            rows = store.handle(owner, {'action': 'observations'})['observations']
            oldest = min(rows, key=lambda row: (row['observed_at'], row['id']))
            corrected = 'neutral' if oldest['tendency'] != 'neutral' else 'positive'
            changed = store.handle(owner, {'action': 'observation_update', 'id': oldest['id'], 'tendency': corrected})
            assert changed['trend']['groups'] != baseline['trend']['groups']
            checker = ContextBuilder(SemanticMemoryIndex(store, UnitEncoder()))
            try:
                await checker.validate(original, policy)
            except ContextError:
                report['old_context_rejected_after_correction'] = True
            else:
                raise AssertionError('Corrected observation left the old context valid')
            report['corrected_trend'] = changed['trend']
            _, updated = await converse(store, policy, 'audit-corrected', '我修正了一条心情观察。按修正后的记录，最近心情有什么规律？', report)
            assert updated.diagnostics()['trends'] == 'available'
            # Retain one observation so an old aggregate cannot conceal deletion.
            for row in rows[1:]:
                store.handle(owner, {'action': 'observation_delete', 'id': row['id']})
            report['deleted_trend'] = store.handle(owner, {'action': 'emotion_status'})['trend']
            assert report['deleted_trend']['status'] == 'insufficient_observations'
            _, deleted = await converse(store, policy, 'audit-deleted', '删掉这些旧记录后，你还能判断我长期的心情趋势吗？', report)
            assert deleted.diagnostics()['trends'] == 'insufficient_observations'
            disabled = store.handle(owner, {'action': 'emotion_consent', 'enabled': False})
            assert disabled['trend'] == {'status': 'disabled'}
            retained = store.handle(owner, {'action': 'observations'})
            assert len(retained['observations']) == 1 and retained['trend'] == {'status': 'disabled'}
            _, stopped = await converse(store, policy, 'audit-disabled', '我已停用情绪记录，现在你会怎么看待我的心情？', report)
            assert stopped.diagnostics()['trends'] == 'disabled'
            report['disabled_retains_raw_only'] = True
            report['longitudinal_change_checks_passed'] = True
        elif name == 'seed':
            await extract(store, policy, '我喜欢蓝色', name, report)
            assert any(row['confirmed'] and '蓝色' in row['content'] for row in store.list_memories(owner))
        elif name in {'continuity', 'repeat', 'reflection', 'boundary', 'deleted', 'insufficient', 'casual', 'quiet', 'choice'}:
            query = {'continuity': '你还记得我喜欢蓝色吗', 'repeat': '你还记得我喜欢蓝色吗，不用重新介绍',
                'reflection': '你怎么看我喜欢蓝色这件事', 'boundary': '请不要叫我宝贝',
                'deleted': '你还记得我以前喜欢哪种颜色吗', 'insufficient': '你能从长期记录判断我最近的心情趋势吗',
                'casual': '刚忙完，脑子有点乱，不想听大道理。', 'quiet': '嗯，今天真不想说话。',
                'choice': '小说和散文，你更愿意聊哪个？'}[name]
            if name == 'deleted':
                for row in store.list_memories(owner):
                    store.delete_memory(owner, row['id'])
            text, bundle = await converse(store, policy, name, query, report)
            case = report['cases'][-1]
            if name in {'continuity', 'reflection', 'boundary'}:
                assert case['events_activated'], 'Actual completed response did not confirm candidate'
            elif name == 'repeat':
                assert not case['events_activated'] and any(item['state'] == 'active' for item in case['events_before'])
                assert store.db.execute("SELECT COUNT(*) FROM mia_events WHERE event_type='continuity'").fetchone()[0] == 1
            elif name == 'deleted':
                assert not case['selected_fact_ids'] and not any(item['state'] == 'active' and item['type'] != 'boundary'
                    for item in json.loads(bundle.inputs_json)['events'])
            elif name == 'insufficient':
                assert case['trend_status'] == 'insufficient_observations'
        elif name in {'default-off', 'enabled-mood', 'quoted-mood', 'daily'}:
            if name == 'enabled-mood':
                store.handle(owner, {'action': 'emotion_consent', 'enabled': True})
            if name == 'daily':
                if not emotions.consent(store, owner)['enabled']:
                    store.handle(owner, {'action': 'emotion_consent', 'enabled': True})
                day = int(store.clock()) // 86400
                rows = store.db.execute('SELECT DISTINCT observed_at/86400 FROM mia_observations WHERE owner=?', (owner,)).fetchall()
                assert all(row[0] != day for row in rows), 'One real-date observation run per UTC day'
                assert len(rows) < 7, 'Seven-day collection budget already reached'
                name = 'daily-' + str(day)
            text = '这只是小说台词：“我今天很难过”，不是我的真实心情' if name == 'quoted-mood' else '我今天很难过'
            before = store.db.execute('SELECT COUNT(*) FROM mia_observations WHERE owner=?', (owner,)).fetchone()[0]
            await extract(store, policy, text, name, report)
            after = store.db.execute('SELECT COUNT(*) FROM mia_observations WHERE owner=?', (owner,)).fetchone()[0]
            assert after == before + int(name == 'enabled-mood' or name.startswith('daily-'))
            report['emotion_status'] = store.handle(owner, {'action': 'emotion_status'})
            if name.startswith('daily-'):
                await converse(store, policy, name + '-reply', '我今天很难过，你能结合长期记录看看我的心情趋势吗', report)
        else:
            raise ValueError('Unknown verification phase')
    finally:
        store.close()
        if source_hash:
            report['original_collection_unchanged'] = hashlib.sha256((root / 'state.sqlite3').read_bytes()).hexdigest() == source_hash
            assert report['original_collection_unchanged'], 'Original calendar evidence changed'


def main():
    root = Path(os.environ['MIA_CONTINUITY_DIR'])
    root.mkdir(parents=True, exist_ok=True)
    target = root / 'provider-results.json'
    report = json.loads(target.read_text()) if target.exists() else {'code_sha': os.environ['GITHUB_SHA'],
        'requests': 0, 'cases': [], 'passed': False, 'actual_clock': True,
        'fixtures': 'Synthetic test user, policy and unit vectors; installed reply bridge and real provider. Actual BGE/manager separate.',
        'production_context_limits': 'unverified; isolated 8192-byte conservative test envelope only',
        'full_B05_B07_B08': 'not_accepted'}
    name = os.environ.get('MIA_CONTINUITY_STAGE')
    mode = os.environ.get('MIA_CONTINUITY_MODE')
    if not name and mode == 'audit':
        assert report.get('mode') == 'daily' and report.get('passed'), 'Audit requires a verified daily artifact'
        (root / 'daily-source-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        report = {'code_sha': os.environ['GITHUB_SHA'], 'mode': 'audit', 'requests': 0, 'cases': [], 'passed': False,
            'source_collection_run': report['collection_run_id'], 'actual_clock': True,
            'real_longitudinal_complete': False, 'raw_response_review': 'required even if automated checks pass'}
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if name:
        try:
            asyncio.run(stage(name, report))
            report['passed'] = True
        except Exception as error:
            report.update(passed=False, error_class=type(error).__name__, failed_phase=name)
        finally:
            secret = os.environ['MIA_TEST_LLM_API_KEY']
            encoded = json.dumps(report, ensure_ascii=False, indent=2)
            target.write_text((encoded.replace(secret, '[REDACTED]') if secret else encoded) + '\n')
        if not report['passed']:
            raise SystemExit('Bounded continuity phase failed; inspect sanitized artifact')
        return
    phases = [mode] if mode in {'daily', 'audit'} else [
        'seed', 'continuity', 'repeat', 'reflection', 'boundary', 'deleted', 'default-off', 'enabled-mood', 'insufficient', 'quoted-mood',
        'casual', 'quiet', 'choice']
    for phase in phases:
        env = dict(os.environ, MIA_CONTINUITY_STAGE=phase)
        # Each phase starts a NEW Python process, restoring only the persisted DB.
        result = subprocess.run([sys.executable, __file__], env=env, timeout=150 if phase == 'audit' else 45, capture_output=True)
        if result.returncode:
            raise SystemExit('Bounded continuity verification stopped; inspect sanitized artifact')
    if phases == ['daily']:
        report = json.loads(target.read_text())
        report.update(collection_run_id=os.environ['GITHUB_RUN_ID'], completed_at=datetime.now(timezone.utc).isoformat(),
            mode='daily', code_sha=os.environ['GITHUB_SHA'], previous_run=os.environ.get('MIA_CONTINUITY_PREVIOUS_RUN') or None,
            state_sha256=hashlib.sha256((root / 'state.sqlite3').read_bytes()).hexdigest(),
            real_calendar_threshold_met=report['emotion_status']['trend']['status'] == 'available',
            real_longitudinal_complete=False)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'phases': len(phases), 'passed': True, 'clock': 'real', 'process_restart_between_phases': True}))


if __name__ == '__main__':
    main()
