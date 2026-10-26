"""V2 conservative reservations and observed SDK/HTTP entries, never payloads."""
import asyncio
import json
import threading

from .authorization import PrivateAuthorizationRequired
from .extraction import context_for
from .memory import MemoryError


_native_lock = threading.Lock()
_native = set()


def recover_requests(store):
    # A new Worker in the same process must not reinterpret a live native call
    # as a crashed process. This is also checked for queued, unreserved calls.
    with _native_lock:
        if any(path == store.db.execute('PRAGMA database_list').fetchone()['file'] for path, unused in _native):
            raise RuntimeError('V2 native call still running')
        with store.lock, store.db:
            store.db.execute("UPDATE mia_model_requests SET outcome='process_lost',native_finished_at=? "
                'WHERE native_finished_at IS NULL', (store.clock(),))


class RequestAccounting:
    def __init__(self, worker, job, context):
        self.worker, self.jobs, self.job, self.context = worker, worker.jobs, job, context
        self.store = self.jobs.store
        self.loop = asyncio.get_running_loop()
        self.ordinal = None
        self.key = (self.store.db.execute('PRAGMA database_list').fetchone()['file'], job['id'])

    def call(self, operation, *, cleanup=False):
        remaining = self.job['deadline'] - self.store.clock()
        future = asyncio.run_coroutine_threadsafe(operation, self.loop)
        try:
            return future.result(timeout=5 if cleanup else max(.001, remaining))
        except BaseException:
            future.cancel()
            raise

    def _eligible(self):
        current = self.jobs.current(self.job)
        state = self.store.db.execute('SELECT revision,paused FROM owners WHERE owner=?', (self.job['owner'],)).fetchone()
        if (self.context.request_cancelled.is_set() or current['state'] not in {'pending', 'extracting', 'validated', 'retry_wait'}
                or current['deadline'] <= self.store.clock() or state is None or state['paused']
                or state['revision'] != self.job['memory_revision']):
            raise PrivateAuthorizationRequired()
        expected = self.context
        actual = context_for(self.store, self.job['owner'],
            json.loads(self.job['source_payload']).get('emotion_consent_version'))
        from .memory_reflection_policy import for_job
        if (actual != expected or actual.tracking != expected.tracking
                or actual.artifact_versions != expected.artifact_versions
                or for_job(self.store, self.job) != getattr(expected, 'reflection', None)):
            raise MemoryError('evidence_changed', 'Extraction context changed before SDK request')

    def _phase(self, value):
        column = {'waiting_slot': 'waiting_at', 'slot_acquired': 'slot_acquired_at'}.get(value)
        self.store.db.execute('UPDATE mia_job_policy SET phase=?,phase_at=?' +
            (f',{column}=?' if column else '') + " WHERE job_id=? AND EXISTS (SELECT 1 FROM mia_jobs j "
            "WHERE j.id=job_id AND j.state IN ('pending','extracting','validated','retry_wait'))",
            (value, self.store.clock(), *([self.store.clock()] if column else []), self.job['id']))

    def phase(self, value):
        with self.store.lock, self.store.db:
            self._phase(value)

    async def observe(self, phase):
        await asyncio.to_thread(self.phase, phase)

    def reserve(self):
        with self.store.lock, self.store.db:
            db = self.store.db
            db.execute('BEGIN IMMEDIATE')
            self._eligible()
            current = self.jobs.current(self.job)
            policy = self.jobs.policy(self.job)
            count = db.execute('SELECT COUNT(*) FROM mia_model_requests WHERE job_id=?', (self.job['id'],)).fetchone()[0]
            if policy['version'] != 2 or count != current['attempts'] or count >= 2:
                raise RuntimeError('V2 request budget exhausted or inconsistent')
            ordinal, now = count + 1, self.store.clock()
            db.execute('INSERT INTO mia_model_requests(job_id,ordinal,waiting_at,slot_acquired_at,rechecked_at,intent_at,outcome) '
                "VALUES (?,?,?,?,?,?,'intent')", (self.job['id'], ordinal, policy['waiting_at'], policy['slot_acquired_at'], now, now))
            db.execute('UPDATE mia_jobs SET attempts=?,updated_at=? WHERE id=?', (ordinal, now, self.job['id']))
            db.execute("UPDATE mia_job_policy SET phase='sdk_start_intent',phase_at=?,rechecked_at=? WHERE job_id=?",
                (now, now, self.job['id']))
        self.ordinal = ordinal
        return ordinal

    async def intent(self):
        await self.worker._scope(self.job)
        self.ordinal = await asyncio.to_thread(self.reserve)

    def check(self):
        with self.store.lock:
            self._eligible()

    async def preflight(self):
        await self.worker._scope(self.job)
        await asyncio.to_thread(self.check)

    def entry(self, http=False):
        with self.store.lock, self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            self._eligible()
            if http:
                changed = self.store.db.execute("UPDATE mia_model_requests SET http_started_at=?,http_sends=1 "
                    "WHERE job_id=? AND ordinal=? AND sdk_started_at IS NOT NULL AND http_sends=0",
                    (self.store.clock(), self.job['id'], self.ordinal)).rowcount
            else:
                changed = self.store.db.execute("UPDATE mia_model_requests SET sdk_started_at=?,outcome='sdk_started' "
                    'WHERE job_id=? AND ordinal=? AND sdk_started_at IS NULL',
                    (self.store.clock(), self.job['id'], self.ordinal)).rowcount
                self._phase('sdk_started')
            if changed != 1:
                raise RuntimeError('V2 duplicate SDK or HTTP request blocked')

    async def start(self, http=False):
        # SDK entry is observed from inside the SDK's request builder. HTTP
        # still rechecks manager binding immediately before sending.
        if http:
            await self.worker._scope(self.job)
        await asyncio.to_thread(self.entry, http)

    def response(self):
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE mia_model_requests SET response_completed_at=?,outcome='response_complete' "
                'WHERE job_id=? AND ordinal=?', (self.store.clock(), self.job['id'], self.ordinal))
            self._phase('response_complete')

    def finish(self, outcome):
        if self.ordinal is not None:
            with self.store.lock, self.store.db:
                self.store.db.execute('UPDATE mia_model_requests SET native_finished_at=?,outcome=? '
                    'WHERE job_id=? AND ordinal=?', (self.store.clock(), outcome, self.job['id'], self.ordinal))

    async def finished(self, outcome):
        await asyncio.to_thread(self.finish, outcome)

    def enter_native(self):
        with _native_lock:
            if self.key in _native:
                raise RuntimeError('Duplicate V2 native task')
            _native.add(self.key)

    def leave_native(self):
        with _native_lock:
            _native.discard(self.key)


def bind_sdk(provider, accounting):
    """Reviewed fixed OpenAI adapter + exact SDK; no opaque retrying fallback.

    The owned Provider/client is per extraction. Both SDK retries and HTTP
    redirects are disabled; the HTTP hook independently refuses a second send.
    """
    import httpx
    import openai
    from .proposal import provider_capabilities, ExtractionError
    if not provider_capabilities(provider)['native_tools'] or type(provider.client) is not openai.OpenAI:
        raise ExtractionError('tool_contract', 'V2 requires the reviewed single-send Provider contract')
    client = provider.client
    # This is the per-extraction owned client. Keep its actual SDK entry and
    # installed observers; cloning would silently discard resource wrappers.
    client.max_retries = 0
    from openai._base_client import SyncHttpxClientWrapper
    if type(client._client) not in (httpx.Client, openai.DefaultHttpxClient, SyncHttpxClientWrapper):
        raise ExtractionError('tool_contract', 'V2 HTTP transport contract is unreviewed')
    transports = {client._client._transport, *[t for t in client._client._mounts.values() if t is not None]}
    if any(type(t) is not httpx.MockTransport and (type(t) is not httpx.HTTPTransport
            or t._pool._retries != 0) for t in transports):
        raise ExtractionError('tool_contract', 'V2 HTTP transport retry budget is unreviewed')
    client._client.follow_redirects = False
    create = client.chat.completions.create
    build = client._build_request
    blocked = []
    def observed_build(*args, **kwargs):
        try:
            accounting.call(accounting.start())
            return build(*args, **kwargs)
        except Exception as error:
            blocked.append(error)
            raise
    client._build_request = observed_build
    def request_hook(unused):
        try:
            accounting.call(accounting.start(http=True))
        except Exception as error:
            blocked.append(error)
            raise
    client._client.event_hooks['request'].append(request_hook)

    class ObservedStream:
        def __init__(self, stream):
            self.stream = stream

        def __iter__(self):
            yield from self.stream
            accounting.call(_response())

        def close(self):
            self.stream.close()

    async def _response():
        await asyncio.to_thread(accounting.response)

    def tracked_create(*args, **kwargs):
        accounting.call(accounting.intent())
        accounting.call(accounting.preflight())
        try:
            return ObservedStream(create(*args, **kwargs))
        except Exception:
            if blocked:
                raise blocked[0] from None
            raise
    client.chat.completions.create = tracked_create
