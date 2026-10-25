"""Opt-in isolated CI spans; never installed in the running companion package."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import threading
import time


class StageTrace:
    def __init__(self, sample, *, clock=time.perf_counter, capacity=8192):
        self.sample, self.clock, self.capacity = sample, clock, capacity
        self.lock = threading.Lock()
        self.parents = ContextVar('mia_p23_stage_parents', default=())
        self.events, self.restorations = [], []
        self.serial, self.dropped, self.enabled = 0, 0, True

    @contextmanager
    def span(self, name):
        if not self.enabled:
            yield
            return
        start, parents = self.clock(), self.parents.get()
        with self.lock:
            self.serial += 1
            identifier = self.serial
        token = self.parents.set((*parents, identifier))
        error = None
        try:
            yield
        except BaseException as failure:
            error = type(failure).__name__
            raise
        finally:
            end = self.clock()
            self.parents.reset(token)
            row = {'id': identifier, 'parent': parents[-1] if parents else None,
                'name': name, 'start': start, 'end': end, 'error_class': error}
            with self.lock:
                if len(self.events) < self.capacity:
                    self.events.append(row)
                else:
                    self.dropped += 1

    def wrap(self, owner, attribute, name):
        original = getattr(owner, attribute)
        if inspect.iscoroutinefunction(original):
            @wraps(original)
            async def observed(*args, **kwargs):
                with self.span(name):
                    return await original(*args, **kwargs)
        else:
            @wraps(original)
            def observed(*args, **kwargs):
                with self.span(name):
                    return original(*args, **kwargs)
        setattr(owner, attribute, observed)
        self.restorations.append((owner, attribute, original))

    def install(self, store):
        from youmi_companion import authorization, context, embedding, persona, reply, semantic
        targets = [
            (authorization.PrivateAuthorization, 'require', 'authorization.require'),
            (authorization.PrivateAuthorization, 'restore', 'authorization.restore'),
            (authorization.PrivateAuthorization, '_request', 'manager.request'),
            (authorization, 'allow_output', 'authorization.output_delivery'),
            (reply, 'provider_limits', 'provider.profile_read'),
            (reply, 'prepare', 'reply.prepare'), (reply, 'validate', 'reply.validate'),
            (reply, 'wait', 'reply.event_loop_wait'),
            (context.ContextBuilder, '_capture', 'context.capture'),
            (context.ContextBuilder, '_versions', 'context.versions'),
            (context.ContextBuilder, 'validate', 'context.validate'),
            (context.ContextBuilder, '_assemble', 'context.assemble'),
            (semantic.SemanticMemoryIndex, 'retrieve', 'retrieval.total'),
            (semantic.SemanticMemoryIndex, '_encode', 'retrieval.encode_wait_and_run'),
            (semantic.SemanticMemoryIndex, '_scan', 'retrieval.sqlite_scan'),
            (embedding.SemanticEncoder, 'encode', 'bge.native_encode'),
            (persona.GuardedTTSQueue, 'put', 'guard.queue_put'),
            (persona.GuardedTTSQueue, '_text', 'guard.checked_text_release'),
            (persona.PersonaGuardrail, 'inspect_output', 'guard.output_check')]
        for target in targets:
            self.wrap(*target)
        original = store.lock
        store.lock = ObservedLock(original, self)
        self.restorations.append((store, 'lock', original))
        original_db = store.db
        store.db = ObservedDB(original_db, self)
        self.restorations.append((store, 'db', original_db))

    def close(self):
        self.enabled = False
        for owner, attribute, original in reversed(self.restorations):
            setattr(owner, attribute, original)
        self.restorations.clear()

    def summary(self, until_ms=None):
        origin = self.sample['started']
        end = float('inf') if until_ms is None else origin + until_ms / 1000
        rows = []
        for event in self.events:
            # Pre-case setup shares the same origin with negative offsets, but
            # is excluded from the formal first-sentence interval below.
            start, finish = max(origin, event['start']), min(end, event['end'])
            if finish <= start:
                continue
            rows.append(dict(event, start=start, end=finish))
        by_name = {}
        for row in rows:
            children = sorted((max(row['start'], child['start']), min(row['end'], child['end']))
                for child in rows if child['parent'] == row['id'])
            covered, last = 0, row['start']
            for start, finish in children:
                if finish > max(start, last):
                    covered += finish - max(start, last)
                    last = finish
            value = by_name.setdefault(row['name'], {'calls': 0, 'inclusive_ms': 0, 'exclusive_ms': 0})
            value['calls'] += 1
            value['inclusive_ms'] += (row['end'] - row['start']) * 1000
            value['exclusive_ms'] += max(0, row['end'] - row['start'] - covered) * 1000
        return by_name

    def report(self):
        origin = self.sample['started']
        return {'origin': 'Same unchanged case-start monotonic clock; negative offsets are connection setup, excluded from case totals',
            'capacity': self.capacity, 'dropped': self.dropped,
            'scope': 'Nested inclusive/exclusive spans; concurrent calls overlap. Do not sum all categories or infer pure network latency.',
            'events': [dict(id=x['id'], parent=x['parent'], name=x['name'],
                start_ms=(x['start'] - origin) * 1000, end_ms=(x['end'] - origin) * 1000,
                error_class=x['error_class']) for x in sorted(self.events, key=lambda x: x['id'])],
            'before_tts_ready': self.summary(self.sample.get('first_sentence_ms', 0)),
            'whole_case': self.summary()}


class ObservedLock:
    def __init__(self, original, trace):
        self.original, self.trace = original, trace

    def __getattr__(self, name):
        return getattr(self.original, name)

    def acquire(self, *args, **kwargs):
        with self.trace.span('sqlite.python_lock_wait'):
            return self.original.acquire(*args, **kwargs)

    def release(self):
        return self.original.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *unused):
        self.release()


class ObservedDB:
    def __init__(self, original, trace):
        self.original, self.trace = original, trace

    def __getattr__(self, name):
        return getattr(self.original, name)

    def execute(self, sql, *args, **kwargs):
        if sql.lstrip().upper().startswith('BEGIN'):
            with self.trace.span('sqlite.begin_wait_and_execute'):
                return self.original.execute(sql, *args, **kwargs)
        return self.original.execute(sql, *args, **kwargs)

    def __enter__(self):
        self.original.__enter__()
        return self

    def __exit__(self, *args):
        return self.original.__exit__(*args)


def calibrate(iterations=500):
    """One fixed ABBA mechanism probe, no subtraction from measured latency."""
    def noop():
        return 1
    rows = []
    for enabled in (False, True, True, False):
        sample = {'started': time.perf_counter()}
        trace = StageTrace(sample, capacity=iterations)
        target = type('Target', (), {})()
        target.call = noop
        if enabled:
            trace.wrap(target, 'call', 'calibration.noop')
        start = time.perf_counter()
        for _ in range(iterations):
            target.call()
        elapsed = (time.perf_counter() - start) * 1000
        trace.close()
        rows.append({'enabled': enabled, 'calls': iterations, 'elapsed_ms': elapsed})
    return {'scope': 'Fixed empty-call ABBA probe only, not a real-path overhead bound or performance acceptance', 'arms': rows}


def controlled_provider(factory, model, report, sample):
    """Actual fixed SDK, four local HTTP exchanges at most, zero remote calls."""
    import httpx
    import json
    import openai
    provider = factory(model['type'], model)
    provider.client.close()
    text = '🙂可以带上水杯，按自己的节奏慢慢走，累了就休息一下。'
    chunks = [text[index:index + 2] for index in range(0, len(text), 2)]
    sample['controlled_output_chunks'] = chunks
    def transport(request):
        body = json.loads(request.content)
        assert report.get('controlled_sdk_calls', 0) < 4
        assert body['model'] == 'glm-4-flash-250414' and body['stream'] is True and not body.get('tools')
        report['controlled_sdk_calls'] = report.get('controlled_sdk_calls', 0) + 1
        sample['sdk_start_ms'] = (time.perf_counter() - sample['started']) * 1000
        frames = [{'id': 'controlled', 'object': 'chat.completion.chunk', 'created': 0,
            'model': body['model'], 'choices': [{'index': 0, 'finish_reason': None,
                'delta': {'content': chunk}}]} for chunk in chunks]
        frames.append({'id': 'controlled', 'object': 'chat.completion.chunk', 'created': 0,
            'model': body['model'], 'choices': [{'index': 0, 'finish_reason': 'stop', 'delta': {}}]})
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            content=(''.join('data: ' + json.dumps(frame, ensure_ascii=False) + '\n\n' for frame in frames)
                + 'data: [DONE]\n\n').encode())
    provider.client = openai.OpenAI(api_key='synthetic-controlled-only', base_url='https://controlled.invalid',
        max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    original = provider.response
    def response(*args, **kwargs):
        stream = original(*args, **kwargs)
        try:
            for delta in stream:
                if delta and 'sdk_first_content_ms' not in sample:
                    sample['sdk_first_content_ms'] = (time.perf_counter() - sample['started']) * 1000
                yield delta
        finally:
            stream.close()
    provider.response = response
    return provider
