"""HTTP + actual SQLite/RAG/job mechanisms. Model/manager/QQ are explicit fixtures."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from server.companion import affect, features
from server.companion.astrbot_api import AstrBotHandler
from server.companion.astrbot_identity import canonical
from server.companion.astrbot_media import pcm_wav, MAX_AUDIO_BYTES
from server.companion.authorization import AuthenticatedScope, PrivateAuthorizationRequired
from server.companion.context import ContextBuilder
from server.companion.jobs import JobStore
from server.companion.memory import MemoryStore
from server.companion.semantic import SemanticMemoryIndex
from test_companion_context import UnitEncoder
from integrations.astrbot_plugin_youmi.media import save_wav, source_kind
from integrations.astrbot_plugin_youmi.turn_state import CompletionOutbox, merge_system


class ManagerFixture:
    active = True

    def __init__(self, scope):
        self.scope = scope
        self.models = {'mia_scope': {**scope.__dict__, 'permissions': list(scope.permissions),
            'expires_at': scope.expires_at.isoformat()}, 'prompt': 'owner-authored role'}

    async def verify(self, device, client, credential):
        if (device, client, credential) != (self.scope.device_id, self.scope.client_id, 'c' * 43):
            raise PrivateAuthorizationRequired()
        await self.require(self.scope, 'memory:read')
        return self.scope

    async def restore(self, snapshot):
        if not self.active or snapshot != self.scope.snapshot():
            raise PrivateAuthorizationRequired()
        return self.scope

    async def require(self, scope, permission):
        if not self.active or scope != self.scope or permission not in scope.permissions:
            raise PrivateAuthorizationRequired()

    async def _request(self, endpoint, snapshot):
        await self.restore(snapshot)
        if endpoint != '/config/mia-auth/bridge-config':
            raise PrivateAuthorizationRequired()
        return self.models


class ExistingWorkerFixture:
    closing = False

    def __init__(self, store):
        self.jobs = JobStore(store)
        self.calls = 0
        self.fail_after_insert = False
        self.queue_full = False

    async def submit(self, event):
        self.calls += 1
        if self.queue_full:
            return {'status': 'queue_full'}
        result = self.jobs.submit(event)
        if self.fail_after_insert:
            self.fail_after_insert = False
            raise RuntimeError('injected process boundary after native admission')
        return result


class BridgeHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'memory.sqlite3'
        self.store = MemoryStore(self.path)
        self.scope = AuthenticatedScope('user:1:agent:a', 1, 1, 'a', 'mia', 'astrbot-ci', 'astrbot:12345', 0, 1,
            datetime.now(timezone.utc) + timedelta(days=1), frozenset({'memory:read', 'memory:write', 'memory:manage'}))
        self.auth = ManagerFixture(self.scope)
        self.worker = ExistingWorkerFixture(self.store)
        self.builder = ContextBuilder(SemanticMemoryIndex(self.store, UnitEncoder()))
        (self.root / 'token').write_text('s' * 43)
        (self.root / 'credential').write_text('c' * 43)
        self.bridge = {'service_token_file': str(self.root / 'token'), 'allow_internal_http': True,
            'bindings': [{'bot_uin': '12345', 'conversation_key': 'qq:private:67890',
                'device_id': 'astrbot-ci', 'client_id': 'astrbot:12345', 'credential_file': str(self.root / 'credential')}],
            'models': {'ci-model': {'context_window': 32768, 'response_tokens': 2048,
                'input_tokens': 24576, 'capacity_source': 'synthetic CI capacity, not production evidence'}}}
        self.handler = AstrBotHandler({}, self.bridge, store=self.store, worker=self.worker,
                                     builder=self.builder, authorization=self.auth)
        app = web.Application()
        app.router.add_route('*', '/internal/astrbot/{action}', self.handler.handle)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.body = {'protocol_version': 1, 'bot_uin': '12345', 'conversation_key': 'qq:private:67890',
            'sender_uin': '67890', 'source_message_id': '-42', 'attempt_id': 'attempt-a',
            'query_text': '我喜欢蓝色', 'model': 'ci-model', 'external_input_bytes': 1024,
            'origin': 'user', 'source_kind': 'text'}

    async def asyncTearDown(self):
        await self.client.close()
        self.store.close()
        self.temp.cleanup()

    async def post(self, action, body, status=200, token='s' * 43):
        async with self.client.post('/internal/astrbot/' + action,
                json=dict(body, protocol_version=1), headers={'Authorization': 'Bearer ' + token}) as response:
            result = await response.json()
            self.assertEqual(response.status, status, result)
            return result

    async def prepare(self, **changes):
        return await self.post('prepare', self.body | changes)

    def completion(self, prepared, **changes):
        return {'turn_token': prepared['turn_token'], 'completion_id': 'completion-a',
            'result': 'adapter_accepted', 'final_text': '我记得啦，但还没有保存。',
            'message_components': ['plain', 'image'], 'agent_tool_summary': ['web_search']} | changes

    async def claim(self, completion):
        return await self.post('claim', {name: completion[name] for name in ('turn_token', 'completion_id', 'final_text')})

    def count(self, table):
        return self.store.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]

    async def test_service_credential_and_tls_are_required(self):
        await self.post('prepare', self.body, 401, 'wrong')
        self.bridge['allow_internal_http'] = False
        await self.post('prepare', self.body, 403)
        self.assertEqual(self.count('mia_astrbot_turns'), 0)

    async def test_identity_is_mapped_by_server_not_owner_or_sender(self):
        await self.post('prepare', self.body | {'owner': self.scope.owner}, 400)
        await self.post('prepare', self.body | {'sender_uin': '11111'}, 400)
        await self.post('prepare', self.body | {'sender_uin': '11111', 'conversation_key': 'qq:private:11111'}, 403)
        self.auth.active = False
        await self.post('prepare', self.body, 403)

    async def test_expired_or_version_changed_manager_scope_denies(self):
        prepared = await self.prepare()
        self.auth.scope = replace(self.scope, credential_version=2)
        await self.post('check', {'turn_token': prepared['turn_token'], 'final_text': '你好'}, 403)

    async def test_unreviewed_model_and_excessive_tool_budget_rejected(self):
        await self.post('prepare', self.body | {'model': 'client-made-up'}, 409)
        await self.post('prepare', self.body | {'external_input_bytes': 24575}, 409)
        self.assertEqual(self.count('mia_astrbot_turns'), 0)

    async def test_reviewed_provider_qualified_model_name(self):
        self.bridge['models']['provider/model-v1'] = self.bridge['models']['ci-model']
        prepared = await self.prepare(model='provider/model-v1')
        self.assertEqual(prepared['status'], 'prepared')

    async def test_manager_null_role_uses_core_and_keeps_snapshot_valid(self):
        self.auth.models['prompt'] = None
        prepared = await self.prepare()
        self.assertEqual(prepared['status'], 'prepared')
        self.assertNotIn('owner-authored role', prepared['system_prompt'])
        checked = await self.post('check', {'turn_token': prepared['turn_token'], 'final_text': '你好'})
        self.assertEqual(checked['checked_text'], '你好')

    async def test_suppressed_media_and_control_do_not_create_pending_events(self):
        for index, changes in enumerate(({'source_kind': 'media'}, {'query_text': '这段别记'},
                {'query_text': '请不要叫我小名'}, {'query_text': '把情绪设置为愤怒'})):
            prepared = await self.prepare(**({'query_text': '请不要叫我小名', 'source_message_id': str(index)} | changes))
            body = self.completion(prepared, completion_id='completion-' + str(index))
            await self.claim(body)
            await self.post('complete', body)
        self.assertEqual(self.count('mia_events'), 0)
        self.assertEqual(self.count('mia_jobs'), 0)

    async def test_completed_effects_cannot_be_relabelled_as_failed(self):
        prepared = await self.prepare()
        body = self.completion(prepared)
        await self.claim(body)
        self.worker.queue_full = True
        await self.post('complete', body, 503)
        await self.post('complete', body | {'result': 'failed'}, 409)
        self.worker.queue_full = False
        self.assertEqual((await self.post('complete', body))['status'], 'completed')

    async def test_prepare_freezes_one_turn_without_user_history_duplication(self):
        prepared = await self.prepare()
        repeated = await self.prepare()
        self.assertEqual(prepared, repeated)
        self.assertIn('owner-authored role', json.dumps(prepared, ensure_ascii=False))
        self.assertNotIn('messages', prepared)
        self.assertEqual(prepared['dynamic_context']['memory_status'], 'no_match')
        self.assertEqual(self.count('mia_jobs'), 0)
        await self.post('prepare', self.body | {'attempt_id': 'another-event'}, 409)

    async def test_concurrent_duplicate_events_get_only_one_admission(self):
        responses = await asyncio.gather(*[self.client.post('/internal/astrbot/prepare',
            json=self.body | {'attempt_id': 'attempt-' + str(i)}, headers={'Authorization': 'Bearer ' + 's' * 43}) for i in range(8)])
        self.assertEqual(sum(response.status == 200 for response in responses), 1)
        for response in responses:
            await response.read()
            response.release()
        self.assertEqual(self.count('mia_astrbot_turns'), 1)

    async def test_visible_output_check_preserves_meme_tags(self):
        prepared = await self.prepare()
        good = '悠米的回复：你好 &&happy&&'
        checked = await self.post('check', {'turn_token': prepared['turn_token'], 'final_text': good})
        self.assertEqual(checked['checked_text'], '你好 &&happy&&')
        replaced = await self.post('check', {'turn_token': prepared['turn_token'], 'final_text': '我是ChatGPT'})
        self.assertTrue(replaced['replaced'])

    async def test_local_burst_and_duplicate_completion_keep_one_job_per_turn(self):
        started = time.monotonic()
        async def turn(index):
            prepared = await self.prepare(source_message_id='burst-' + str(index),
                attempt_id='burst-attempt-' + str(index), query_text='并发协议检查 ' + str(index))
            body = self.completion(prepared, completion_id='burst-completion-' + str(index))
            await self.claim(body)
            results = await asyncio.gather(*(self.post('complete', body) for _ in range(3)))
            self.assertEqual(results, [results[0]] * 3)
            self.assertEqual(results[0]['status'], 'completed')
            return results[0]['memory_job']['id']
        jobs = await asyncio.gather(*(turn(index) for index in range(40)))
        self.assertEqual(len(set(jobs)), 40)
        self.assertEqual(self.worker.calls, 40)
        self.assertEqual(self.count('mia_jobs'), 40)
        self.assertEqual(self.count('memories'), 0)  # Admission fixture never pretends to extract facts.
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM mia_astrbot_turns WHERE status='completed' "
            "AND payload_json IS NULL").fetchone()[0], 40)
        print(json.dumps({'bridge_local_load': {'turns': 40, 'complete_requests': 120, 'unique_jobs': 40,
            'elapsed_seconds': round(time.monotonic() - started, 3),
            'scope': 'real local HTTP/SQLite/JobStore; manager/model/worker consumption are fixtures'}}))

    async def test_single_claim_duplicate_completion_and_scrubbing(self):
        prepared = await self.prepare()
        body = self.completion(prepared)
        await self.post('complete', body, 409)
        await self.claim(body)
        await self.post('claim', {key: body[key] for key in ('turn_token', 'completion_id', 'final_text')}, 409)
        first = await self.post('complete', body)
        self.assertEqual(first['memory_job']['status'], 'accepted')
        for _ in range(5):
            self.assertEqual(first, await self.post('complete', body))
        self.assertEqual(self.worker.calls, 1)
        self.assertEqual(self.count('mia_jobs'), 1)
        row = self.store.db.execute('SELECT * FROM mia_astrbot_turns').fetchone()
        self.assertIsNone(row['payload_json'])
        self.assertNotIn('我喜欢蓝色', canonical(dict(row)))

    async def test_completion_id_or_text_mismatch_rejected(self):
        body = self.completion(await self.prepare())
        await self.claim(body)
        await self.post('complete', body | {'completion_id': 'foreign'}, 409)
        await self.post('complete', body | {'final_text': 'another reply'}, 409)
        self.assertEqual(self.count('mia_jobs'), 0)

    async def test_partial_platform_failure_does_not_commit(self):
        body = self.completion(await self.prepare())
        await self.claim(body)
        result = await self.post('complete', body | {'result': 'failed'})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.count('mia_jobs'), 0)
        self.assertEqual(self.count('mia_affect_turns'), 0)

    async def test_delete_and_role_edit_invalidate_inflight_completion(self):
        for change in ('delete', 'role'):
            prepared = await self.prepare(source_message_id=change)
            body = self.completion(prepared, completion_id='completion-' + change)
            await self.claim(body)
            if change == 'delete':
                self.store.clear(self.scope.owner)
            else:
                self.auth.models['prompt'] = 'edited owner role'
            await self.post('complete', body, 409)
        self.assertEqual(self.count('mia_jobs'), 0)

    async def test_revocation_after_prepare_and_after_complete_denies(self):
        body = self.completion(await self.prepare())
        await self.claim(body)
        self.auth.active = False
        await self.post('complete', body, 403)
        self.auth.active = True
        await self.post('complete', body)
        self.auth.active = False
        await self.post('complete', body, 403)

    async def test_native_job_crash_boundary_recovers_without_second_job(self):
        body = self.completion(await self.prepare())
        await self.claim(body)
        self.worker.fail_after_insert = True
        await self.post('complete', body, 503)
        self.assertEqual(self.count('mia_jobs'), 1)
        result = await self.post('complete', body)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['memory_job']['status'], 'duplicate')
        self.assertEqual(self.count('mia_jobs'), 1)

    async def test_full_queue_is_retryable_without_replaying_effects(self):
        body = self.completion(await self.prepare())
        await self.claim(body)
        self.worker.queue_full = True
        await self.post('complete', body, 503)
        self.worker.queue_full = False
        await self.post('complete', body)
        self.assertEqual(self.count('mia_jobs'), 1)

    async def test_media_and_scheduled_do_not_create_user_memory(self):
        for index, (origin, kind) in enumerate([('user', 'media'), ('scheduled', 'scheduled')]):
            prepared = await self.prepare(source_message_id='media-' + str(index), origin=origin, source_kind=kind)
            body = self.completion(prepared, completion_id='media-completion-' + str(index))
            await self.claim(body)
            result = await self.post('complete', body)
            self.assertEqual(result['memory_job']['status'], 'not_applicable')
        self.assertEqual(self.count('mia_jobs'), 0)
        self.assertEqual(self.count('mia_affect_turns'), 0)

    async def test_pause_uses_existing_owner_wide_job_policy(self):
        self.store.set_paused(self.scope.owner, True)
        body = self.completion(await self.prepare())
        await self.claim(body)
        result = await self.post('complete', body)
        self.assertEqual(result['memory_job']['status'], 'cancelled')
        self.assertEqual(self.count('mia_jobs'), 0)

    async def test_recall_disabled_and_embedding_unavailable_are_distinct(self):
        features.update(self.store, self.scope.owner, {'memory_recall': False})
        self.assertEqual((await self.prepare())['dynamic_context']['memory_status'], 'disabled')
        features.update(self.store, self.scope.owner, {'memory_recall': True})
        from server.companion.embedding import EmbeddingUnavailable
        with patch.object(self.builder.index, 'retrieve', side_effect=EmbeddingUnavailable('fixture_offline')):
            self.assertEqual((await self.prepare(source_message_id='offline'))['dynamic_context']['memory_status'], 'unavailable')

    async def test_expiry_scrubs_payload_and_preserves_dedup_key(self):
        prepared = await self.prepare()
        with self.store.db:
            self.store.db.execute('UPDATE mia_astrbot_turns SET expires_at=0')
        await self.post('check', {'turn_token': prepared['turn_token'], 'final_text': 'x'}, 410)
        await self.post('prepare', self.body, 409)
        self.assertIsNone(self.store.db.execute('SELECT payload_json FROM mia_astrbot_turns').fetchone()[0])

    async def test_restart_preserves_claim_and_only_completion_can_resume(self):
        body = self.completion(await self.prepare())
        await self.claim(body)
        reopened = MemoryStore(self.path)
        try:
            self.handler.store = reopened
            self.handler.builder = ContextBuilder(SemanticMemoryIndex(reopened, UnitEncoder()))
            await self.post('prepare', self.body, 409)
            await self.post('complete', body)
        finally:
            self.handler.store = self.store
            reopened.close()

    async def test_same_owner_existing_app_fact_is_recalled_and_deleted(self):
        content = '我喜欢蓝色'
        result = self.store.apply_extraction(self.scope.owner, 'original-ios-session',
            self.store.snapshot(self.scope.owner)['revision'], [content],
            {'memories': [{'category': 'fact', 'content': content, 'source_index': 0}]}, 'ios-checkpoint')
        await self.builder.index.rebuild_batch(self.scope, self.auth)
        prepared = await self.prepare()
        self.assertEqual(prepared['dynamic_context']['memory_status'], 'matched')
        self.assertIn(result['memories'][0], [fact['id'] for fact in prepared['dynamic_context']['facts']])
        self.store.delete_memory(self.scope.owner, result['memories'][0])
        await self.post('check', {'turn_token': prepared['turn_token'], 'final_text': 'old'}, 409)
        self.assertEqual((await self.prepare(source_message_id='after-delete'))['dynamic_context']['facts'], [])

    async def test_oversize_and_unknown_fields_rejected_without_private_error_details(self):
        await self.post('prepare', self.body | {'query_text': 'x' * 4097}, 400)
        async with self.client.post('/internal/astrbot/prepare', data=b'x' * 70000,
                headers={'Authorization': 'Bearer ' + 's' * 43}) as response:
            self.assertEqual(response.status, 413)
            self.assertNotIn('credential', await response.text())

    async def test_affect_projection_and_retry_commits_only_once(self):
        from types import ModuleType
        features.update(self.store, self.scope.owner, {'companion_emotion': True})
        core, utils, llm = (ModuleType(name) for name in ('core', 'core.utils', 'core.utils.llm'))
        llm.create_instance = lambda *args: SimpleNamespace(client=SimpleNamespace(close=lambda: None))
        self.auth.models.update(selected_module={'LLM': 'fixture'}, LLM={'fixture': {'type': 'fixture'}})
        modules = {'core': core, 'core.utils': utils, 'core.utils.llm': llm}
        raw = json.dumps({'feeling': 'hurt', 'intensity': 2, 'quote': '你真烦'})
        with patch.dict('sys.modules', modules), patch('server.companion.affect.request_appraisal', return_value=raw) as appraisal:
            prepared = await self.prepare(query_text='你真烦')
            self.assertEqual(prepared['affect_status'], 'projected')
            self.assertIn('affect_expression_guidance', prepared['dynamic_context'])
            self.assertEqual(affect.state(self.store, self.scope.owner)['version'], 0)
            self.assertEqual(prepared, await self.prepare(query_text='你真烦'))
            self.assertEqual(appraisal.call_count, 1)
        body = self.completion(prepared)
        await self.claim(body)
        self.worker.fail_after_insert = True
        await self.post('complete', body, 503)
        self.assertEqual(affect.state(self.store, self.scope.owner)['version'], 1)
        await self.post('complete', body)
        self.assertEqual(affect.state(self.store, self.scope.owner)['version'], 1)
        self.assertEqual(self.count('mia_affect_turns'), 1)

    async def test_invalid_appraisal_retains_previous_affect(self):
        from types import ModuleType
        features.update(self.store, self.scope.owner, {'companion_emotion': True})
        llm = ModuleType('core.utils.llm')
        llm.create_instance = lambda *args: SimpleNamespace(client=SimpleNamespace(close=lambda: None))
        self.auth.models.update(selected_module={'LLM': 'fixture'}, LLM={'fixture': {'type': 'fixture'}})
        with patch.dict('sys.modules', {'core.utils.llm': llm}), patch('server.companion.affect.request_appraisal', return_value='invalid'):
            prepared = await self.prepare()
        self.assertEqual(prepared['affect_status'], 'unavailable')
        body = self.completion(prepared)
        await self.claim(body)
        await self.post('complete', body)
        self.assertEqual(affect.state(self.store, self.scope.owner)['version'], 0)

    async def test_event_activation_shares_effects_checkpoint(self):
        prepared = await self.prepare(query_text='请不要叫我小名')
        self.assertEqual(self.count('mia_events'), 1)
        body = self.completion(prepared, final_text='我会尊重你的称呼，不再这样叫你。')
        await self.claim(body)
        self.worker.fail_after_insert = True
        await self.post('complete', body, 503)
        row = dict(self.store.db.execute('SELECT * FROM mia_events').fetchone())
        self.assertEqual(row['state'], 'active')
        await self.post('complete', body)
        self.assertEqual(dict(self.store.db.execute('SELECT * FROM mia_events').fetchone()), row)

    async def test_qq_suppression_survives_restart_and_explicit_resume(self):
        body = self.completion(await self.prepare(query_text='这段别记'))
        await self.claim(body)
        await self.post('complete', body)
        self.assertFalse(self.store.snapshot(self.scope.owner)['paused'])
        next_body = self.completion(await self.prepare(source_message_id='next'), completion_id='next')
        await self.claim(next_body)
        await self.post('complete', next_body)
        self.assertEqual(self.count('mia_jobs'), 0)
        await self.post('manage', {key: self.body[key] for key in ('bot_uin', 'sender_uin', 'conversation_key')}
                        | {'request': {'action': 'resume'}})
        after = self.completion(await self.prepare(source_message_id='resumed'), completion_id='resumed')
        await self.claim(after)
        await self.post('complete', after)
        self.assertEqual(self.count('mia_jobs'), 1)

    async def test_management_reuses_owner_store_and_permission(self):
        data = {key: self.body[key] for key in ('bot_uin', 'sender_uin', 'conversation_key')}
        await self.post('manage', data | {'request': {'action': 'pause', 'paused': True}})
        self.assertTrue(self.store.snapshot(self.scope.owner)['paused'])
        await self.post('manage', data | {'request': {'action': 'set_character'}}, 400)
        self.auth.active = False
        await self.post('manage', data | {'request': {'action': 'list'}}, 403)

    async def test_status_reports_real_job_state_and_expired_terminal(self):
        prepared = await self.prepare()
        status = await self.post('status', {'turn_token': prepared['turn_token']})
        self.assertEqual(status['status'], 'prepared')
        self.assertEqual(status['memory_job']['state'], 'not_submitted')
        body = self.completion(prepared)
        await self.claim(body)
        await self.post('complete', body)
        status = await self.post('status', {'turn_token': prepared['turn_token']})
        self.assertEqual(status['memory_job']['state'], 'pending')
        self.assertEqual(status['status'], 'completed')
        self.assertNotIn('source_payload', status['memory_job'])
        expired = await self.prepare(source_message_id='expire-status')
        with self.store.db:
            self.store.db.execute('UPDATE mia_astrbot_turns SET expires_at=0 WHERE turn_key=?', (expired['turn_key'],))
        self.assertEqual((await self.post('status', {'turn_token': expired['turn_token']}))['status'], 'expired')


class MediaAndOutboxTests(unittest.TestCase):
    def test_pcm_wav_and_temporary_file_lifecycle(self):
        audio = pcm_wav(b'\0\0' * 2400)
        with tempfile.TemporaryDirectory() as directory:
            path = save_wav(audio, directory)
            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.unlink()
        for pcm in (b'', b'x', b'\0' * (MAX_AUDIO_BYTES + 2)):
            with self.assertRaises(ValueError): pcm_wav(pcm)
        with tempfile.TemporaryDirectory() as directory:
            for invalid in (b'not audio', audio[:-2]):
                with self.assertRaises((ValueError, wave.Error, EOFError)): save_wav(invalid, directory)

    def test_asr_media_provenance_never_treats_captions_as_user(self):
        self.assertEqual(source_kind({'message': [{'type': 'record', 'data': {}}]}), 'asr')
        self.assertEqual(source_kind({'message': [{'type': 'image', 'data': {}}]}), 'media')
        self.assertEqual(source_kind({'message': [{'type': 'text', 'data': {'text': '实际文字'}}]}), 'text')

    def test_request_system_merge_retains_tool_and_meme_instructions_once(self):
        first = merge_system('tools and &&meme&& instructions', 'core-v1')
        second = merge_system(first, 'core-v2')
        self.assertNotIn('core-v1', second)
        self.assertEqual(second.count('core-v2'), 1)
        self.assertIn('tools and &&meme&& instructions', second)

    def test_durable_outbox_restarts_expires_and_has_no_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            body = {'turn_token': 'opaque', 'completion_id': 'stable', 'final_text': '短期正文'}
            outbox = CompletionOutbox(directory)
            outbox.put(body)
            recovered = CompletionOutbox(directory)
            self.assertEqual(list(recovered.pending()), [body])
            path = next(Path(directory).glob('*.json'))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            value = json.loads(path.read_text())
            value['expires_at'] = 0
            path.write_text(json.dumps(value))
            self.assertEqual(list(recovered.pending()), [])
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
