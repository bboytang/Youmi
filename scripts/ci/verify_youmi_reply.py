"""Installed connection + actual Dialogue/BGE/OpenAI SDK, synthetic HTTP/tool/ASR sinks.

Only unrelated startup imports and hardware initialization are omitted. Selected
installed classes/functions are compiled unchanged, including the entire chat
and recursive tool handlers. This is not a live provider or acoustic test.
"""

import argparse
import ast
import asyncio
import __future__
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from queue import Queue
import re
import tempfile
import time
from types import ModuleType, SimpleNamespace
import sys
import uuid

import httpx
import openai
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve


errors = []
logger = SimpleNamespace()
logger.bind = lambda *args, **kwargs: logger
for level in ('debug', 'info', 'warning'):
    setattr(logger, level, lambda *args, **kwargs: None)
logger.error = lambda *args, **kwargs: errors.append(str(args[0]) if args else 'error')
logging_module = ModuleType('config.logger')
logging_module.setup_logging = lambda: logger
sys.modules['config.logger'] = logging_module
util = ModuleType('core.utils.util')
util.check_model_key = lambda *args, **kwargs: None
sys.modules['core.utils.util'] = util

from core.providers.llm.openai.openai import LLMProvider
from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType
from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType
from core.utils.dialogue import Dialogue, Message
from plugins_func.register import Action, ActionResponse
from youmi_companion import youmi_adapter as youmi, authorization as youmi_auth, reply
from youmi_companion.context import ContextBuilder, turns
from youmi_companion.embedding import SemanticEncoder
from youmi_companion.memory import MemoryStore
from youmi_companion.jobs import JobStore, MemoryWorker
from youmi_companion.semantic import SemanticMemoryIndex


def installed(path, names, namespace, evidence):
    source = path.read_text()
    evidence[str(path.relative_to(Path('/installed')))] = hashlib.sha256(source.encode()).hexdigest()
    tree = ast.parse(source)
    selected = [node for node in tree.body if getattr(node, 'name', None) in names
        or isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)]
    assert len(selected) == len(names), (path, names)
    # Keep function/class bodies exactly as installed, without audio imports.
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec',
        flags=__future__.annotations.compiler_flag), namespace)


class Policy:
    active = True

    async def require(self, scope, permission):
        if not self.active or permission not in scope.permissions:
            raise youmi_auth.PrivateAuthorizationRequired()

    async def restore(self, snapshot):
        if not self.active or snapshot != self.scope.snapshot():
            raise youmi_auth.PrivateAuthorizationRequired()
        return self.scope


class TrackingExecutor:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.jobs = []

    def submit(self, *args):
        future = self.pool.submit(*args)
        self.jobs.append(future)
        return future


async def evaluate(directory, output):
    root = Path('/installed')
    hashes = output['installed_sources'] = {}
    async def noop(*args, **kwargs):
        return False
    def text_only(text):
        return re.sub(r'[😌🙂🤔😲🙄😍😔😜。！？!?，,]', '', text)
    ns = dict(asyncio=asyncio, json=json, re=re, uuid=uuid, time=time, TAG='P15', youmi=youmi, youmi_auth=youmi_auth,
        Message=Message, Dialogue=Dialogue, TTSMessageDTO=TTSMessageDTO, SentenceType=SentenceType,
        ContentType=ContentType, Action=Action, ActionResponse=ActionResponse, TextMessageHandler=TextMessageHandler,
        TextMessageType=TextMessageType, enqueue_tool_report=lambda *args, **kwargs: None,
        enqueue_asr_report=lambda *args, **kwargs: None, checkWakeupWords=noop,
        remove_punctuation_and_length=lambda text: (len(text), text_only(text)),
        textUtils=SimpleNamespace(get_emotion=noop, check_emoji=text_only, get_string_no_punctuation_or_emoji=text_only),
        get_system_error_response=lambda config: 'UNEXPECTED_UPSTREAM_ERROR')
    installed(root / 'core/connection.py', {'ConnectionHandler', 'DIRECT_ANSWER_TOOL'}, ns, hashes)
    installed(root / 'core/handle/sendAudioHandle.py', {'send_stt_message', 'send_tts_message'}, ns, hashes)
    installed(root / 'core/handle/intentHandler.py', {'handle_user_intent', 'check_direct_exit'}, ns, hashes)
    installed(root / 'core/handle/receiveAudioHandle.py', {'startToChat'}, ns, hashes)
    installed(root / 'core/handle/textHandler/listenMessageHandler.py', {'ListenTextMessageHandler'}, ns, hashes)
    installed(root / 'core/handle/textMessageProcessor.py', {'TextMessageProcessor'}, ns, hashes)
    handler = ns['ListenTextMessageHandler']()
    processor = ns['TextMessageProcessor'](SimpleNamespace(get_handler=lambda name: handler if name == 'listen' else None))
    async def handle_text(conn, message):
        await processor.process_message(conn, message)
    ns['handleTextMessage'] = handle_text
    encoder, policy = SemanticEncoder(directory), Policy()
    scope = youmi_auth.AuthenticatedScope('user:1:agent:reply-ci', 1, 1, 'reply-ci', 'mia', 'ci-device', 'ci-client', 0, 1,
        datetime.now(timezone.utc) + timedelta(days=1), frozenset({'memory:read', 'memory:write', 'memory:manage'}))
    with tempfile.TemporaryDirectory() as folder:
        store = MemoryStore(Path(folder) / 'reply.sqlite3')
        policy.scope = scope
        async def resolve_memory(restored):
            assert restored == scope
            return lambda sources, context: {'memories': [], 'trait': None}
        worker = MemoryWorker(JobStore(store), policy, resolve_memory)
        server_context = SimpleNamespace(_youmi_worker=worker)
        completion_jobs = []
        requests, tool_calls, emitted = [], [], []
        mode, step, mutate = 'plain', 0, None
        tools = [{'type': 'function', 'function': {'name': 'weather', 'parameters': {'type': 'object', 'properties': {}}}}]
        async def execute(conn, call):
            nonlocal mutate
            tool_calls.append(call['id'])
            if mutate:
                mutate()
                mutate = None
            return ActionResponse(Action.RECORD if mode == 'record' else Action.RESPONSE if mode == 'tool_response' else Action.REQLLM, result='CI_TOOL_RESULT')
        def transport(request):
            nonlocal step
            body = json.loads(request.content)
            requests.append(body)
            step += 1
            turns(body['messages'][2:])
            assert body['max_tokens'] == 1024
            assert 'FOREIGN_PRIVATE' not in request.content.decode()
            data = json.loads(body['messages'][1]['content'])['youmi_context']
            if mode in {'tools', 'delete', 'revoke', 'record', 'tool_response'} and step <= (2 if mode == 'tools' else 1):
                count = 2 if mode == 'tools' and step == 1 else 1
                delta = {'tool_calls': [{'index': i, 'id': f'{mode}-call-{step}-{i}', 'type': 'function',
                    'function': {'name': 'weather', 'arguments': '{}'}} for i in range(count)]}
            elif mode == 'direct':
                delta = {'tool_calls': [{'index': 0, 'id': 'direct', 'type': 'function',
                    'function': {'name': 'direct_answer', 'arguments': json.dumps({'response': '😌这是直接回答。'}, ensure_ascii=False)}}]}
            else:
                if mode == 'delete':
                    assert not data['facts'] and 'CI_TOOL_RESULT' not in request.content.decode()
                    assert not body.get('tools')
                elif mode in {'plain', 'tools', 'voice'}:
                    assert any(row['content'] == '每次喝全脂牛奶，我的肚子都会不舒服。' for row in data['facts'])
                delta = {'content': '😌根据已知信息回答。'}
            chunk = {'id': 'ci', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'ci-synthetic',
                'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                content=('data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\ndata: [DONE]\n\n').encode())
        provider = LLMProvider({'model_name': 'ci-synthetic', 'api_key': 'ci-synthetic-key',
            'base_url': 'https://ci.invalid/v4', 'max_tokens': 1024, 'timeout': 5})
        provider.client.close()
        provider.client = openai.OpenAI(api_key='ci-synthetic-key', base_url=provider.base_url, max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(transport)))
        profile = Path(folder) / 'profile.json'
        profile.write_text(json.dumps({'provider_module': type(provider).__module__, 'base_url': provider.base_url,
            'model': provider.model_name, 'context_window': 16384,
            'capacity_source': 'Synthetic SDK transport capacity fixture, not production evidence'}))
        os.environ['MIA_CONTEXT_PROFILE_FILE'] = str(profile)
        index = SemanticMemoryIndex(store, encoder)
        def remember(owner, text, marker):
            return store.apply_extraction(owner, marker, store.snapshot(owner)['revision'], [text],
                {'memories': [{'category': 'fact', 'content': text, 'source_index': 0}]}, marker)['memories'][0]
        fact = remember(scope.owner, '每次喝全脂牛奶，我的肚子都会不舒服。', 'own')
        remember('user:2:agent:other', 'FOREIGN_PRIVATE', 'foreign')
        await index.rebuild_batch(scope, policy)
        executor = TrackingExecutor()
        class Sink:
            def __init__(self):
                self.tts_text_queue = Queue()
            def store_tts_text(self, *args):
                pass
            def tts_one_sentence(self, conn, content_type, content_detail):
                self.tts_text_queue.put(TTSMessageDTO(conn.sentence_id, SentenceType.MIDDLE, ContentType.TEXT, content_detail))
        def connection(socket):
            conn = object.__new__(ns['ConnectionHandler'])
            conn.__dict__.update(_youmi_scope=scope, _youmi_authorization=policy, read_config_from_api=True,
                _youmi_context_builder=ContextBuilder(index), llm=provider, session_id='reply-ci', logger=logger,
                dialogue=Dialogue(), loop=asyncio.get_running_loop(), executor=executor, websocket=socket,
                memory=None, _youmi_memory=youmi.YoumiMemoryProvider(store, scope.owner, provider, 'reply-ci', scope, policy),
                server=server_context, intent_type='function_call', need_bind=False, client_abort=False,
                bind_completed_event=asyncio.Event(), max_output_size=0, client_is_speaking=False,
                client_listen_mode='manual', introduced_speakers=set(), system_introduced_speakers=set(),
                current_speaker=None, cmd_exit=[], features={'emoji': False},
                config={'wakeup_words': [], 'tool_call_timeout': 2}, prompt='简短中文。', tts=Sink(),
                func_handler=SimpleNamespace(get_functions=lambda: tools, handle_llm_function_call=execute))
            conn.bind_completed_event.set()
            conn.reset_audio_states = lambda: None  # Physical audio reset is outside this test.
            youmi.apply_persona_prompt(conn)
            youmi.attach_output_guard(conn)
            return conn
        current = None
        async def socket_handler(socket):
            nonlocal current
            current = connection(socket)
            async for message in socket:
                parsed = json.loads(message)
                if parsed.get('type') == 'ci_recognized_voice':
                    # Invoke the identical installed callback used after ASR.
                    await ns['startToChat'](current, parsed['text'])
                else:
                    await current._route_message(message)
                jobs = list(executor.jobs)
                for job in jobs:
                    await asyncio.wrap_future(job)
                # Deliver via the installed send_tts_message serializer; only
                # waveform synthesis/audio sink is omitted.
                while not current.tts.tts_text_queue.empty():
                    item = current.tts.tts_text_queue.get()
                    if item.content_type == ContentType.TEXT:
                        await ns['send_tts_message'](current, 'sentence_start', item.content_detail)
                await socket.send(json.dumps({'type': 'ci_done'}))
        async with serve(socket_handler, '127.0.0.1', 0) as server:
            async with connect('ws://127.0.0.1:' + str(server.sockets[0].getsockname()[1])) as socket:
                cases = []
                async def text_case(name):
                    nonlocal mode, step
                    mode, step = name, 0
                    start = len(requests)
                    await socket.send(json.dumps({'type': 'ci_recognized_voice' if name == 'voice' else 'listen',
                        'state': 'detect', 'text': '我喝哪类乳品容易闹肚子？'}))
                    messages = []
                    while True:
                        message = json.loads(await asyncio.wait_for(socket.recv(), 15))
                        if message['type'] == 'ci_done':
                            break
                        messages.append(message)
                    assert any(m['type'] == 'stt' for m in messages)
                    # RECORD tools may produce their own audio/file output; the
                    # generic terminal handler only records history.
                    if name not in {'revoke', 'record'}:
                        assert any(m['type'] == 'tts' and m.get('text') for m in messages)
                    job_status = getattr(current, '_youmi_job_status', None)
                    if name in {'plain', 'tools', 'direct', 'voice', 'record', 'tool_response'}:
                        assert job_status['status'] == 'accepted', (name, job_status)
                        job = worker.jobs.current(job_status)
                        assert job['state'] == 'pending' and worker.foreground == 0
                        assert not job['validated_result']
                        assert len([m for m in messages if m['type'] == 'mia_memory_job']) == 1
                        await worker.run_once()
                        assert worker.jobs.current(job_status)['state'] == 'empty'
                        completion_jobs.append({'case': name, 'id': job_status['id'], 'state': 'empty'})
                    cases.append({'name': name, 'provider_requests': len(requests) - start,
                        'protocol': messages, 'diagnostics': getattr(current, '_youmi_context_status', None)})
                    return cases[-1]
                assert (await text_case('plain'))['provider_requests'] == 1
                assert (await text_case('tools'))['provider_requests'] == 3
                assert len(tool_calls) == 3 and len(set(tool_calls)) == 3
                assert (await text_case('direct'))['provider_requests'] == 1
                assert (await text_case('voice'))['provider_requests'] == 1
                assert (await text_case('record'))['provider_requests'] == 1
                assert (await text_case('tool_response'))['provider_requests'] == 1
                mutate = lambda: store.delete_memory(scope.owner, fact)
                count = len(tool_calls)
                assert (await text_case('delete'))['provider_requests'] == 2
                assert len(tool_calls) == count + 1  # No side-effect replay during rebuild.
                # A missing profile now falls back to the conservative default
                # capacity instead of blocking the turn without an API call.
                profile.unlink()
                fallback = await text_case('default_capacity')
                assert fallback['provider_requests'] == 1
                assert not any(m['type'] == 'youmi_context' for m in fallback['protocol'])
                # Restore fixture for revoked authorization at tool continuation.
                profile.write_text(json.dumps({'provider_module': type(provider).__module__, 'base_url': provider.base_url,
                    'model': provider.model_name, 'context_window': 16384, 'capacity_source': 'CI synthetic'}))
                mutate = lambda: setattr(policy, 'active', False)
                assert (await text_case('revoke'))['provider_requests'] == 1
                assert current._youmi_authorization_required
                output['cases'] = cases
        output['completion_jobs'] = completion_jobs
        assert len(completion_jobs) == 6
        output['provider_requests'] = len(requests)
        output['tool_dispatches'] = len(tool_calls)
        output['sample_provider_request'] = requests[1]
        output['errors'] = errors
        assert not errors, errors
        output['passed'] = True
        executor.pool.shutdown(wait=True)
        provider.client.close()
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = {'code_sha': os.environ['GITHUB_SHA'], 'passed': False, 'kind': 'P16_installed_completion_jobs_plus_P15_BGE_SDK_protocol',
        'provider': 'actual_installed_OpenAI_compatible_provider_with_synthetic_HTTP_SSE',
        'manager': 'policy_fixture_actual_manager_separate', 'tool_results': 'synthetic',
        'provider_capacity': 'synthetic_not_production', 'chat_provider_calls': 0,
        'acoustic_engine': 'not_executed', 'real_token_calibration': 'not_executed', 'full_B08': 'not_accepted'}
    try:
        asyncio.run(evaluate(args.model_dir, report))
    except Exception as error:
        report['error_class'] = type(error).__name__
        report['errors'] = errors
        # No live credentials or private data exist in this network-none fixture.
        report['failure'] = str(error)
        import traceback
        report['traceback'] = traceback.format_exc()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key not in ('cases', 'sample_provider_request')}))
    if not report['passed']:
        raise SystemExit('P15 installed reply verification failed; inspect fixture report')


if __name__ == '__main__':
    main()
