"""Service-owned durable extraction jobs; SQLite is the queue authority."""

import asyncio
from collections import deque
from dataclasses import dataclass
import hashlib
import json
import threading
import sqlite3

from .authorization import AuthenticatedScope, PrivateAuthorizationRequired
from .memory import MemoryError, _safe_text
from .extraction import context_for
from .memory_reflection import validate_proposal as validate_output
from .embedding import EmbeddingUnavailable
from .semantic import SemanticMemoryIndex
from .proposal import validation_feedback
from .model_requests import ModelQueueError, model_requests


ACTIVE = ('pending', 'extracting', 'validated', 'retry_wait')


@dataclass(frozen=True)
class TurnCompleted:
    scope: AuthenticatedScope
    session_id: str
    turn_id: str
    message_id: str
    text: str
    revision: int
    priority: bool = False


class JobStore:
    def __init__(self, store, capacity=128, lifetime=120, *, policy_version=2):
        if policy_version not in (1, 2):
            raise ValueError('Unsupported job policy')
        self.store, self.capacity, self.lifetime = store, capacity, lifetime
        self.policy_version = policy_version

    def submit(self, event):
        if not isinstance(event.scope, AuthenticatedScope) or event.scope.persona_id != 'mia':
            raise PrivateAuthorizationRequired()
        text = _safe_text(event.text, 512, 'source')
        for value in (event.session_id, event.turn_id, event.message_id):
            _safe_text(value, 128, 'source id')
        key = hashlib.sha256(json.dumps([event.scope.owner, event.session_id, event.message_id]).encode()).hexdigest()
        now = int(self.store.clock())
        with self.store.lock, self.store.db:
            db = self.store.db
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT id,state FROM mia_jobs WHERE owner=? AND idempotency_key=?',
                                  (event.scope.owner, key)).fetchone()
            if previous:
                return dict(previous, status='duplicate')
            self.store._owner(event.scope.owner)
            state = db.execute('SELECT revision,paused FROM owners WHERE owner=?', (event.scope.owner,)).fetchone()
            if state['paused'] or state['revision'] != event.revision:
                return {'status': 'cancelled', 'code': 'memory_changed'}
            count = db.execute("SELECT COUNT(*) FROM mia_jobs WHERE state IN ('pending','extracting','validated','retry_wait')").fetchone()[0]
            if count >= self.capacity:
                return {'status': 'queue_full', 'accepted': False}
            # Stable identifiers, one bounded real user excerpt, no credential or assistant text.
            from .emotions import consent
            tracking = consent(self.store, event.scope.owner)
            db.execute('INSERT INTO mia_jobs(id,owner,idempotency_key,scope_snapshot,memory_revision,session_id,turn_id,'
                'source_payload,state,created_at,updated_at,deadline) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (key, event.scope.owner, key, json.dumps(event.scope.snapshot(), sort_keys=True), event.revision,
                 event.session_id, event.turn_id, json.dumps({'message_id': event.message_id, 'text': text,
                 'priority': event.priority, 'emotion_consent_version': tracking['version'] if tracking['enabled'] else None},
                 ensure_ascii=False), 'pending', now, now, now + self.lifetime))
            if self.policy_version == 2:
                db.execute("INSERT INTO mia_job_policy(job_id,version,phase,phase_at) VALUES (?,2,'admitted',?)", (key, now))
            from .memory_reflection_policy import bind_job
            bind_job(self.store, key, event.scope.owner, self.policy_version)
            return {'status': 'accepted', 'accepted': True, 'id': key, 'state': 'pending'}

    def recover(self):
        from .request_accounting import recover_requests
        recover_requests(self.store)
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE mia_jobs SET state=CASE WHEN validated_result IS NULL THEN 'pending' ELSE 'validated' END "
                                  "WHERE state='extracting'")

    def ready(self, *, v2_only=False):
        now = int(self.store.clock())
        with self.store.lock, self.store.db:
            from .emotions import prune
            prune(self.store)
            self.store.db.execute("UPDATE mia_jobs SET state='failed',error_category='deadline_exceeded',source_payload='{}',"
                "validated_result=NULL,updated_at=? WHERE state IN ('pending','extracting','validated','retry_wait') AND deadline<=?", (now, now))
            return [dict(row) for row in self.store.db.execute(
                "SELECT * FROM mia_jobs WHERE state IN ('pending','extracting','validated','retry_wait') "
                "AND (next_attempt_at IS NULL OR next_attempt_at<=?) " +
                ("AND EXISTS (SELECT 1 FROM mia_job_policy p WHERE p.job_id=mia_jobs.id AND p.version=2) " if v2_only else '') +
                "ORDER BY created_at,rowid LIMIT ?", (now, self.capacity))]

    def update(self, job, state, *, result=None, error=None, attempt=False):
        now = int(self.store.clock())
        terminal = state not in ACTIVE
        with self.store.lock, self.store.db:
            self.store.db.execute('UPDATE mia_jobs SET state=?,validated_result=?,error_category=?,updated_at=?,'
                'next_attempt_at=?,attempts=attempts+?,source_payload=CASE WHEN ? THEN \'{}\' ELSE source_payload END '
                "WHERE id=? AND state IN ('pending','extracting','validated','retry_wait')",
                (state, result, error, now, now + 1 if state == 'retry_wait' else None, int(attempt), terminal, job['id']))

    def current(self, job):
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM mia_jobs WHERE id=?', (job['id'],)).fetchone()
            return dict(row)

    def policy(self, job):
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM mia_job_policy WHERE job_id=?', (job['id'],)).fetchone()
            return dict(row) if row else {'version': 1}

    def active_ids(self):
        # Includes deferred retries; ready() is only the scheduling subset.
        with self.store.lock:
            return {row[0] for row in self.store.db.execute(
                "SELECT id FROM mia_jobs WHERE state IN ('pending','extracting','validated','retry_wait')")}

    def eligible(self, job):
        with self.store.lock:
            from .memory_reflection_policy import for_job
            for_job(self.store, job)
            current = self.current(job)
            state = self.store.snapshot(job['owner'])
            return (current['state'] in ACTIVE and current['deadline'] > self.store.clock()
                    and not state['paused'] and state['revision'] == job['memory_revision'])

    def commit(self, job, result, vectors=None):
        # Fact write and terminal job state share the fact store's transaction.
        return self.store.apply_extraction(job['owner'], job['session_id'], job['memory_revision'],
            [json.loads(job['source_payload'])['text']], result, job['id'], job_id=job['id'], prepared_vectors=vectors)


class NativeLane:
    """One daemon lane: cancellation cannot launch a second hung native call."""
    def __init__(self):
        self.done = threading.Event()
        self.done.set()

    async def call(self, function, timeout):
        if not self.done.is_set():
            raise RuntimeError('native_lane_busy')
        self.done.clear()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        def deliver(value, error):
            if not future.done():
                if error is None:
                    future.set_result(value)
                else:
                    future.set_exception(error)
        def run():
            try:
                value, error = function(), None
            except Exception as failure:
                value, error = None, failure
            finally:
                self.done.set()
            try:
                loop.call_soon_threadsafe(deliver, value, error)
            except RuntimeError:
                pass  # Late completion is reconciled on the next process start.
        threading.Thread(target=run, daemon=True, name='youmi-memory-extractor').start()
        return await asyncio.wait_for(future, timeout)


class MemoryWorker:
    def __init__(self, jobs, authorization, resolve, *, extraction_timeout=30, encoder=None):
        self.jobs, self.authorization, self.resolve = jobs, authorization, resolve
        self.extraction_timeout = extraction_timeout
        self.index = SemanticMemoryIndex(jobs.store, encoder) if encoder is not None else None
        self.lane = NativeLane()
        self.queue = deque(maxlen=jobs.capacity)
        self.served, self.tick = {}, 0
        self.results = {}
        self.validation_feedback = {}
        self.foreground = 0
        self.closing = False
        self.task = None
        self.wake = asyncio.Event()

    async def start(self):
        await asyncio.to_thread(self.jobs.recover)
        self.task = asyncio.create_task(self.run(), name='youmi-memory-worker')

    async def submit(self, event):
        if self.closing:
            return {'status': 'closing', 'accepted': False}
        await self.authorization.require(event.scope, 'memory:write')
        result = await asyncio.to_thread(self.jobs.submit, event)
        self.wake.set()
        return result

    async def _scope(self, job):
        scope = await self.authorization.restore(json.loads(job['scope_snapshot']))
        if scope.owner != job['owner'] or scope.persona_id != 'mia':
            raise PrivateAuthorizationRequired()
        await self.authorization.require(scope, 'memory:write')
        if job['deadline'] <= self.jobs.store.clock():
            await asyncio.to_thread(self.jobs.update, job, 'failed', error='deadline_exceeded')
        if not await asyncio.to_thread(self.jobs.eligible, job):
            raise PrivateAuthorizationRequired()
        return scope

    async def run_once(self):
        rows = await asyncio.to_thread(self.jobs.ready, v2_only=bool(self.foreground))
        active = {row['id'] for row in rows}
        self.results = {key: value for key, value in self.results.items() if key in active}
        if self.validation_feedback:
            pending = await asyncio.to_thread(self.jobs.active_ids)
            self.validation_feedback = {key: value for key, value in self.validation_feedback.items() if key in pending}
        if (self.foreground and model_requests.background_after is None) or not self.lane.done.is_set():
            return False
        owners = {row['owner'] for row in rows}
        self.served = {owner: count for owner, count in self.served.items() if owner in owners}
        rows.sort(key=lambda row: (self.served.get(row['owner'], 0),
            not json.loads(row['source_payload']).get('priority', False), row['created_at']))
        self.queue.clear()
        self.queue.extend(rows)
        if not self.queue:
            return False
        job = self.queue.popleft()
        policy = await asyncio.to_thread(self.jobs.policy, job)
        self.tick += 1
        self.served[job['owner']] = self.tick
        result = self.results.get(job['id'], job['validated_result'])
        try:
            scope = await self._scope(job)
            if result is None:
                if job['attempts'] >= 2:
                    await asyncio.to_thread(self.jobs.update, job, 'failed', error='attempts_exhausted')
                    return True
                context = await asyncio.to_thread(context_for, self.jobs.store, scope.owner,
                    json.loads(job['source_payload']).get('emotion_consent_version'))
                from .memory_reflection_policy import for_job, signature
                with self.jobs.store.lock:
                    context.reflection = for_job(self.jobs.store, job)
                maintenance_signature = signature(context)
                context.validation_feedback = (json.loads(policy['feedback']) if policy.get('feedback')
                    else self.validation_feedback.get(job['id']))
                extract = await self.resolve(scope)
                # Context/model preparation can outlive the durable deadline.
                # Reject before entering an attempt; do not refund old attempts.
                if job['deadline'] <= self.jobs.store.clock():
                    await asyncio.to_thread(self.jobs.update, job, 'failed', error='deadline_exceeded')
                    return True
                # V1 keeps the old entry rule; V2 competes through the gate's
                # foreground-first, bounded background opportunity policy.
                if self.foreground and (policy['version'] == 1 or model_requests.background_after is None):
                    return False
                await asyncio.to_thread(self.jobs.update, job, 'extracting', attempt=policy['version'] == 1)
                context.request_cancelled = threading.Event()
                context.model_request_phase = 'preparing'
                from .request_accounting import RequestAccounting
                accounting = RequestAccounting(self, job, context)
                if policy['version'] == 2:
                    context.request_accounting = accounting
                else:
                    # Keep V1's queue-charged counter, but never exempt old
                    # tasks from current permission/source/deadline checks.
                    context.before_sdk = lambda: accounting.call(accounting.preflight())
                try:
                    output = await self.lane.call(lambda: extract([json.loads(job['source_payload'])['text']], context),
                        min(self.extraction_timeout, max(0.001, job['deadline'] - self.jobs.store.clock())))
                except ModelQueueError:
                    raise
                except asyncio.TimeoutError:
                    context.request_cancelled.set()
                    if context.model_request_phase == 'queued':
                        raise ModelQueueError('model_request_queue_timeout') from None
                    raise TimeoutError('extraction_timeout') from None
                finally:
                    # A cancelled/deadline-expired native waiter must never start
                    # a fresh model request when a slot becomes available later.
                    context.request_cancelled.set()
                validated = validate_output(output, [json.loads(job['source_payload'])['text']], context)
                if (context.reflection and context.reflection['maintenance_due']
                        and getattr(context, 'maintenance_generated', False)):
                    validated['_maintenance_signature'] = maintenance_signature
                if policy['version'] == 2:
                    await asyncio.to_thread(context.request_accounting.phase, 'validation_succeeded')
                self.validation_feedback.pop(job['id'], None)
                result = json.dumps(validated, ensure_ascii=False)
                self.results[job['id']] = result
                await self._scope(job)
            if json.loads(result).get('_schema') != 1:
                raise MemoryError('extractor_version_changed', 'Unverified legacy task result')
            if result is not None:
                await asyncio.to_thread(self.jobs.update, job, 'validated', result=result)
                if policy['version'] == 2:
                    await asyncio.to_thread(self._v2_validation, job, None)
                self.results.pop(job['id'], None)
            await self._scope(job)
            structured = json.loads(result)
            vectors = {}
            for i, record in enumerate(structured['memories']):
                if not record['sensitive'] and record['kind'] == 'explicit':
                    if self.index is None:
                        raise EmbeddingUnavailable('embedding_assets_unconfigured')
                    try:
                        await self.index._background_ready(max(0.001, job['deadline'] - self.jobs.store.clock()))
                    except asyncio.TimeoutError:
                        raise TimeoutError('embedding_wait_timeout') from None
                    await self._scope(job)
                    vector = await self.lane.call(lambda: self.index.encoder.encode(record['content']),
                        min(3, max(0.001, job['deadline'] - self.jobs.store.clock())))
                    vectors[i] = self.index._vector(vector).tobytes()
            await self._scope(job)
            if policy['version'] == 2:
                await asyncio.to_thread(self._v2_phase, job, 'committing')
            await asyncio.to_thread(self.jobs.commit, job, structured, vectors)
        except PrivateAuthorizationRequired:
            await asyncio.to_thread(self.jobs.update, job, 'cancelled', error='authorization_or_memory_changed')
            self.results.pop(job['id'], None)
        except MemoryError as error:
            if error.code in {'evidence_changed', 'extractor_version_changed'}:
                await asyncio.to_thread(self.jobs.update, job, 'cancelled', error=error.code)
                self.results.pop(job['id'], None)
                return True
            current = await asyncio.to_thread(self.jobs.current, job)
            feedback = validation_feedback(error)
            if policy['version'] == 2:
                await asyncio.to_thread(self._v2_validation, job, feedback, getattr(error, 'category', 'invalid_output'))
            if current['attempts'] < 2 and feedback is not None:
                self.validation_feedback[job['id']] = feedback
            else:
                self.validation_feedback.pop(job['id'], None)
            await asyncio.to_thread(self.jobs.update, job, 'failed' if current['attempts'] >= 2 else 'retry_wait',
                                    error=getattr(error, 'category', 'invalid_output'))
        except Exception as error:
            current = await asyncio.to_thread(self.jobs.current, job)
            category = ('database' if isinstance(error, sqlite3.Error) else
                        'embedding' if isinstance(error, EmbeddingUnavailable) else
                        getattr(error, 'category', 'timeout' if isinstance(error, TimeoutError) else 'model'))
            terminal = result is None and current['attempts'] >= 2
            await asyncio.to_thread(self.jobs.update, job, 'failed' if terminal else 'retry_wait',
                                    result=result, error=category)
        return True

    def _v2_phase(self, job, phase):
        with self.jobs.store.lock, self.jobs.store.db:
            self.jobs.store.db.execute("UPDATE mia_job_policy SET phase=?,phase_at=? WHERE job_id=? AND phase!='terminal'",
                (phase, self.jobs.store.clock(), job['id']))

    def _v2_validation(self, job, feedback, category=None):
        with self.jobs.store.lock, self.jobs.store.db:
            self.jobs.store.db.execute("UPDATE mia_job_policy SET feedback=?,phase=?,phase_at=? WHERE job_id=? AND phase!='terminal'",
                (json.dumps(feedback, ensure_ascii=False) if feedback else None,
                 'validation_failed' if category else 'validation_succeeded', self.jobs.store.clock(), job['id']))
            self.jobs.store.db.execute('UPDATE mia_model_requests SET outcome=? WHERE job_id=? AND ordinal='
                '(SELECT MAX(ordinal) FROM mia_model_requests WHERE job_id=?)',
                (category or 'validation_succeeded', job['id'], job['id']))

    async def run(self):
        while not self.closing:
            try:
                worked = await self.run_once()
            except Exception:
                # DB outage: durable rows remain authoritative; retry without a spin.
                worked = False
            if not worked:
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), 0.25)
                except asyncio.TimeoutError:
                    pass

    async def close(self, timeout=20):
        self.closing = True
        self.wake.set()
        if self.task:
            done, pending = await asyncio.wait({self.task}, timeout=timeout)
            for task in pending:
                task.cancel()
            # No wait for an uncooperative model thread or a fresh per-job deadline.
            return not pending
        return True
