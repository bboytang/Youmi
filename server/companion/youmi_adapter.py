"""Youmi hooks for xiaozhi-esp32-server v0.9.7; no model or gateway replacement."""

import asyncio
from collections import deque
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import unicodedata
import uuid
from types import SimpleNamespace

from .memory import EXTRACTION_INSTRUCTIONS, MemoryError, MemoryStore, SECRET
from .authorization import AuthenticatedScope, PrivateAuthorizationRequired, require_connection
from .persona import PersonaAgent, GuardedTTSQueue, SAFE_REPLY
from .context import ContextError
from . import reply


logger = logging.getLogger(__name__)
_stores = {}
_stores_lock = threading.Lock()
EMOTIONS = {
    "😌": "calm", "😶": "calm", "🙂": "happy", "😂": "happy", "😆": "happy",
    "🤔": "curious", "😲": "surprised", "😱": "surprised", "🙄": "confused",
    "😍": "caring", "😘": "caring", "😔": "sad", "😭": "sad",
    "😜": "playful", "😉": "playful",
}


def _store():
    directory = Path(__file__).resolve().parent.parent / 'data'
    historical = directory / 'mia-memory.sqlite3'
    default = historical if historical.exists() else directory / 'youmi-memory.sqlite3'
    path = Path(os.environ.get('YOUMI_MEMORY_DB') or os.environ.get('MIA_MEMORY_DB') or default)
    with _stores_lock:
        if path not in _stores:
            _stores[path] = MemoryStore(path)
        return _stores[path]


def attach_private_config(conn, private_config):
    """Config lookup must already have verified the independent private grant."""
    conn._youmi_owner = None
    conn._youmi_llm_config = None
    if not conn.read_config_from_api or not isinstance(private_config, dict):
        return
    scope = getattr(conn, "_youmi_scope", None)
    if not isinstance(scope, AuthenticatedScope) or private_config.get("youmi_owner", private_config.get("mia_owner")) != scope.owner:
        return
    conn._youmi_owner = scope.owner
    selected = private_config.get("selected_module")
    models = private_config.get("LLM")
    if isinstance(selected, dict) and isinstance(models, dict):
        name = selected.get("LLM")
        config = models.get(name) if isinstance(name, str) else None
        if isinstance(config, dict):
            conn._youmi_llm_config = dict(config)


def initialize_memory(conn):
    conn._youmi_memory = None
    if not getattr(conn, "_youmi_owner", None):
        if conn.read_config_from_api:
            conn.memory = None
            return True
        return False
    try:
        conn.memory = YoumiMemoryProvider(_store(), conn._youmi_owner, conn.llm, conn.session_id,
                                       conn._youmi_scope, conn._youmi_authorization)
        conn._youmi_memory = conn.memory
        return True
    except Exception as error:
        logger.warning("Youmi memory initialization skipped (%s)", type(error).__name__)
        conn.memory = None
        return True


def apply_persona_prompt(conn):
    if not getattr(conn, "_youmi_scope", None):
        return
    prompt = conn.prompt or conn.config.get("prompt", "")
    conn.change_system_prompt(compose_persona(conn, prompt))


def persona_agent(conn):
    if not isinstance(getattr(conn, "_youmi_scope", None), AuthenticatedScope):
        return None
    agent = getattr(conn, "_youmi_persona", None)
    if agent is None:
        agent = conn._youmi_persona = PersonaAgent()
        conn.dialogue._youmi_persona = agent
    return agent


def compose_persona(conn, prompt):
    agent = persona_agent(conn)
    if agent and prompt != agent.last_prompt:
        conn._youmi_role_data = re.sub(r'<memory>.*?</memory>|</?memory>', '', prompt or '', flags=re.S)
    return agent.system_prompt(prompt) if agent else prompt


def uses_context(conn):
    return isinstance(getattr(conn, '_youmi_scope', None), AuthenticatedScope)


def context_response(conn, functions, depth):
    if not hasattr(conn, '_youmi_context_builder'):
        try:
            store = getattr(getattr(conn, '_youmi_memory', None), 'store', None) or _store()
        except Exception:
            store = None
        conn._youmi_context_builder = reply.builder_for(store)
    return reply.response(conn, functions, depth)


def context_failure(conn, error):
    incomplete_turn()
    from .authorization import allow_generation, allow_output
    if str(error) == 'context_turn_superseded':
        return
    conn._youmi_context_turn = None
    conn._youmi_previous_context = None
    if not allow_generation(conn):
        return
    code = str(error) if isinstance(error, ContextError) else 'authorization_required'
    logger.warning("Youmi context rejected (%s)", code)
    notice = conn._youmi_context_status = {'status': 'limited', 'code': code, 'turn_id': conn.sentence_id}
    async def status():
        if await allow_output(conn):
            await conn.websocket.send(json.dumps({'type': 'youmi_context', **notice}))
    reply.wait(conn, status())
    text = ('😌当前对话容量尚未确认，请稍后再试。' if code == 'context_limits_unverified' else
            '😌本轮上下文已变化或无法安全处理，请重新发起问题；已执行的操作不会自动重试。')
    _complete_checked_reply(conn, text, send_text=True)


def attach_output_guard(conn):
    agent = persona_agent(conn)
    if agent:
        apply_persona_prompt(conn)
    if agent and not isinstance(conn.tts.tts_text_queue, GuardedTTSQueue):
        conn.tts.tts_text_queue = GuardedTTSQueue(conn.tts.tts_text_queue, conn, agent)


def guard_history(dialogue, message):
    agent = getattr(dialogue, "_youmi_persona", None)
    if agent and message.role == "assistant" and isinstance(message.content, str):
        message.content = agent.checked_text(message.content)
    frame = getattr(_completion, 'frame', None)
    if frame is not None and message.role == 'assistant' and isinstance(message.content, str):
        frame['assistant'] = (frame.get('assistant', '') + message.content)[-512:]


def store_tts_text(conn, sentence_id, text):
    agent = persona_agent(conn)
    conn.tts.store_tts_text(sentence_id, agent.checked_text(text) if agent else text)


def blocked_input_reply(conn, depth):
    agent = persona_agent(conn)
    if depth != 0 or not agent or not agent.input_decision.blocked:
        return False
    incomplete_turn()
    _complete_checked_reply(conn, SAFE_REPLY, send_text=True)
    return True


def output_was_blocked(conn):
    return bool(getattr(conn, '_youmi_output_replacement', None))


def finish_blocked_output(conn):
    replacement = getattr(conn, '_youmi_output_replacement', None)
    if not replacement:
        return False
    incomplete_turn()
    _complete_checked_reply(conn, replacement, send_text=False)
    return True


def _complete_checked_reply(conn, text, send_text):
    from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType
    from core.utils.dialogue import Message
    if send_text:
        conn.tts.tts_text_queue.put(TTSMessageDTO(conn.sentence_id, SentenceType.MIDDLE, ContentType.TEXT, text))
    store_tts_text(conn, conn.sentence_id, text)
    conn.dialogue.put(Message(role="assistant", content=text))
    checkpoint(conn)
    conn.tts.tts_text_queue.put(TTSMessageDTO(conn.sentence_id, SentenceType.LAST, ContentType.ACTION))


def _user_excerpts(messages):
    result = []
    for message in messages:
        if getattr(message, "role", None) != "user" or getattr(message, "is_temporary", False):
            continue
        content = getattr(message, "content", None)
        if not isinstance(content, str):
            continue
        try:
            parsed = json.loads(content) if content.startswith("{") else None
            if isinstance(parsed, dict) and isinstance(parsed.get("content"), str):
                content = parsed["content"]
        except (ValueError, RecursionError):
            pass
        content = unicodedata.normalize("NFKC", content).strip()
        if not content or SECRET.search(content):
            continue
        result.append((getattr(message, "uniq_id", None) or str(id(message)), content[:512]))
    return result


def bounded_history(messages):
    starts = [i for i, message in enumerate(messages) if getattr(message, "role", None) == "user"]
    return messages[starts[-20]:] if len(starts) > 20 else messages


class YoumiMemoryProvider:
    """Implements the upstream memory provider protocol using the configured LLM."""

    def __init__(self, store, owner, llm, session_id, scope, authorization):
        if not isinstance(scope, AuthenticatedScope) or scope.owner != owner:
            raise PrivateAuthorizationRequired()
        self.store, self.owner, self.llm, self.session_id = store, owner, llm, session_id
        self.scope, self.authorization = scope, authorization
        self.lock = threading.RLock()
        self.pending = deque(maxlen=20)
        self.seen = deque(maxlen=80)
        self.saving = False
        self.final_requested = False
        self.suppressed = False
        self.notice = None
        self.force_checkpoint = False
        self.revision = store.snapshot(owner)["revision"]

    def set_llm(self, llm):
        self.llm = llm

    async def query_memory(self, query):
        await self.authorization.require(self.scope, "memory:read")
        try:
            def context():
                with self.lock:
                    return {"youmi_context": {
                        "memories": self.store.retrieve(self.owner, query[:4096]),
                        "personality": self.store.personality(self.owner),
                        "memory_status": self.notice,
                        "session_memory_paused": self.suppressed,
                    }}
            result = await asyncio.to_thread(context)
            await self.authorization.require(self.scope, "memory:read")
            return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        except PrivateAuthorizationRequired:
            raise
        except Exception as error:
            logger.warning("Youmi memory lookup skipped (%s)", type(error).__name__)
            return ""

    def _take_batch(self, excerpts, final):
        with self.lock:
            state = self.store.snapshot(self.owner)
            if state["revision"] != self.revision:
                self.pending.clear()
                self.seen = deque((marker for marker, _ in excerpts), maxlen=80)
                self.revision = state["revision"]
                return None
            for marker, content in excerpts:
                if marker not in self.seen:
                    self.seen.append(marker)
                    if not self.suppressed:
                        self.pending.append(content)
            self.final_requested |= final or self.force_checkpoint
            self.force_checkpoint = False
            if self.saving or not self.pending or (len(self.pending) < 10 and not self.final_requested):
                return None
            if state["paused"] or self.suppressed:
                self.pending.clear()
                return None
            sources = list(self.pending)
            self.pending.clear()
            self.saving = True
            self.final_requested = False
            return sources, state["revision"], uuid.uuid4().hex

    async def checkpoint(self, excerpts, final=False):
        try:
            await self.authorization.require(self.scope, "memory:write")
        except PrivateAuthorizationRequired:
            with self.lock:
                self.pending.clear()
            return
        batch = await asyncio.to_thread(self._take_batch, excerpts, final)
        if batch is None:
            return
        sources, revision, checkpoint_id = batch
        deferred_retry = False
        try:
            def extract():
                context = {"user_excerpts": sources,
                           "existing_memories": self.store.retrieve(self.owner, " ".join(sources)[:4096]),
                           "personality": self.store.personality(self.owner)}
                output = self.llm.response_no_stream(EXTRACTION_INSTRUCTIONS,
                    json.dumps(context, ensure_ascii=False))
                return output
            output = await asyncio.to_thread(extract)
            await self.authorization.require(self.scope, "memory:write")
            await asyncio.to_thread(self.store.apply_extraction, self.owner, self.session_id,
                                    revision, sources, output, checkpoint_id)
        except PrivateAuthorizationRequired:
            with self.lock:
                self.pending.clear()
        except Exception as error:
            logger.warning("Youmi memory extraction skipped (%s)", type(error).__name__)
            with self.lock:
                state = self.store.snapshot(self.owner)
                if state["revision"] == revision and not state["paused"] and not self.suppressed:
                    self.pending = deque(sources + list(self.pending), maxlen=20)
                    deferred_retry = True
        finally:
            with self.lock:
                self.saving = False
                repeat = not deferred_retry and bool(self.pending) and (self.final_requested or len(self.pending) >= 10)
            if repeat:
                await self.checkpoint([])

    async def save_memory(self, messages, session_id=None):
        await self.checkpoint(_user_excerpts(list(messages)), final=True)

    def manage(self, request, messages):
        with self.lock:
            result = self.store.handle(self.owner, request)
            if request["action"] not in {"list", "personality", "feature_settings", "events", "emotion_status", "observations"}:
                self.pending.clear()
                self.seen = deque((marker for marker, _ in _user_excerpts(messages)), maxlen=80)
                self.revision = self.store.snapshot(self.owner)["revision"]
            return result


def _trim_dialogue(conn, clear=False):
    messages = conn.dialogue.dialogue
    prefix = [m for m in messages if m.role == "system" or getattr(m, "is_temporary", False)]
    actual = [m for m in messages if m.role != "system" and not getattr(m, "is_temporary", False)]
    messages[:] = prefix + ([] if clear else bounded_history(actual))


def begin_turn(conn, query):
    conn._youmi_previous_context = getattr(conn, '_youmi_context_turn', None)
    conn._youmi_context_turn = None
    conn._youmi_job_status = None
    conn._youmi_affect_pending = None
    conn._youmi_emotion = ("calm", 0.5)
    conn._youmi_output_replacement = None
    agent = persona_agent(conn)
    if agent:
        agent.begin_turn(query)
    provider = getattr(conn, "_youmi_memory", None)
    if not provider or not isinstance(query, str):
        return
    command = query.strip().rstrip("。.!！ ")
    with provider.lock:
        provider.force_checkpoint = False
        try:
            state = provider.store.snapshot(provider.owner)
        except sqlite3.Error:
            provider.notice = 'memory_unavailable'
            return
        if state["revision"] != provider.revision:
            provider.pending.clear()
            provider.seen = deque((marker for marker, _ in _user_excerpts(conn.dialogue.dialogue)), maxlen=80)
            provider.revision = state["revision"]
            _trim_dialogue(conn, clear=True)
        provider.notice = None
        if command in {"这段别记", "这段不要记", "米娅，这段别记"}:
            provider.suppressed = True
            provider.pending.clear()
            provider.store.set_paused(provider.owner, provider.store.snapshot(provider.owner)["paused"])
            provider.notice = "本会话不再提炼新记忆，下次聊天恢复；已有记忆未删除。"
        elif command in {"忘掉这件事", "米娅，忘掉这件事"}:
            provider.notice = "未确定要删除的记忆。询问具体内容，或请用户在记忆设置中选择；尚未删除。"
        elif re.match(r"^(?:你好[，, ]*)?(?:米娅[，, ]*)?(?:请|帮我)?记住(?!了吗|了没|没|吗|\?|？).+", command):
            provider.force_checkpoint = True
            provider.notice = "用户明确要求记忆；将在本轮结束提炼，尚未保存，不可声称已完成。"
        else:
            match = re.fullmatch(r"(?:米娅[，, ]*)?忘掉关于(.{1,100})的记忆", command)
            if match:
                topic = match[1].strip()
                records = [record for record in provider.store.list_memories(provider.owner)
                           if topic in record["content"]]
                if len(records) == 1:
                    provider.manage({"action": "delete", "id": records[0]["id"]}, conn.dialogue.dialogue)
                    _trim_dialogue(conn, clear=True)
                    provider.notice = "已删除用户指定的记忆及关联性格依据。"
                else:
                    provider.notice = "无法唯一确定待删除记忆；请在记忆设置选择，尚未删除。"

    frame = getattr(_completion, 'frame', None)
    if frame is not None and provider and not provider.suppressed and isinstance(query, str):
        from .jobs import TurnCompleted
        sources = _user_excerpts([SimpleNamespace(role='user', content=query, uniq_id=conn.sentence_id)])
        if sources:
            frame['event'] = TurnCompleted(provider.scope, conn.session_id, conn.sentence_id,
                conn.sentence_id, sources[0][1], provider.revision, provider.force_checkpoint)


def checkpoint(conn):
    if uses_context(conn):
        # The outer TurnCompleted hook is the sole admission path, including
        # direct answers. Disconnect never resubmits a mutable display history.
        _trim_dialogue(conn)
        return
    provider = getattr(conn, "_youmi_memory", None)
    if not provider:
        return
    excerpts = _user_excerpts(list(conn.dialogue.dialogue))
    _trim_dialogue(conn)
    try:
        asyncio.run_coroutine_threadsafe(provider.checkpoint(excerpts), conn.loop)
    except RuntimeError:
        logger.warning("Youmi memory checkpoint skipped: event loop closed")


async def handle_message(conn, message):
    if not isinstance(message, str) or len(message) > 16384:
        return False
    try:
        request = json.loads(message)
    except (ValueError, RecursionError):
        return False
    if not isinstance(request, dict) or request.get("type") != "mia_memory":
        return False
    response = {"type": "mia_memory", "request_id": request.get("request_id")}
    try:
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or len(request_id) != 36 or str(uuid.UUID(request_id)) != request_id.lower():
            raise MemoryError("invalid_input", "Invalid request_id")
        # Upstream sends hello before its background configuration/components finish.
        for _ in range(100):
            provider = getattr(conn, "_youmi_memory", None)
            if (provider or not conn.read_config_from_api or getattr(conn, "need_bind", False)
                    or getattr(conn, "_youmi_owner", True) is None):
                break
            await asyncio.sleep(0.05)
        provider = getattr(conn, "_youmi_memory", None)
        if not provider:
            raise MemoryError("unavailable", "Persistent memory is not available on this server")
        await require_connection(conn, "memory:manage")
        scope = conn._youmi_scope
        for field in ("owner", "agent_id", "persona_id", "device_id", "client_id"):
            if field in request and request[field] != getattr(scope, field):
                raise MemoryError("not_authorized", "Requested memory scope is not authorized")
        if "user_id" in request and (type(request["user_id"]) not in {str, int}
                                     or str(request["user_id"]) != str(scope.user_id)):
            raise MemoryError("not_authorized", "Requested memory scope is not authorized")
        if request.get('action') == 'job_status':
            def status():
                with provider.store.lock:
                    columns = 'SELECT id,state,attempts,error_category,validated_result FROM mia_jobs WHERE owner=?'
                    if 'id' in request:
                        row = provider.store.db.execute(columns + ' AND id=?', (scope.owner, request['id'])).fetchone()
                    else:
                        row = provider.store.db.execute(columns + ' ORDER BY created_at DESC,rowid DESC LIMIT 1', (scope.owner,)).fetchone()
                    if row is None:
                        if 'id' not in request:
                            return {'state': 'none', 'confirmation_required': False}
                        raise MemoryError('not_found', 'Task does not exist')
                    result = dict(row)
                    summary = result.pop('validated_result')
                    result['confirmation_required'] = bool(result['state'] == 'committed' and summary
                        and json.loads(summary).get('confirmation_required'))
                    return result
            response['result'] = await asyncio.to_thread(status)
        else:
            response["result"] = await asyncio.to_thread(provider.manage, request, list(conn.dialogue.dialogue))
        await require_connection(conn, "memory:manage")
        if request["action"] not in {"list", "personality", "feature_settings", "job_status", "events", "emotion_status", "observations"}:
            _trim_dialogue(conn, clear=True)
    except PrivateAuthorizationRequired:
        response.pop("result", None)
        response["error"] = {"code": "authorization_required", "message": "请用智控台授权码完成授权"}
    except MemoryError as error:
        response["error"] = {"code": error.code, "message": str(error)}
    except (ValueError, TypeError, AttributeError):
        response["error"] = {"code": "invalid_input", "message": "Invalid memory request"}
    except Exception as error:
        logger.warning("Youmi memory management failed (%s)", type(error).__name__)
        response["error"] = {"code": "unavailable", "message": "Memory is temporarily unavailable"}
    await conn.websocket.send(json.dumps(response, ensure_ascii=False))
    return True


def _emotion(text):
    if not isinstance(text, str):
        return None
    for i, character in enumerate(text[:256]):
        if character in EMOTIONS:
            count = 1
            while count < 3 and text[i:i + count + 1] == character * (count + 1):
                count += 1
            return EMOTIONS[character], (0.35, 0.65, 0.9)[count - 1]
    return None


def remember_emotion(conn, text):
    if getattr(conn, "_youmi_scope", None) or getattr(conn, "_youmi_memory", None):
        expression = _emotion(text)
        if expression:
            conn._youmi_emotion = expression


def tts_emotion(conn, text):
    if not getattr(conn, "_youmi_scope", None) and not getattr(conn, "_youmi_memory", None):
        return {}
    expression = _emotion(text) or getattr(conn, "_youmi_emotion", ("calm", 0.5))
    return {"emotion": expression[0], "intensity": expression[1]}


_completion = threading.local()


def incomplete_turn():
    frame = getattr(_completion, 'frame', None)
    if frame is not None:
        frame['limited'] = True


def completed_chat(function):
    """One outer completion covers recursion, direct_answer and terminal tools."""
    def chat(conn, query, depth=0):
        if depth:
            return function(conn, query, depth)
        worker = getattr(getattr(conn, 'server', None), '_youmi_worker', None)
        frame = {'event': None, 'limited': False}
        previous = getattr(_completion, 'frame', None)
        _completion.frame = frame
        if worker:
            worker.foreground += 1
        try:
            return function(conn, query, depth)
        except Exception:
            frame['limited'] = True
            raise
        finally:
            _completion.frame = previous
            if worker:
                worker.foreground -= 1
            event = frame['event']
            if (event or getattr(conn, '_youmi_affect_pending', None)) and not frame['limited'] and not getattr(conn, 'client_abort', False):
                async def accept():
                    from .authorization import allow_output
                    try:
                        if frame.get('assistant') and not output_was_blocked(conn):
                            await reply.finish_affect(conn)
                        turn = getattr(conn, '_youmi_context_turn', None)
                        if turn and any(item['state'] == 'pending' for item in turn.bundle.diagnostics()['events']):
                            activated = await turn.builder.complete(turn.bundle, conn._youmi_authorization, frame.get('assistant', ''))
                            conn._youmi_event_status = {'activated': activated}
                        result = await worker.submit(event) if worker and event else {'status': 'unavailable', 'accepted': False}
                    except PrivateAuthorizationRequired:
                        result = {'status': 'cancelled', 'accepted': False}
                    except Exception:
                        result = {'status': 'unavailable', 'accepted': False}
                    if event is None:
                        return
                    conn._youmi_job_status = result
                    if await allow_output(conn):
                        await conn.websocket.send(json.dumps({'type': 'mia_memory_job', 'turn_id': event.turn_id, **result}))
                try:
                    reply.wait(conn, accept())
                except Exception as error:
                    logger.warning('Youmi completion admission unavailable (%s)', type(error).__name__)
    return chat


async def start_worker(server):
    from .authorization import PrivateAuthorization
    from .jobs import JobStore, MemoryWorker
    server._youmi_worker = None
    if not server.config.get('read_config_from_api', False):
        return
    authorization = PrivateAuthorization(server.config)
    store = await asyncio.to_thread(_store)
    async def resolve(scope):
        models = await authorization.worker_model(scope)
        selected = models['selected_module']['LLM']
        config = models['LLM'][selected]
        from .extraction import LLMMemoryExtractor
        def factory():
            from core.utils.llm import create_instance
            return create_instance(config.get('type', selected), config)
        return LLMMemoryExtractor(factory)
    worker = server._youmi_worker = MemoryWorker(JobStore(store), authorization, resolve, encoder=reply._encoder)
    await worker.start()
    from .astrbot_api import attach_runtime
    attach_runtime(store, worker)


async def stop_worker(server, timeout=20):
    worker = getattr(server, '_youmi_worker', None)
    if worker:
        return await worker.close(timeout=timeout)
    return True


async def stop_service(server, gc_manager, tasks, timeout=20):
    """One application cleanup budget, retaining unfinished durable jobs.

    Leave a small part of that budget for cancellation and app/event-loop exit.
    This bounds the cleanup await; full-process/audio exit is verified separately.
    """
    loop = asyncio.get_running_loop()
    full_deadline = loop.time() + max(0, timeout)
    deadline = full_deadline - min(.5, max(0, timeout) / 2)
    worker = getattr(server, '_youmi_worker', None)
    if worker:
        # Stop completion admission before cancelling foreground services.
        worker.closing = True
        worker.wake.set()
    services = {task for task in tasks if task is not None}
    for task in services:
        task.cancel()
    stops = {asyncio.create_task(gc_manager.stop())}
    worker_stop = asyncio.create_task(stop_worker(server, timeout=max(0, deadline - loop.time())))
    stops.add(worker_stop)
    tracked = services | stops

    def observe(task):
        if not task.cancelled():
            error = task.exception()
            if error:
                logger.warning('Youmi service cleanup failed (%s)', type(error).__name__)

    for task in tracked:
        task.add_done_callback(observe)
    pending = tracked
    try:
        done, pending = await asyncio.wait(tracked, timeout=max(0, deadline - loop.time()))
        clean = not pending and all(task.cancelled() or task.exception() is None for task in done)
        return clean and worker_stop in done and not worker_stop.cancelled() and worker_stop.result()
    finally:
        remaining = set(pending)
        for task in pending:
            task.cancel()
        if worker and worker.task and not worker.task.done():
            worker.task.cancel()
            worker.task.add_done_callback(observe)
            remaining.add(worker.task)
        # Cancellation of nested waits needs more than one event-loop turn.
        # Join only within the reserve of the original deadline, never a second
        # per-task timeout or an uncooperative model/native thread.
        if remaining:
            await asyncio.wait(remaining, timeout=max(0, full_deadline - loop.time()))
