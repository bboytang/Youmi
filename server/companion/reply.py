"""The existing connection's single provider path, with scoped turn snapshots."""

import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, replace
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading

from .authorization import AuthenticatedScope, PrivateAuthorizationRequired, require_connection
from .context import ContextBuilder, ContextError, ContextLimits, packed
from .embedding import DIMENSION, MODEL_KEY, REVISION, EmbeddingUnavailable, SemanticEncoder
from .semantic import SemanticMemoryIndex
from .model_requests import model_requests


class OfflineEncoder:
    model_id, revision, dimension = MODEL_KEY, REVISION, DIMENSION

    def __init__(self):
        self.lock = threading.Lock()
        self.encoder = None

    def encode(self, text, query=False):
        with self.lock:
            if self.encoder is None:
                directory = os.environ.get('MIA_EMBEDDING_DIR')
                if not directory:
                    raise EmbeddingUnavailable('embedding_assets_unconfigured')
                self.encoder = SemanticEncoder(directory)
        return self.encoder.encode(text, query)


class UnavailableIndex:
    @property
    def store(self):
        raise sqlite3.OperationalError('Context database unavailable')

    async def _require(self, scope, authorization, permission):
        if not isinstance(scope, AuthenticatedScope) or scope.persona_id != 'mia':
            raise PrivateAuthorizationRequired()
        await authorization.require(scope, permission)

    async def retrieve(self, *args):
        raise EmbeddingUnavailable('context_database_unavailable')


_encoder = OfflineEncoder()
_builders = {}
_lock = threading.Lock()

DEFAULT_CONTEXT_WINDOW = 32768
DEFAULT_INPUT_TOKENS = 24576
DEFAULT_RESPONSE_TOKENS = 2048
DEFAULT_CAPACITY_SOURCE = ('conservative fallback: model capacity not catalogued; '
                           '32K window, 24K estimated input and 2048 response budget')


@dataclass(frozen=True)
class ModelCapacity:
    context_window: int
    input_tokens: int
    capacity_source: str


MODEL_CAPACITY = {
    'glm-4-flash': ModelCapacity(
        32768, 24576,
        'https://docs.bigmodel.cn/cn/guide/models/free/glm-4-flash-250414 documents 128K context; '
        'conservative personal 32K window, 24K estimated input and 2048 response budget'),
    'doubao-seed-2-0-lite': ModelCapacity(
        32768, 24576,
        'https://ark.volcengine.com/region:cn-beijing/docs/model-list documents 256K context and '
        '128K max response (default 4K); conservative personal 32K window, 24K estimated input '
        'and 2048 response budget'),
    'doubao-seed-2-1-lite': ModelCapacity(
        32768, 24576,
        'https://ark.volcengine.com/region:cn-beijing/docs/model-list documents 1024K context and '
        '256K max response (default 4K); conservative personal 32K window, 24K estimated input '
        'and 2048 response budget'),
}


def _make_limits(model, context_window, response_tokens, capacity_source, input_tokens):
    if not isinstance(model, str) or not model.strip():
        model = 'unknown'
    if type(context_window) is not int or context_window <= 1:
        return None
    if type(response_tokens) is not int or response_tokens <= 0 or response_tokens >= context_window:
        response_tokens = min(DEFAULT_RESPONSE_TOKENS, max(1, context_window // 4))
    if type(input_tokens) is not int or input_tokens <= 0:
        return None
    if not isinstance(capacity_source, str) or not capacity_source.strip():
        return None
    try:
        limits = ContextLimits(model, context_window, response_tokens, capacity_source, input_tokens)
        limits.input_limit()
        return limits
    except ContextError:
        return None


def _configured_limits(config, model, base_url, response_tokens):
    if not isinstance(config, dict):
        return None
    configured_model = config.get('model_name')
    configured_url = config.get('base_url')
    if (isinstance(configured_model, str) and isinstance(model, str) and model.strip()
            and configured_model != model):
        return None
    if isinstance(configured_url, str) and base_url and configured_url.rstrip('/') != base_url:
        return None
    window = config.get('context_window')
    input_tokens = config.get('input_tokens', DEFAULT_INPUT_TOKENS)
    source = config.get('capacity_source')
    if not isinstance(source, str) or not source.strip():
        source = f'manager model config: context_window={window}'
    if type(response_tokens) is not int or response_tokens <= 0:
        try:
            response_tokens = int(config.get('max_tokens'))
        except (TypeError, ValueError):
            response_tokens = DEFAULT_RESPONSE_TOKENS
    return _make_limits(model, window, response_tokens, source, input_tokens)


def _reviewed_profile(provider, model, base_url, response_tokens):
    try:
        profile = json.loads(Path(os.environ['MIA_CONTEXT_PROFILE_FILE']).read_text(encoding='utf-8'))
        if (profile['provider_module'] != type(provider).__module__
                or profile['model'] != model
                or profile['base_url'].rstrip('/') != base_url):
            return None
        return _make_limits(model, profile['context_window'], response_tokens,
                            profile['capacity_source'], profile.get('input_tokens', 8192))
    except (OSError, KeyError, AttributeError, TypeError, ValueError):
        return None


def _catalog_limits(model, response_tokens):
    for name, entry in MODEL_CAPACITY.items():
        if model == name or model.startswith(name + '-'):
            return _make_limits(model, entry.context_window, response_tokens,
                                entry.capacity_source, entry.input_tokens)
    return None


def builder_for(store):
    if store is None:
        return ContextBuilder(UnavailableIndex())
    with _lock:
        if store not in _builders:
            _builders[store] = ContextBuilder(SemanticMemoryIndex(store, _encoder))
        return _builders[store]


def provider_limits(provider, config=None):
    """Resolve capacity without requiring a per-model profile.

    Precedence: explicit manager config -> reviewed profile / built-in catalog ->
    conservative default. Client/hello/listen fields are never consulted.
    """
    model = getattr(provider, 'model_name', None)
    if not isinstance(model, str) or not model.strip():
        model = 'unknown'
    base_url = getattr(provider, 'base_url', '') or ''
    if not isinstance(base_url, str):
        base_url = ''
    base_url = base_url.rstrip('/')
    response_tokens = getattr(provider, 'max_tokens', None)
    limits = _configured_limits(config, model, base_url, response_tokens)
    if limits is not None:
        return limits
    limits = _reviewed_profile(provider, model, base_url, response_tokens)
    if limits is not None:
        return limits
    limits = _catalog_limits(model, response_tokens)
    if limits is not None:
        return limits
    limits = _make_limits(model, DEFAULT_CONTEXT_WINDOW, response_tokens,
                          DEFAULT_CAPACITY_SOURCE, DEFAULT_INPUT_TOKENS)
    if limits is not None:
        return limits
    raise ContextError('context_limits_unverified')


def actual_messages(dialogue):
    # Deliberately bypass upstream's synthetic interrupted results and system
    # memory insertion. Display DTOs remain the existing authority, unchanged.
    result = []
    for message in list(dialogue.dialogue):
        if message.role == 'system' or getattr(message, 'is_temporary', False):
            continue
        value = {'role': message.role, 'content': message.content}
        if message.tool_calls is not None:
            value['tool_calls'] = message.tool_calls
        if message.tool_call_id is not None:
            value['tool_call_id'] = message.tool_call_id
        result.append((message.uniq_id, value))
    return result


@dataclass(frozen=True)
class ReplyTurn:
    builder: ContextBuilder
    bundle: object
    user_marker: str
    consumed_json: str


async def validate(conn, turn=None):
    authorization = getattr(conn, '_youmi_authorization', None)
    if authorization is None:
        raise PrivateAuthorizationRequired()
    turn = turn or getattr(conn, '_youmi_context_turn', None)
    if turn is None or turn.bundle.scope != getattr(conn, '_youmi_scope', None):
        raise ContextError('context_turn_unavailable')
    limits = await asyncio.to_thread(
        provider_limits, conn.llm, getattr(conn, '_youmi_llm_config', None))
    if limits != turn.bundle.limits:
        raise ContextError('context_limits_changed')
    # Context validation already checks current manager permission for this
    # exact Scope. Do it once per delivery, without caching across deltas.
    await turn.builder.validate(turn.bundle, authorization)
    if (turn.bundle.scope != getattr(conn, '_youmi_scope', None)
            or authorization is not getattr(conn, '_youmi_authorization', None)):
        raise ContextError('context_turn_unavailable')
    return turn


async def prepare(conn, functions, depth):
    await require_connection(conn)
    limits = await asyncio.to_thread(
        provider_limits, conn.llm, getattr(conn, '_youmi_llm_config', None))
    builder = conn._youmi_context_builder
    rows = actual_messages(conn.dialogue)
    turn = getattr(conn, '_youmi_context_turn', None)
    tools = functions or []
    if depth == 0:
        if not rows or rows[-1][1]['role'] != 'user':
            raise ContextError('context_turn_unavailable')
        marker, current = rows[-1]
        query = current['content']
        history = [value for _, value in rows[:-1]]
        # History may contain old model-derived facts. Only keep it when the
        # last generation's full snapshot can still be proved current.
        previous = getattr(conn, '_youmi_previous_context', None)
        if previous is None:
            history = []
        else:
            try:
                await validate(conn, previous)
            except ContextError:
                history = []
        bundle = await builder.build(conn._youmi_scope, conn._youmi_authorization, conn.sentence_id, query,
            limits=limits, history=history, role=getattr(conn, '_youmi_role_data', ''), tools=tools, session_id=conn.session_id)
        consumed = [current]
    else:
        if turn is None:
            raise ContextError('context_turn_unavailable')
        marker, query = turn.user_marker, json.loads(turn.bundle.inputs_json)['query']
        try:
            await validate(conn, turn)
        except ContextError as error:
            if str(error) not in {'context_changed', 'context_snapshot_unavailable'}:
                raise
            # Re-query current facts, discard ALL old model/tool-derived data.
            # Already executed side effects are never replayed on a rebuild.
            bundle = await builder.build(conn._youmi_scope, conn._youmi_authorization, conn.sentence_id, query,
                limits=limits, role=getattr(conn, '_youmi_role_data', ''), tools=[],
                scene='上下文已变化；旧工具结果已失效，本轮不再执行工具。需要新的工具操作时请用户重新发起。', session_id=conn.session_id)
            consumed = []
            marker = None
        else:
            start = next((i for i, (key, _) in enumerate(rows) if key == marker), None)
            if start is None:
                raise ContextError('context_turn_unavailable')
            current = [value for _, value in rows[start:]]
            consumed = json.loads(turn.consumed_json)
            if current[:len(consumed)] != consumed or not current[len(consumed):]:
                raise ContextError('context_turn_modified')
            bundle = await builder.continue_turn(turn.bundle, conn._youmi_authorization,
                current[len(consumed):], tools=tools)
            consumed = current
    result = ReplyTurn(builder, bundle, marker, packed(consumed))
    await validate(conn, result)
    conn._youmi_context_turn = result
    conn._youmi_context_status = {'status': 'ready', 'turn_id': bundle.turn_id, **bundle.diagnostics()}
    return result


def wait(conn, operation):
    # chat() already runs in the existing executor; never call on the event loop.
    future = asyncio.run_coroutine_threadsafe(operation, conn.loop)
    try:
        return future.result(timeout=12)
    except FutureTimeout:
        future.cancel()
        raise ContextError('context_deadline_exceeded') from None


def response(conn, functions, depth):
    turn = wait(conn, prepare(conn, functions, depth))
    # One foreground lease covers the short appraisal and existing reply. A
    # failed appraisal neither writes a state nor prevents normal speech.
    def guarded():
        lease = model_requests.acquire(foreground=True, timeout=10,
            cancelled=lambda: getattr(conn, 'client_abort', False))
        stream, active = None, turn
        try:
            wait(conn, validate(conn, active))
            inputs = json.loads(active.bundle.inputs_json)
            if inputs.get('youmi_affect') and depth == 0:
                from .affect import CONTROL, CONTROL_REPLY, request_appraisal, parse_appraisal
                conn._youmi_affect_pending = None
                if CONTROL.search(inputs['query']):
                    yield CONTROL_REPLY if functions is None else (CONTROL_REPLY, None)
                    return
                proposal = None
                try:
                    proposal = parse_appraisal(request_appraisal(conn.llm, inputs['query'], inputs['youmi_affect']), inputs['query'])
                    if 'changes' in proposal:
                        proposal['system_version'] = inputs['youmi_affect']['affect_system']['version']
                    elif inputs['youmi_affect'].get('affect_system'):
                        raise ValueError('Expected emotional system appraisal')
                except Exception as error:
                    logging.getLogger(__name__).warning('Youmi affect appraisal retained previous state (%s)', type(error).__name__)
                wait(conn, validate(conn, active))
                if getattr(conn, '_youmi_context_turn', None) is not active:
                    raise ContextError('context_turn_superseded')
                if proposal:
                    bundle = wait(conn, active.builder.with_affect(active.bundle, conn._youmi_authorization, proposal))
                    if getattr(conn, '_youmi_context_turn', None) is not active:
                        raise ContextError('context_turn_superseded')
                    active = replace(active, bundle=bundle)
                    conn._youmi_context_turn = active
                    conn._youmi_context_status = {'status': 'ready', 'turn_id': bundle.turn_id, **bundle.diagnostics()}
                    conn._youmi_affect_pending = (active, proposal)
            request = active.bundle.request()
            tools = request.get('tools')
            stream = (conn.llm.response_with_functions(conn.session_id, request['messages'], functions=tools,
                max_tokens=request['max_tokens']) if tools else
                conn.llm.response(conn.session_id, request['messages'], max_tokens=request['max_tokens']))
            for delta in stream:
                if getattr(conn, '_youmi_context_turn', None) is not active:
                    raise ContextError('context_turn_superseded')
                if getattr(conn, 'client_abort', False):
                    return
                wait(conn, validate(conn, active))
                yield delta if tools or functions is None else (delta, None)
            wait(conn, validate(conn, active))
        finally:
            try:
                if hasattr(stream, 'close'):
                    stream.close()
            finally:
                lease.release()
    return guarded()


async def finish_affect(conn):
    pending = getattr(conn, '_youmi_affect_pending', None)
    if not pending or getattr(getattr(conn, '_youmi_memory', None), 'suppressed', False):
        return False
    turn, proposal = pending
    await validate(conn, turn)
    await conn._youmi_authorization.require(turn.bundle.scope, 'memory:write')
    inputs = json.loads(turn.bundle.inputs_json)
    if not inputs.get('session_id'):
        return False
    def finish():
        from .affect import commit
        store = turn.builder.index.store
        with store.lock, store.db:
            store.db.execute('BEGIN IMMEDIATE')
            if turn.builder._versions(turn.bundle.scope) != turn.bundle.snapshot:
                raise ContextError('context_changed')
            return commit(store, turn.bundle.scope.owner, inputs['session_id'], turn.bundle.turn_id,
                          inputs['query'], proposal, inputs['youmi_affect']['version'])
    return await asyncio.to_thread(finish)
