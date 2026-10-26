"""Per-agent reflection policy and completion-driven, single-claim maintenance."""

import asyncio
import hashlib
import json

from .memory import MemoryError
from .memory_state import YoumiMemoryState


def status(store, owner):
    row = store.db.execute('SELECT * FROM youmi_memory_reflection_settings WHERE owner=?', (owner,)).fetchone()
    return (dict(row, enabled=bool(row['enabled'])) if row else dict(owner=owner, enabled=False,
        interval_seconds=86400, version=0, last_maintenance_at=None, last_signature=None))


def signature(context):
    return hashlib.sha256(json.dumps(sorted((r['id'], r['content_version'], r['evidence_version'])
        for r in context)).encode()).hexdigest()


def bind_job(store, job_id, owner, policy_version):
    # Called inside the original admission transaction; no new task or request.
    cfg = status(store, owner)
    if not cfg['enabled'] or policy_version != 2:
        return
    from .extraction import context_for
    context = context_for(store, owner)
    due = bool(any(r.get('evidence') for r in context) and (cfg['last_maintenance_at'] is None or
        store.clock() >= cfg['last_maintenance_at'] + cfg['interval_seconds']) and
        signature(context) != cfg['last_signature'] and not store.db.execute(
            'SELECT 1 FROM youmi_memory_reflection_jobs WHERE owner=? AND maintenance_active=1', (owner,)).fetchone())
    store.db.execute('INSERT INTO youmi_memory_reflection_jobs VALUES (?,?,?,?,?)',
        (job_id, owner, cfg['version'], int(due), int(due)))


def for_job(store, job):
    row = store.db.execute('SELECT * FROM youmi_memory_reflection_jobs WHERE job_id=?', (job['id'],)).fetchone()
    if not row:
        return None
    cfg = status(store, job['owner'])
    if not cfg['enabled'] or row['settings_version'] != cfg['version'] or row['owner'] != job['owner']:
        raise MemoryError('evidence_changed', 'Reflection policy changed')
    return dict(version=row['settings_version'], maintenance_due=bool(row['maintenance_due']))


def validate_completion(store, job, structured):
    marker = structured.get('_maintenance_signature')
    if marker is None:
        return
    policy = for_job(store, job)
    from .extraction import context_for
    if not policy or not policy['maintenance_due'] or marker != signature(context_for(store, job['owner'])):
        raise MemoryError('evidence_changed', 'Maintenance evidence changed')


def commit_completion(store, job, structured):
    if '_maintenance_signature' in structured:
        store.db.execute('UPDATE youmi_memory_reflection_settings SET last_maintenance_at=?,last_signature=? WHERE owner=?',
            (int(store.clock()), structured['_maintenance_signature'], job['owner']))


class YoumiReflectionPolicy:
    def __init__(self, store):
        self.store = store

    def _configure(self, owner, command):
        if (set(command) != {'action', 'enabled', 'interval_seconds', 'version'} or command['action'] != 'configure'
                or type(command['enabled']) is not bool or type(command['interval_seconds']) is not int
                or not 60 <= command['interval_seconds'] <= 604800 or type(command['version']) is not int):
            raise MemoryError('invalid_input', 'Invalid reflection configuration')
        with self.store.lock, self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            if status(self.store, owner)['version'] != command['version']:
                raise MemoryError('conflict', 'Reflection configuration changed')
            self.store._owner(owner)
            self.store.db.execute('INSERT INTO youmi_memory_reflection_settings VALUES (?,?,?,?,NULL,NULL) '
                'ON CONFLICT(owner) DO UPDATE SET enabled=excluded.enabled,interval_seconds=excluded.interval_seconds,'
                'version=excluded.version,last_maintenance_at=NULL,last_signature=NULL',
                (owner, int(command['enabled']), command['interval_seconds'], command['version'] + 1))
            self.store.db.execute('UPDATE owners SET revision=revision+1 WHERE owner=?', (owner,))
            self.store._cancel_jobs(owner)
            return status(self.store, owner)

    def _status(self, owner):
        with self.store.lock:
            return status(self.store, owner)

    async def manage(self, scope, authorization, command):
        if not isinstance(command, dict):
            raise MemoryError('invalid_input', 'Invalid reflection command')
        permission = 'memory:read' if command == {'action': 'status'} else 'memory:manage'
        await YoumiMemoryState._require(scope, authorization, permission)
        result = (await asyncio.to_thread(self._status, scope.owner) if permission == 'memory:read' else
            await asyncio.to_thread(self._configure, scope.owner, command))
        await YoumiMemoryState._require(scope, authorization, permission)
        return result
