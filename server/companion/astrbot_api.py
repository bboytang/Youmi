"""Opt-in routes on the EXISTING XiaoZhi HTTP app; no second model reply or worker."""

import asyncio
import hmac
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
import weakref

from aiohttp import web

from .astrbot_identity import BridgeIdentity, canonical, digest, identifier, secret_file
from .astrbot_turns import TurnConflict, TurnLedger
from .authorization import PrivateAuthorization, PrivateAuthorizationRequired
from .context import ContextError, ContextLimits
from .memory import MemoryError, SECRET
from .persona import PersonaAgent


_runtime = None


def attach_runtime(store, worker):
    global _runtime
    _runtime = (store, worker)


class AstrBotHandler:
    def __init__(self, config, bridge, *, store=None, worker=None, builder=None, authorization=None):
        self.config, self.bridge = config, bridge
        self.store, self.worker, self.builder = store, worker, builder
        self.authorization = authorization
        self.locks = weakref.WeakValueDictionary()
        self.media_lock = asyncio.Semaphore(2)

    def _lock(self, key):
        lock = self.locks.get(key)
        if lock is None:
            lock = self.locks[key] = asyncio.Lock()
        return lock

    def _ledger(self, authorization):
        if self.store is None:
            if _runtime is None:
                raise TurnConflict('worker_unavailable', 503)
            self.store, self.worker = _runtime
        if self.builder is None:
            from .reply import builder_for
            self.builder = builder_for(self.store)
        return TurnLedger(self.store, self.builder, authorization, self.worker)

    async def handle(self, request):
        action = request.match_info['action']
        authorization = self.authorization or PrivateAuthorization(self.config)
        started, trace, outcome = time.monotonic(), '-', 'rejected'
        try:
            supplied = request.headers.get('Authorization', '')
            if not hmac.compare_digest(supplied, 'Bearer ' + secret_file(self.bridge['service_token_file'])):
                raise TurnConflict('service_unauthorized', 401)
            if not request.secure and not self.bridge.get('allow_internal_http', False):
                raise TurnConflict('tls_required', 403)
            ledger = self._ledger(authorization)
            if action == 'health' and request.method == 'GET':
                ledger.prune()
                return web.json_response({'protocol_version': 1, 'schema': 8,
                    'worker_available': bool(self.worker and not self.worker.closing)})
            if request.method != 'POST' or action not in {'prepare', 'check', 'claim', 'complete', 'tts', 'manage', 'status'}:
                raise TurnConflict('invalid_request', 400)
            raw = bytearray()
            async for chunk in request.content.iter_chunked(4096):
                raw.extend(chunk)
                if len(raw) > 65536:
                    raise TurnConflict('payload_too_large', 413)
            data = json.loads(raw)
            if not isinstance(data, dict) or type(data.get('protocol_version')) is not int or data['protocol_version'] != 1:
                raise ValueError()
            if action == 'manage':
                key = digest([data.get('bot_uin'), data.get('conversation_key')])
            elif action == 'prepare':
                key = self.prepare_key(data)
            else:
                key = identifier(data.get('turn_token'))
            trace = key if action in {'prepare', 'manage'} else ledger.by_token(key, allow_expired=True)['turn_key']
            async with self._lock(key):
                timeout = 30 if action == 'tts' else 15 if action == 'prepare' else 10
                result = await asyncio.wait_for(self.dispatch(action, data, ledger), timeout)
                outcome = 'accepted'
                return result
        except TurnConflict as error:
            return web.json_response({'error': {'code': error.code}}, status=error.status)
        except PrivateAuthorizationRequired:
            return web.json_response({'error': {'code': 'authorization_required'}}, status=403)
        except ContextError:
            return web.json_response({'error': {'code': 'context_changed'}}, status=409)
        except (ValueError, TypeError, KeyError, MemoryError, RecursionError):
            return web.json_response({'error': {'code': 'invalid_request'}}, status=400)
        except asyncio.TimeoutError:
            return web.json_response({'error': {'code': 'dependency_timeout'}}, status=504)
        except Exception:
            # Never serialize an upstream exception containing model config or user content.
            return web.json_response({'error': {'code': 'dependency_unavailable'}}, status=503)
        finally:
            if action in {'prepare', 'check', 'claim', 'complete', 'tts', 'manage', 'status'}:
                logging.getLogger(__name__).info('Youmi bridge phase=%s trace=%s outcome=%s duration_ms=%d',
                    action, trace, outcome, int((time.monotonic() - started) * 1000))
            if self.authorization is None:
                await authorization.close()

    def prepare_key(self, data):
        required = {'protocol_version', 'bot_uin', 'conversation_key', 'sender_uin', 'source_message_id',
                    'attempt_id', 'query_text', 'model', 'external_input_bytes', 'origin', 'source_kind'}
        if set(data) != required or data['origin'] not in {'user', 'scheduled'}:
            raise ValueError()
        for name in ('bot_uin', 'conversation_key', 'sender_uin', 'source_message_id', 'attempt_id'):
            identifier(data[name])
        if not isinstance(data['model'], str) or not 1 <= len(data['model']) <= 128 or any(ord(c) < 32 for c in data['model']):
            raise ValueError()
        if data['source_kind'] not in {'text', 'asr', 'media', 'scheduled'}:
            raise ValueError()
        if (data['origin'] == 'scheduled') != (data['source_kind'] == 'scheduled'):
            raise ValueError()
        if not isinstance(data['query_text'], str) or not 0 < len(data['query_text']) <= 4096:
            raise ValueError()
        if type(data['external_input_bytes']) is not int or not 0 <= data['external_input_bytes'] <= 1000000:
            raise ValueError()
        return digest(['qq', data['bot_uin'], data['conversation_key'], data['origin'], data['source_message_id']])

    async def dispatch(self, action, data, ledger):
        identity = BridgeIdentity(ledger.authorization, self.bridge['bindings'])
        if action == 'manage':
            if set(data) != {'protocol_version', 'bot_uin', 'sender_uin', 'conversation_key', 'request'}:
                raise ValueError()
            scope = await identity.resolve(data)
            await ledger.authorization.require(scope, 'memory:manage')
            command = data['request']
            if not isinstance(command, dict) or command.get('action') not in {'list', 'update', 'delete', 'pause', 'resume',
                                                                             'feature_settings', 'events', 'emotion_status'}:
                raise ValueError()
            if command['action'] == 'resume':
                session = digest(['qq', data['bot_uin'], data['conversation_key']])
                with self.store.lock, self.store.db:
                    self.store.db.execute("UPDATE mia_astrbot_turns SET memory_suppressed=0 WHERE session_id=? "
                        "AND json_extract(scope_json,'$.owner')=?", (session, scope.owner))
                    self.store.set_paused(scope.owner, self.store.snapshot(scope.owner)['paused'])
                result = {'qq_memory_suppressed': False}
            else:
                result = await asyncio.to_thread(self.store.handle, scope.owner, command)
            await ledger.authorization.require(scope, 'memory:manage')
            return web.json_response(result)
        if action == 'prepare':
            return web.json_response(await self.prepare(data, ledger, identity))
        row = ledger.by_token(data['turn_token'], allow_expired=action == 'status')
        scope = await ledger.authorize(row)
        if action == 'status':
            if set(data) != {'protocol_version', 'turn_token'}:
                raise ValueError()
            with self.store.lock:
                job = self.store.db.execute('SELECT id,state,error_category FROM mia_jobs WHERE owner=? AND session_id=? '
                    'AND turn_id=?', (scope.owner, row['session_id'], row['turn_key'])).fetchone()
            return web.json_response({'turn_key': row['turn_key'], 'status': row['status'],
                'completion': json.loads(row['result_json']) if row['result_json'] else None,
                'memory_job': dict(job) if job else {'state': 'not_submitted'}})
        if row['payload_json']:
            models = await identity.config(scope)
            if digest(models.get('prompt', '')) != json.loads(row['payload_json'])['role_hash']:
                raise ContextError('context_changed')
        if action == 'complete':
            if set(data) != {'protocol_version', 'turn_token', 'completion_id', 'result', 'final_text',
                             'message_components', 'agent_tool_summary'} or data['result'] not in {'adapter_accepted', 'failed'}:
                raise ValueError()
            identifier(data['completion_id'])
            for name in ('message_components', 'agent_tool_summary'):
                if not isinstance(data[name], list) or len(data[name]) > 64:
                    raise ValueError()
                for item in data[name]:
                    identifier(item)
            if self.checked(data['final_text']) != data['final_text']:
                raise ValueError()
            return web.json_response(await ledger.complete(row, scope, data))
        if row['status'] not in {'prepared', 'sending'}:
            raise TurnConflict('turn_not_active')
        payload = json.loads(row['payload_json'])
        with self.store.lock:
            ledger.validate(scope, payload)
        if action in {'check', 'claim'}:
            required = {'protocol_version', 'turn_token', 'final_text'} | ({'completion_id'} if action == 'claim' else set())
            if set(data) != required:
                raise ValueError()
            checked = self.checked(data['final_text'])
            if action == 'claim':
                identifier(data['completion_id'])
                ledger.claim(row, scope, data['completion_id'], checked)
            return web.json_response({'checked_text': checked, 'replaced': checked != data['final_text']})
        if set(data) != {'protocol_version', 'turn_token', 'text'}:
            raise ValueError()
        from .astrbot_media import synthesize
        text = self.checked(data['text'])
        if not text.strip() or len(text) > 1500:
            raise ValueError()
        async with self.media_lock:
            audio = await synthesize(models, text)
        await ledger.authorization.require(scope, 'memory:read')
        with self.store.lock:
            ledger.validate(scope, payload)
        return web.Response(body=audio, content_type='audio/wav', headers={'Cache-Control': 'no-store'})

    @staticmethod
    def checked(text):
        if not isinstance(text, str) or len(text) > 16000:
            raise ValueError()
        return PersonaAgent().checked_text(text)

    async def prepare(self, data, ledger, identity):
        key = self.prepare_key(data)
        scope = await identity.resolve(data)
        previous = ledger.find(key)
        if previous:
            await ledger.authorize(previous)
            if (previous['scope_json'] != canonical(scope.snapshot()) or previous['request_hash'] != digest(data)
                    or previous['status'] != 'prepared'):
                raise TurnConflict('duplicate_turn')
            payload = json.loads(previous['payload_json'])
            with self.store.lock:
                ledger.validate(scope, payload)
            models = await identity.config(scope)
            if digest(models.get('prompt', '')) != payload['role_hash']:
                raise ContextError('context_changed')
            return self.prepared(previous, payload)
        models = await identity.config(scope)
        profile = self.bridge['models'].get(data['model'])
        if profile is None:
            raise TurnConflict('model_capacity_unverified', 409)
        limits = ContextLimits(data['model'], profile['context_window'], profile['response_tokens'],
            profile['capacity_source'], min(profile['input_tokens'], profile['context_window'] - profile['response_tokens'])
            - data['external_input_bytes'])
        limits.input_limit()
        session = digest(['qq', data['bot_uin'], data['conversation_key']])
        with self.store.lock:
            row = self.store.db.execute("SELECT memory_suppressed FROM mia_astrbot_turns WHERE session_id=? "
                "AND json_extract(scope_json,'$.owner')=? ORDER BY rowid DESC LIMIT 1", (session, scope.owner)).fetchone()
        suppressed = bool(row[0]) if row else False
        notice, skip_memory, priority = None, False, False
        if data['origin'] == 'user' and data['source_kind'] in {'text', 'asr'}:
            command = data['query_text'].strip().rstrip('。.!！ ')
            if command in {'这段别记', '这段不要记', '米娅，这段别记'}:
                await ledger.authorization.require(scope, 'memory:write')
                self.store.set_paused(scope.owner, self.store.snapshot(scope.owner)['paused'])
                suppressed = True
                notice = 'QQ当前私聊暂停提炼新记忆；用 /youmi_memory resume 明确恢复。原App自动保存开关不变，已有事实未删除。'
            elif command in {'忘掉这件事', '米娅，忘掉这件事'}:
                notice, skip_memory = '未确定要删除的记忆；请用户选择，尚未删除。', True
            else:
                match = re.fullmatch(r'(?:米娅[，, ]*)?忘掉关于(.{1,100})的记忆', command)
                if match:
                    await ledger.authorization.require(scope, 'memory:manage')
                    records = [item for item in self.store.list_memories(scope.owner) if match[1].strip() in item['content']]
                    if len(records) == 1:
                        self.store.delete_memory(scope.owner, records[0]['id'])
                        notice = '已删除用户指定的记忆及关联性格依据。'
                    else:
                        notice = '无法唯一确定待删除记忆；请在记忆设置选择，尚未删除。'
                    skip_memory = True
                priority = bool(re.match(r'^(?:你好[，, ]*)?(?:米娅[，, ]*)?(?:请|帮我)?记住(?!了吗|了没|没|吗|\?|？).+', command))
        from .affect import CONTROL, CONTROL_REPLY
        if CONTROL.search(data['query_text']):
            notice, skip_memory = CONTROL_REPLY, True
        user_evidence = (data['origin'] == 'user' and data['source_kind'] in {'text', 'asr'}
                         and not suppressed and not skip_memory)
        bundle = await self.builder.build(scope, ledger.authorization, key, data['query_text'],
            limits=limits, role=models.get('prompt', ''), session_id=session if user_evidence else None)
        proposal, affect_status = None, 'not_applicable'
        inputs = json.loads(bundle.inputs_json)
        current = inputs.get('youmi_affect')
        if current and user_evidence:
            from .affect import parse_appraisal, request_appraisal
            try:
                from core.utils.llm import create_instance
                from .model_requests import model_requests
                selected = models['selected_module']['LLM']
                config = models['LLM'][selected]
                def appraise():
                    # Share the existing bounded native model slots; the native
                    # thread owns its provider/client through timeout cleanup.
                    with model_requests.acquire(foreground=True, timeout=1):
                        provider = create_instance(config.get('type', selected), config)
                        try:
                            return request_appraisal(provider, data['query_text'], current)
                        finally:
                            close = getattr(getattr(provider, 'client', None), 'close', None)
                            if callable(close):
                                close()
                raw = await asyncio.wait_for(asyncio.to_thread(appraise), 7)
                proposal = parse_appraisal(raw, data['query_text'])
                bundle = await self.builder.with_affect(bundle, ledger.authorization, proposal)
                affect_status = 'projected'
            except (ContextError, PrivateAuthorizationRequired):
                raise
            except Exception:
                proposal, affect_status = None, 'unavailable'
        await self.builder.validate(bundle, ledger.authorization)
        if bundle.snapshot is None:
            raise TurnConflict('context_snapshot_unavailable', 503)
        messages = bundle.request()['messages']
        dynamic = json.loads(messages[1]['content'])['youmi_context']
        dynamic.update(input_origin=data['origin'], source_kind=data['source_kind'], user_evidence=user_evidence)
        if notice:
            dynamic['memory_action_notice'] = notice
        from .features import settings
        with self.store.lock:
            disabled = not settings(self.store, scope.owner)['memory_recall']
        dynamic['memory_status'] = ('disabled' if disabled else 'unavailable' if dynamic['memory_status'] == 'unavailable'
                                    else 'matched' if dynamic['facts'] else 'no_match')
        system = messages[0]['content']
        projected_affect = json.loads(bundle.inputs_json).get('youmi_affect')
        if projected_affect:
            from .affect import guidance
            expression = guidance(projected_affect)
            if system.endswith(expression):
                system = system[:-len(expression)]
                dynamic['affect_expression_guidance'] = expression
        if data['origin'] == 'scheduled':
            system += '\n本轮由定时任务主动触发，不是用户新发言；不要声称用户刚说过任务提示。'
        payload = {'system_prompt': system, 'dynamic_context': dynamic,
            'persona_version': bundle.persona_version, 'snapshot': bundle.snapshot,
            'session': session, 'origin': data['origin'], 'source_kind': data['source_kind'],
            'suppressed': suppressed, 'skip_memory': skip_memory, 'priority': priority,
            'excerpt': data['query_text'][:512] if data['origin'] == 'user' and not SECRET.search(data['query_text']) else '',
            'proposal': proposal, 'affect_version': current['version'] if current else None,
            'affect_status': affect_status, 'role_hash': digest(models.get('prompt', '')),
            'pending_events': [item['id'] for item in bundle.diagnostics()['events'] if item['state'] == 'pending']}
        row = ledger.create(key, data, scope, payload)
        if row['attempt_id'] != data['attempt_id']:
            raise TurnConflict('duplicate_turn')
        return self.prepared(row, payload)

    @staticmethod
    def prepared(row, payload):
        return {key: payload[key] for key in ('system_prompt', 'dynamic_context', 'persona_version', 'affect_status')} | {
            'protocol_version': 1, 'turn_key': row['turn_key'], 'turn_token': row['token'],
            'status': row['status'], 'expires_at': row['expires_at']}

    async def prune_forever(self):
        while True:
            store = self.store or (_runtime[0] if _runtime else None)
            if store is not None:
                try:
                    await asyncio.to_thread(TurnLedger(store, None, None, None).prune)
                except sqlite3.Error:
                    pass
            await asyncio.sleep(30)


def register_routes(app, config):
    path = os.environ.get('YOUMI_ASTRBOT_CONFIG_FILE')
    if not path:
        return
    bridge = json.loads(Path(path).read_text(encoding='utf-8'))
    handler = AstrBotHandler(config, bridge)
    app.router.add_route('*', '/internal/astrbot/{action}', handler.handle)
    async def cleanup(application):
        task = asyncio.create_task(handler.prune_forever())
        yield
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    app.cleanup_ctx.append(cleanup)
