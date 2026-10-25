"""Called inside real manager tests; synthetic credentials only, never print them."""

import asyncio
from dataclasses import replace
import json
import os
from queue import Queue
import ssl
import tempfile
import traceback
import uuid
from pathlib import Path
import sys
from types import SimpleNamespace, ModuleType
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(os.environ['MIA_SCOPE_UPSTREAM']) / 'main/xiaozhi-server'))

from config.manage_api_client import ManageApiClient
from youmi_companion.authorization import (PrivateAuthorization, PrivateAuthorizationRequired,
    load_private_config, allow_generation, allow_output, execute_tool, server_tls_context, intent_reply)

trace = []


class TracedAuthorization(PrivateAuthorization):
    async def _request(self, endpoint, body):
        entry = {'endpoint': endpoint}
        trace.append(entry)
        result = await super()._request(endpoint, body)
        entry['result_type'] = type(result).__name__
        if endpoint == '/config/mia-auth/verify' and isinstance(result, dict):
            entry['field_types'] = {key: type(value).__name__ for key, value in result.items()}
            entry['binding_version'] = result.get('binding_version')
        return result


async def rejects(operation):
    try:
        await operation
    except PrivateAuthorizationRequired:
        return
    raise AssertionError('Unauthorized operation was accepted')


async def native_wss(config, device, client, credential):
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve
    from core.auth import AuthManager

    transport_auth = AuthManager('synthetic-transport-only')
    legacy = transport_auth.generate_token(client, device)
    async def handler(socket):
        headers = dict(socket.request.headers)
        assert transport_auth.verify_token(headers.get('authorization', '')[7:],
            headers.get('client-id'), headers.get('device-id'))
        conn = SimpleNamespace(config=config, headers=headers, websocket=socket,
            read_config_from_api=True, session_id='native-tls-ci', client_abort=False)
        try:
            private = await load_private_config(conn)
            assert 'CI private prompt marker' in private['prompt']
            assert 'mia-private-credential' not in conn.headers
            await socket.send(json.dumps({'status': 'authorized'}))
            async for _ in socket:
                if await allow_output(conn):
                    await socket.send(json.dumps({'status': 'allowed'}))
        except PrivateAuthorizationRequired:
            await socket.send(json.dumps({'type': 'mia_authorization', 'status': 'required'}))

    async with serve(handler, '127.0.0.1', 0, ssl=server_tls_context()) as server:
        url = 'wss://127.0.0.1:' + str(server.sockets[0].getsockname()[1])
        verified = ssl.create_default_context(cafile=os.environ['MIA_SCOPE_CA'])
        headers = {'Device-Id': device, 'Client-Id': client, 'Authorization': 'Bearer ' + legacy}
        # Native TLS must verify a CA and address; no trust-all socket client.
        try:
            async with connect(url, ssl=ssl.create_default_context(), additional_headers=headers):
                raise AssertionError('Untrusted certificate accepted')
        except ssl.SSLCertVerificationError:
            pass
        async with connect(url, ssl=verified, additional_headers=headers) as socket:
            assert json.loads(await socket.recv())['status'] == 'required'
        headers['Mia-Private-Credential'] = credential
        async with connect(url, ssl=verified, additional_headers=headers) as socket:
            assert json.loads(await socket.recv())['status'] == 'authorized'
            await socket.send('check')
            assert json.loads(await socket.recv())['status'] == 'allowed'
            async with httpx.AsyncClient(timeout=3, trust_env=False) as http:
                response = await http.post(config['manager-api']['url'].rstrip('/') + '/device/mia-auth/revoke',
                    json={'device_id': device}, headers={'Authorization': 'Bearer ' + os.environ['MIA_SCOPE_USER_TOKEN']})
                assert response.json()['code'] == 0
            await socket.send('check')
            required = json.loads(await socket.recv())
            assert required['type'] == 'mia_authorization' and required['status'] == 'required'
    return True


async def main():
    config = {'manager-api': {'url': os.environ['MIA_SCOPE_MANAGER_URL'],
                             'secret': os.environ['MIA_SCOPE_SERVER_SECRET']}, 'selected_module': {}}
    ManageApiClient(config)
    auth = TracedAuthorization(config)
    device, client, credential = (os.environ[name] for name in (
        'MIA_SCOPE_DEVICE', 'MIA_SCOPE_CLIENT', 'MIA_SCOPE_CREDENTIAL'))
    scope, private = await auth.private_config(device, client, credential, {})
    assert private['mia_owner'] == scope.owner
    assert 'CI private prompt marker' in private['prompt']
    assert isinstance(private['correct_words'], list)
    assert credential not in json.dumps(scope.snapshot())
    await auth.require(scope, 'memory:manage')
    restored = await auth.restore(scope.snapshot())
    assert restored == scope
    worker_models = await auth.worker_model(scope)
    assert set(worker_models) == {'mia_scope', 'selected_module', 'LLM'}
    assert worker_models['selected_module'] == {'LLM': 'LLM_ChatGLMLLM'}
    assert set(worker_models['LLM']) == {'LLM_ChatGLMLLM'}
    async with httpx.AsyncClient(timeout=3, trust_env=False) as http:
        response = await http.post(config['manager-api']['url'].rstrip('/') + '/config/mia-auth/worker-model',
            json=scope.snapshot(), headers={'Authorization': 'Bearer ' + os.environ['MIA_SCOPE_USER_TOKEN']})
        assert response.json()['code'] != 0
    forged = dict(scope.snapshot(), owner='user:999999999:agent:other')
    await rejects(auth.restore(forged))
    await rejects(auth._request('/config/mia-auth/worker-model', forged))
    await rejects(auth.private_config(device, client, 'legacy-signature.timestamp', {}))
    await rejects(auth.private_config(device, 'other-client', credential, {}))
    conn = SimpleNamespace(config=config, headers={'device-id': device, 'client-id': client,
                           'mia-private-credential': credential}, websocket=SimpleNamespace(transport=None))
    await rejects(load_private_config(conn))
    assert 'mia-private-credential' not in conn.headers
    assert conn._youmi_scope is None
    class Socket:
        sent = []
        async def send(self, message):
            parsed = json.loads(message)
            assert parsed['type'] in {'mia_authorization', 'mia_memory'}
            self.sent.append(parsed)
    class Tool:
        calls = 0
        async def handle_llm_function_call(self, conn, request):
            self.calls += 1
            return 'synthetic tool output'
    tool = Tool()
    intent = SimpleNamespace(replyResult=tool.handle_llm_function_call)
    active = SimpleNamespace(config=config, _youmi_scope=scope, _youmi_authorization=auth, read_config_from_api=True,
        session_id='scope-ci', client_abort=False, websocket=Socket(), loop=asyncio.get_running_loop(), func_handler=tool,
        intent=intent)
    assert await asyncio.to_thread(allow_generation, active)
    assert await allow_output(active)
    assert await execute_tool(active, {'name': 'synthetic_tool'}) == 'synthetic tool output'
    assert await intent_reply(active, 'synthetic context', 'query') == 'synthetic tool output'
    # Exercise the installed upstream dispatcher, with only logger/executor stubs.
    # Authorization, manager HTTP/TLS, binding and grant state remain real.
    logger_module = ModuleType('config.logger')
    logger_module.setup_logging = lambda: SimpleNamespace(**{name: lambda *args, **kwargs: None
        for name in ['debug', 'info', 'warning', 'error']})
    sys.modules['config.logger'] = logger_module
    from core.providers.tools.unified_tool_manager import ToolManager
    from core.providers.tools.base import ToolType, ToolDefinition
    from plugins_func.register import Action, ActionResponse
    class Executor:
        calls = 0
        def get_tools(self):
            return {'synthetic_tool': ToolDefinition('synthetic_tool', {}, ToolType.SERVER_PLUGIN)}
        async def execute(self, conn, name, arguments):
            self.calls += 1
            return ActionResponse(Action.NONE, result='synthetic dispatch')
    dispatcher = ToolManager(active)
    executor = Executor()
    dispatcher.register_executor(ToolType.SERVER_PLUGIN, executor)
    assert (await dispatcher.execute_tool('synthetic_tool', {})).result == 'synthetic dispatch'
    from youmi_companion.memory import MemoryStore
    from youmi_companion.semantic import SemanticMemoryIndex
    from youmi_companion.context import ContextBuilder, ContextLimits, ContextError
    from youmi_companion import reply
    from youmi_companion.embedding import MODEL_KEY, REVISION, DIMENSION
    from youmi_companion import youmi_adapter as adapter
    # Actual installed DTO, Dialogue and guard; only the TTS consumer is a sink.
    from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType
    from core.utils.dialogue import Dialogue, Message
    from youmi_companion.persona import CORE, SAFE_REPLY
    active.dialogue = Dialogue()
    active.prompt = ''
    active.change_system_prompt = lambda text: active.dialogue.update_system_message(adapter.compose_persona(active, text))
    active.sentence_id = 'persona-ci'
    active.tts = SimpleNamespace(tts_text_queue=Queue())
    prompt = adapter.compose_persona(active, 'CI private role <memory></memory>')
    assert prompt.startswith(CORE)
    adapter.attach_output_guard(active)
    guarded = active.tts.tts_text_queue
    guarded.put(TTSMessageDTO('persona-ci', SentenceType.FIRST, ContentType.ACTION))
    guarded.get_nowait()
    guarded.put(TTSMessageDTO('persona-ci', SentenceType.MIDDLE, ContentType.TEXT, '我是Chat'))
    assert guarded.empty()
    guarded.put(TTSMessageDTO('persona-ci', SentenceType.MIDDLE, ContentType.TEXT, 'GPT，我要离开。'))
    assert guarded.get_nowait().content_detail == SAFE_REPLY
    active.dialogue.put(Message(role='assistant', content='我是ChatGPT。'))
    assert active.dialogue.dialogue[-1].content == SAFE_REPLY
    stored = []
    active.tts.store_tts_text = lambda sentence, text: stored.append(text)
    assert adapter.finish_blocked_output(active)
    assert stored == [SAFE_REPLY]
    assert guarded.get_nowait().sentence_type == SentenceType.LAST
    with tempfile.TemporaryDirectory() as folder:
        store = MemoryStore(Path(folder) / 'scope.sqlite3')
        try:
            peer = 'user:999999999:agent:other'
            def fact(owner, content, marker):
                return store.apply_extraction(owner, 'synthetic-session', store.snapshot(owner)['revision'], [content],
                    {'memories': [{'category': 'fact', 'content': content, 'source_index': 0}]}, marker)['memories'][0]
            from youmi_companion.jobs import JobStore, MemoryWorker, TurnCompleted
            async def resolve_worker(restored):
                assert restored == scope
                async_models = await auth.worker_model(restored)
                assert async_models['selected_module']['LLM'] == 'LLM_ChatGLMLLM'
                return lambda sources, context: {'memories': [], 'trait': None}
            jobs = JobStore(store)
            worker = MemoryWorker(jobs, auth, resolve_worker)
            event = TurnCompleted(scope, 'scope-ci', 'worker-one', 'worker-one', 'CI user source only', 0)
            accepted = await worker.submit(event)
            assert accepted['status'] == 'accepted'
            jobs.recover()
            await worker.run_once()
            assert jobs.current(accepted)['state'] == 'empty'
            await rejects(worker.submit(TurnCompleted(replace(scope, owner=peer),
                'scope-ci', 'forged', 'forged', 'CI cross owner', 0)))
            own_id = fact(scope.owner, 'CI own memory', 'own')
            other_id = fact(peer, 'CI other memory', 'other')
            with store.db:
                for memory_id, keyword in ((own_id, 'own clue'), (other_id, 'FOREIGN CLUE')):
                    store.db.execute('UPDATE mia_memory_meta SET keywords=?,trigger_terms=? WHERE memory_id=?',
                        (json.dumps([keyword]), json.dumps(['CI']), memory_id))
            class SyntheticVectorEncoder:
                model_id, revision, dimension = MODEL_KEY, REVISION, DIMENSION
                def encode(self, text, query=False):
                    import numpy as np
                    value = np.zeros(DIMENSION, dtype=np.float32)
                    value[0] = 1
                    return value
            async def resolve_structured(restored):
                assert restored == scope
                return lambda sources, context: {'memories': [{'category': 'preference', 'content': sources[0],
                    'source_quote': sources[0], 'source_index': 0, 'kind': 'explicit', 'subject': 'user',
                    'value': 'durable', 'keywords': ['蓝色'], 'trigger_terms': ['喜欢']}]}
            structured_worker = MemoryWorker(jobs, auth, resolve_structured, encoder=SyntheticVectorEncoder())
            structured = await structured_worker.submit(TurnCompleted(scope, 'scope-ci', 'extract-own', 'extract-own',
                '我喜欢蓝色', store.snapshot(scope.owner)['revision']))
            await structured_worker.run_once()
            assert jobs.current(structured)['state'] == 'committed'
            extracted = store.db.execute("SELECT m.id,m.owner FROM memories m JOIN mia_source_meta s ON s.memory_id=m.id "
                "WHERE s.message_id='extract-own'").fetchone()
            assert extracted['owner'] == scope.owner
            assert store.db.execute('SELECT COUNT(*) FROM mia_vectors WHERE memory_id=?', (extracted['id'],)).fetchone()[0] == 1
            from youmi_companion.extraction import validate_output, context_for
            relation_source = '我使用CI文具'
            relation_proposal = {'category': 'fact', 'content': relation_source, 'source_quote': relation_source,
                'source_index': 0, 'kind': 'explicit', 'subject': 'user', 'value': 'durable',
                'relations': [{'target_id': own_id, 'relation': 'shared_term', 'anchor': 'CI', 'evidence': relation_source}]}
            owned_context = context_for(store, scope.owner)
            assert validate_output({'memories': [relation_proposal]}, [relation_source], owned_context)['memories']
            relation_proposal['relations'][0]['target_id'] = other_id
            try:
                validate_output({'memories': [relation_proposal]}, [relation_source], owned_context)
            except adapter.MemoryError:
                pass
            else:
                raise AssertionError('Foreign extraction relation was accepted')
            correction_source = '更正：我喜欢蓝色；我喜欢绿色'
            correction = {'category': 'preference', 'content': '我喜欢绿色', 'source_quote': '我喜欢绿色',
                'source_index': 0, 'kind': 'explicit', 'subject': 'user', 'value': 'durable',
                'reconciliation': {'target_id': extracted['id'], 'kind': 'correction',
                    'old_quote': '我喜欢蓝色', 'evidence': correction_source}}
            async def resolve_correction(restored):
                await auth.worker_model(restored)
                return lambda sources, context: {'memories': [correction]}
            correction_worker = MemoryWorker(jobs, auth, resolve_correction, encoder=SyntheticVectorEncoder())
            correction_task = await correction_worker.submit(TurnCompleted(scope, 'scope-ci', 'correct-own', 'correct-own',
                correction_source, store.snapshot(scope.owner)['revision']))
            await correction_worker.run_once()
            assert jobs.current(correction_task)['state'] == 'committed'
            assert store.db.execute('SELECT content FROM memories WHERE id=?', (extracted['id'],)).fetchone()[0] == '我喜欢绿色'
            correction['reconciliation']['target_id'] = other_id
            try:
                validate_output({'memories': [correction]}, [correction_source], context_for(store, scope.owner))
            except adapter.MemoryError:
                pass
            else:
                raise AssertionError('Foreign correction was accepted')
            store.delete_memory(scope.owner, extracted['id'])
            vector_index = SemanticMemoryIndex(store, SyntheticVectorEncoder())
            await vector_index.index_memory(scope, auth, own_id)
            try:
                await vector_index.index_memory(scope, auth, other_id)
            except adapter.MemoryError as error:
                assert error.code == 'not_found'
            else:
                raise AssertionError('Cross-owner vector indexing was accepted')
            found = await vector_index.candidates(scope, auth, 'CI synthetic vector')
            assert [row['id'] for row in found['candidates']] == [own_id]
            hybrid = await vector_index.retrieve(scope, auth, 'CI synthetic vector')
            assert [row['id'] for row in hybrid['candidates']] == [own_id]
            assert [row['term'] for row in hybrid['expansion']] == ['own clue']
            assert [row['id'] for row in hybrid['results']] == [own_id]
            context_builder = ContextBuilder(vector_index)
            event_bundle = await context_builder.build(scope, auth, 'event-scope-ci', '你还记得CI own memory吗',
                limits=ContextLimits('scope-ci-fixture', 16384, 128, 'CI capacity fixture; not production'), session_id='event-session')
            assert await context_builder.complete(event_bundle, auth, '😌记得，CI own memory。')
            event_id = event_bundle.diagnostics()['events'][0]['id']
            assert store.db.execute('SELECT state FROM mia_events WHERE id=?', (event_id,)).fetchone()[0] == 'active'
            await auth.require(scope, 'memory:manage')
            store.handle(scope.owner, {'action': 'emotion_consent', 'enabled': True})
            async def resolve_observation(restored):
                await auth.worker_model(restored)
                return lambda sources, context: {'memories': [], 'observations': [{'source_index': 0,
                    'source_quote': sources[0], 'subject': 'user', 'source_kind': 'self_report',
                    'tendency': 'negative', 'confidence': 0.9}]}
            observation_worker = MemoryWorker(jobs, auth, resolve_observation, encoder=SyntheticVectorEncoder())
            observation_task = await observation_worker.submit(TurnCompleted(scope, 'scope-ci', 'mood-own', 'mood-own',
                '我今天很难过', store.snapshot(scope.owner)['revision']))
            await observation_worker.run_once()
            assert jobs.current(observation_task)['state'] == 'committed'
            assert store.handle(scope.owner, {'action': 'emotion_status'})['trend']['status'] == 'insufficient_observations'
            assert store.handle(peer, {'action': 'observations'})['observations'] == []
            store.handle(scope.owner, {'action': 'emotion_consent', 'enabled': False})
            pending = await worker.submit(TurnCompleted(scope, 'scope-ci', 'worker-delete', 'worker-delete', 'CI user',
                store.snapshot(scope.owner)['revision']))
            context_bundle = await context_builder.build(scope, auth, 'context-scope-ci', 'CI synthetic context',
                limits=ContextLimits('scope-ci-fixture', 16384, 128, 'CI capacity fixture; not production'))
            assert context_bundle.diagnostics()['selected_fact_ids'] == [own_id]
            assert 'CI other memory' not in context_bundle.request_json
            assert 'FOREIGN CLUE' not in context_bundle.request_json
            profile = Path(folder) / 'context-profile.json'
            active.llm = SimpleNamespace(model_name='scope-ci-fixture', max_tokens=128, base_url='https://ci.invalid')
            profile.write_text(json.dumps({'provider_module': type(active.llm).__module__, 'model': active.llm.model_name,
                'base_url': active.llm.base_url, 'context_window': 16384,
                'capacity_source': 'Synthetic manager fixture; not production model evidence'}))
            active._youmi_context_builder = context_builder
            active.dialogue = Dialogue()
            active.dialogue.put(Message(role='user', content='CI synthetic context'))
            with patch.dict(os.environ, {'MIA_CONTEXT_PROFILE_FILE': str(profile)}):
                reply_turn = await reply.prepare(active, [{'type': 'function', 'function': {'name': 'synthetic_tool'}}], 0)
                assert reply_turn.bundle.diagnostics()['selected_fact_ids'] == [own_id]
                assert 'CI other memory' not in reply_turn.bundle.request_json
                assert await execute_tool(active, {'name': 'synthetic_tool'}) == 'synthetic tool output'
            active._youmi_memory = adapter.YoumiMemoryProvider(store, scope.owner, None, 'scope-ci', scope, auth)
            active.dialogue = SimpleNamespace(dialogue=[])
            for action in ('list', 'events', 'observations', 'job_status'):
                request_id = str(uuid.uuid4())
                await adapter.handle_message(active, json.dumps({'type': 'mia_memory', 'request_id': request_id, 'action': action}))
                response = active.websocket.sent[-1]
                assert response['request_id'] == request_id and 'error' not in response
                if action == 'list':
                    assert response['result']['management']['version'] == 1
                    assert 'emotion_consent' in response['result']['management']['actions']
                elif action == 'events':
                    managed_event = next(item for item in response['result']['events'] if item['id'] == event_id)
                    assert managed_event['user_excerpt'] == '你还记得CI own memory吗'
                    assert managed_event['assistant_excerpt'] == '😌记得，CI own memory。'
                elif action == 'job_status':
                    assert response['result']['id'] == pending['id'] and response['result']['state'] == 'pending'
                else:
                    assert response['result']['consent']['enabled'] == 0
                    assert response['result']['trend'] == {'status': 'disabled'}
                    assert len(response['result']['observations']) == 1
            for request, code in [({'action': 'clear', 'owner': peer}, 'not_authorized'),
                                  ({'action': 'observations', 'owner': peer}, 'not_authorized'),
                                  ({'action': 'event_delete', 'owner': peer, 'id': event_id}, 'not_authorized'),
                                  ({'action': 'delete', 'id': other_id}, 'not_found'),
                                  ({'action': 'update', 'id': other_id, 'content': 'CI tampered'}, 'not_found')]:
                await adapter.handle_message(active, json.dumps(dict(request, type='mia_memory', request_id=str(uuid.uuid4()))))
                assert active.websocket.sent[-1]['error']['code'] == code
                assert store.list_memories(scope.owner)[0]['id'] == own_id
                assert store.list_memories(peer)[0]['id'] == other_id
            await adapter.handle_message(active, json.dumps({'type': 'mia_memory', 'request_id': str(uuid.uuid4()),
                'action': 'delete', 'id': own_id, 'owner': scope.owner}))
            assert store.list_memories(scope.owner) == [] and store.list_memories(peer)[0]['id'] == other_id
            assert jobs.current(pending)['state'] == 'cancelled'
            assert jobs.current(pending)['source_payload'] == '{}'
            try:
                await context_builder.validate(context_bundle, auth)
            except ContextError as error:
                assert str(error) == 'context_changed'
            else:
                raise AssertionError('Deleted context was reused')
            with patch.dict(os.environ, {'MIA_CONTEXT_PROFILE_FILE': str(profile)}):
                rebuilt = await reply.prepare(active, [], 1)
                assert not rebuilt.bundle.diagnostics()['selected_fact_ids']
                assert not rebuilt.bundle.request().get('tools')
        finally:
            store.close()
    tls_pass = await native_wss(config, device, client, credential) if os.environ.get('MIA_SCOPE_NATIVE_TLS') == 'true' else None
    # Actual manager revocation, using the existing authenticated console token.
    async with httpx.AsyncClient(timeout=3, trust_env=False) as http:
        response = await http.post(config['manager-api']['url'].rstrip('/') + '/device/mia-auth/revoke',
            json={'device_id': device}, headers={'Authorization': 'Bearer ' + os.environ['MIA_SCOPE_USER_TOKEN']})
        assert response.json()['code'] == 0
    await rejects(auth.require(scope, 'memory:read'))
    await rejects(auth.restore(scope.snapshot()))
    await rejects(auth.worker_model(scope))
    await rejects(worker.submit(event))
    await rejects(auth.private_config(device, client, credential, {}))
    # The fixture DB is already closed: revocation must be rejected before any
    # DB access, not converted to an empty vector result or an indexing retry.
    await rejects(vector_index.candidates(scope, auth, 'CI revoked vector'))
    await rejects(vector_index.retrieve(scope, auth, 'CI revoked hybrid'))
    await rejects(context_builder.validate(context_bundle, auth))
    await rejects(reply.prepare(active, [], 1))
    await rejects(vector_index.index_memory(scope, auth, own_id))
    assert not await asyncio.to_thread(allow_generation, active)
    assert not await allow_output(active)
    await rejects(execute_tool(active, {'name': 'synthetic_tool'}))
    await rejects(intent_reply(active, 'synthetic context', 'query'))
    await rejects(dispatcher.execute_tool('synthetic_tool', {}))
    assert executor.calls == 1
    assert active.client_abort and tool.calls == 3
    print(json.dumps({'manager_python_scope_pass': True, 'checks': 34, 'vector_scope_checks_pass': True,
                      'reply_scope_checks_pass': True, 'worker_scope_checks_pass': True, 'extraction_scope_checks_pass': True,
                      'maintenance_scope_checks_pass': True,
                      'events_scope_checks_pass': True, 'emotion_scope_checks_pass': True,
                      'native_wss_scope_pass': tls_pass,
                      'real_model_acceptance': 'not_executed', 'actual_ios_wss_acceptance': 'not_executed'}))


try:
    asyncio.run(main())
except Exception as error:
    frames = [{'file': Path(frame.filename).name, 'function': frame.name, 'line': frame.lineno}
              for frame in traceback.extract_tb(error.__traceback__)]
    print(json.dumps({'manager_python_scope_pass': False, 'error_class': type(error).__name__,
                      'error_locations': frames, 'request_trace': trace}))
    sys.exit(1)
finally:
    ManageApiClient.safe_close()
