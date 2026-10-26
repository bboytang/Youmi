"""Completed-turn snapshots and owner-scoped storage for the new memory runtime.

Capture/link helpers run inside their caller's authorized transaction. They never
infer a missing historical state from the worker's current affect or cognition.
"""

import asyncio
import copy
import hashlib
import json
import logging
import math
import sqlite3

from .authorization import AuthenticatedScope, PrivateAuthorizationRequired
from .memory import MemoryError, _safe_text, _text


COGNITIVE_DEFAULTS = {
    'recall': dict(minimum=1, baseline=7, maximum=20, mode='fixed', sensitivity=.3),
    'impression': dict(minimum=1, baseline=3, maximum=10, mode='fixed', sensitivity=.3),
    'expression': dict(minimum=0, baseline=.5, maximum=1, mode='fixed', sensitivity=.3),
    'creativity': dict(minimum=0, baseline=.7, maximum=1, mode='fixed', sensitivity=.3),
}


def cognitive_status(store, owner):
    with store.lock:
        row = store.db.execute('SELECT * FROM youmi_cognitive_state WHERE owner=?', (owner,)).fetchone()
        return dict(enabled=bool(row['enabled']) if row else False,
            dimensions=json.loads(row['config_json']) if row else copy.deepcopy(COGNITIVE_DEFAULTS),
            energy=json.loads(row['energy_json']) if row else {key: 0.0 for key in COGNITIVE_DEFAULTS},
            version=row['version'] if row else 0)


def _number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise MemoryError('invalid_input', 'Invalid memory state number')


def _configure_cognition(store, owner, command):
    if command.get('action') not in {'configure', 'adjust'}:
        raise MemoryError('invalid_input', 'Invalid cognitive operation')
    with store.lock, store.db:
        store.db.execute('BEGIN IMMEDIATE')
        current = cognitive_status(store, owner)
        if type(command.get('version')) is not int or command['version'] != current['version']:
            raise MemoryError('conflict', 'Cognitive state changed')
        config, energy, enabled = current['dimensions'], current['energy'], current['enabled']
        if command['action'] == 'configure':
            config, enabled = command.get('dimensions'), command.get('enabled')
            if not isinstance(config, dict) or set(config) != set(COGNITIVE_DEFAULTS) or type(enabled) is not bool:
                raise MemoryError('invalid_input', 'Invalid cognitive configuration')
            for key, item in config.items():
                if (not isinstance(item, dict) or set(item) != set(COGNITIVE_DEFAULTS[key])
                        or item['mode'] not in {'fixed', 'dynamic'}):
                    raise MemoryError('invalid_input', 'Invalid cognitive dimension')
                low, high = (1, 20) if key == 'recall' else (1, 10) if key == 'impression' else (0, 1)
                for name in ('minimum', 'baseline', 'maximum'):
                    _number(item[name], low, high)
                _number(item['sensitivity'], 0, 3)
                if not item['minimum'] <= item['baseline'] <= item['maximum']:
                    raise MemoryError('invalid_input', 'Invalid cognitive bounds')
        else:
            changes = command.get('energy')
            if not isinstance(changes, dict) or not changes or set(changes) - set(energy):
                raise MemoryError('invalid_input', 'Invalid cognitive adjustment')
            for key, value in changes.items():
                _number(value, -20, 20)
                if config[key]['mode'] == 'dynamic':
                    energy[key] = value
        energy = {key: 0.0 if config[key]['mode'] == 'fixed' else value for key, value in energy.items()}
        store._owner(owner)
        store.db.execute('INSERT INTO youmi_cognitive_state VALUES (?,?,?,?,?,?) ON CONFLICT(owner) DO UPDATE SET '
            'enabled=excluded.enabled,config_json=excluded.config_json,energy_json=excluded.energy_json,'
            'version=excluded.version,updated_at=excluded.updated_at',
            (owner, int(enabled), json.dumps(config), json.dumps(energy), current['version'] + 1, int(store.clock())))
    return cognitive_status(store, owner)


def capture_turn(store, owner, session, turn, source):
    """Caller owns completion, authorization and transaction; no raw text stored."""
    if not store.db.in_transaction:
        raise ValueError('Turn state requires the completion transaction')
    _text(session, 128, 'session'); _text(turn, 128, 'turn')
    fingerprint = hashlib.sha256(_safe_text(source, 512, 'source').encode()).hexdigest()
    previous = store.db.execute('SELECT source_fingerprint FROM youmi_memory_turn_state '
        'WHERE owner=? AND session_id=? AND turn_id=?', (owner, session, turn)).fetchone()
    if previous:
        if previous['source_fingerprint'] != fingerprint:
            raise MemoryError('conflict', 'Completed source changed')
        return False
    from .affect import state
    current, cognition = state(store, owner), cognitive_status(store, owner)
    affect = None
    if current['enabled']:
        system = current.get('affect_system')
        affect = (dict(kind='affect_system', version=system['version'], values=system['values'],
            enabled_dimensions=[key for key, item in system['dimensions'].items() if item['enabled']]) if system else
            dict(kind='legacy', **{key: current[key] for key in ('version', 'feeling', 'valence', 'arousal', 'tension')}))
    if not affect and not cognition['enabled']:
        return False
    store.db.execute('INSERT INTO youmi_memory_turn_state VALUES (?,?,?,?,?,?,?)',
        (owner, session, turn, fingerprint, json.dumps(affect) if affect else None,
         json.dumps(cognition) if cognition['enabled'] else None, int(store.clock())))
    prune_turns(store, owner)
    return True


def try_capture_turn(store, owner, session, turn, source):
    """Snapshot failure must not roll back an otherwise valid affect completion."""
    if not store.db.in_transaction:
        raise ValueError('Turn state requires the completion transaction')
    store.db.execute('SAVEPOINT youmi_memory_capture')
    try:
        changed = capture_turn(store, owner, session, turn, source)
    except (sqlite3.Error, ValueError) as error:
        store.db.execute('ROLLBACK TO youmi_memory_capture')
        logging.getLogger(__name__).warning('Youmi memory snapshot unavailable (%s)', type(error).__name__)
        changed = False
    finally:
        store.db.execute('RELEASE youmi_memory_capture')
    return changed


def prune_turns(store, owner):
    # Source-linked state is part of that memory's evidence, not loose history.
    unlinked = ('NOT EXISTS (SELECT 1 FROM youmi_memory_source_state s WHERE s.owner=t.owner '
                'AND s.session_id=t.session_id AND s.turn_id=t.turn_id)')
    store.db.execute('DELETE FROM youmi_memory_turn_state AS t WHERE t.owner=? AND ' + unlinked +
        " AND NOT EXISTS (SELECT 1 FROM mia_jobs j WHERE j.owner=t.owner AND j.session_id=t.session_id "
        "AND j.turn_id=t.turn_id AND j.state IN ('pending','extracting','validated','retry_wait')) "
        'AND (t.captured_at<? OR t.rowid NOT IN (SELECT t.rowid FROM youmi_memory_turn_state t WHERE t.owner=? AND ' +
        unlinked + ' ORDER BY t.captured_at DESC,t.rowid DESC LIMIT 10000))',
        (owner, int(store.clock()) - 90 * 86400, owner))


def attach_job_sources(store, job, memory_ids):
    """Link only a snapshot already captured for this completed source."""
    payload = json.loads(job['source_payload'])
    fingerprint = hashlib.sha256(payload['text'].encode()).hexdigest()
    state = store.db.execute('SELECT 1 FROM youmi_memory_turn_state WHERE owner=? AND session_id=? '
        'AND turn_id=? AND source_fingerprint=?', (job['owner'], job['session_id'], job['turn_id'], fingerprint)).fetchone()
    if not state:
        return  # Historical jobs remain unknown; never backfill from current state.
    for memory_id in memory_ids:
        store.db.execute('INSERT OR IGNORE INTO youmi_memory_source_state '
            'SELECT m.id,s.session_id,s.fingerprint,m.owner,?,x.content_version FROM memories m '
            'JOIN mia_memory_meta x ON x.memory_id=m.id JOIN sources s ON s.memory_id=m.id '
            'WHERE m.owner=? AND m.id=? AND s.session_id=? AND s.fingerprint=?',
            (job['turn_id'], job['owner'], memory_id, job['session_id'], fingerprint))


def merge_in_transaction(store, owner, command, *, job_id=None):
    """Caller owns the verified scope and surrounding fact/job transaction."""
    if not store.db.in_transaction:
        raise ValueError("Merge requires the caller transaction")
    if not isinstance(command, dict):
        raise MemoryError('invalid_input', 'Invalid merge command')
    operation = _text(command.get('operation_id'), 128, 'operation')
    retired = _text(command.get('retired_id'), 64, 'memory id')
    survivor = _text(command.get('survivor_id'), 64, 'memory id')
    versions = command.get('versions')
    if (retired == survivor or not isinstance(versions, dict) or set(versions) != {'retired', 'survivor'}
            or any(not isinstance(v, list) or len(v) != 2 or any(type(n) is not int or n < 1 for n in v)
                   for v in versions.values()) or type(command.get('revision')) is not int):
        raise MemoryError('invalid_input', 'Invalid merge versions')
    request_hash = hashlib.sha256(json.dumps(command, sort_keys=True, allow_nan=False).encode()).hexdigest()
    previous = store.db.execute('SELECT * FROM youmi_memory_replacements WHERE owner=? AND operation_id=?',
                                (owner, operation)).fetchone()
    if previous:
        if previous['request_hash'] != request_hash:
            raise MemoryError('conflict', 'Merge operation changed')
        return dict(retired_id=retired, survivor_id=survivor, duplicate=True)
    state = store.db.execute('SELECT revision,paused FROM owners WHERE owner=?', (owner,)).fetchone()
    if state is None or state['paused'] or state['revision'] != command['revision']:
        raise MemoryError('conflict', 'Memory changed')
    for name, memory_id in [('retired', retired), ('survivor', survivor)]:
        row = store.db.execute('SELECT x.content_version,x.evidence_version FROM memories m '
            'JOIN mia_memory_meta x ON x.memory_id=m.id WHERE m.owner=? AND m.id=? AND m.confirmed=1 '
            "AND x.state='active' AND NOT EXISTS (SELECT 1 FROM youmi_memory_replacements r WHERE r.retired_id=m.id)",
            (owner, memory_id)).fetchone()
        if row is None or list(row) != versions[name]:
            raise MemoryError('evidence_changed', 'Merge target changed')
    store._invalidate_traits(owner, retired)
    store._invalidate_observations(owner, retired)
    store.db.execute("UPDATE mia_events SET state='invalidated',version=version+1 WHERE id IN "
                     '(SELECT event_id FROM mia_event_evidence WHERE memory_id=?)', (retired,))
    store.db.execute('UPDATE memories SET confirmed=0,updated_at=? WHERE id=?', (int(store.clock()), retired))
    store.db.execute("UPDATE mia_memory_meta SET state='superseded' WHERE memory_id=?", (retired,))
    store.db.execute('DELETE FROM memory_search WHERE memory_id=?', (retired,))
    store.db.execute('INSERT INTO youmi_memory_replacements VALUES (?,?,?,?,?,?,?,?)',
        (retired, survivor, owner, operation, request_hash, versions['retired'][0], versions['survivor'][0], int(store.clock())))
    store.db.execute('UPDATE owners SET revision=revision+1 WHERE owner=?', (owner,))
    store._cancel_jobs(owner, exclude=job_id)
    return dict(retired_id=retired, survivor_id=survivor, duplicate=False)


def _merge(store, owner, command):
    with store.lock, store.db:
        store.db.execute("BEGIN IMMEDIATE")
        return merge_in_transaction(store, owner, command)


class YoumiMemoryState:
    """Internal control interface; no new HTTP route or model tool is enabled."""
    def __init__(self, store):
        self.store = store

    @staticmethod
    async def _require(scope, authorization, permission):
        if (not isinstance(scope, AuthenticatedScope) or scope.persona_id != 'mia'
                or scope.owner != f'user:{scope.user_id}:agent:{scope.agent_id}'):
            raise PrivateAuthorizationRequired()
        await authorization.require(scope, permission)

    async def cognition(self, scope, authorization, command):
        if not isinstance(command, dict):
            raise MemoryError('invalid_input', 'Invalid cognitive command')
        permission = 'memory:read' if command.get('action') == 'status' else 'memory:manage'
        await self._require(scope, authorization, permission)
        if command.get('action') == 'status':
            result = await asyncio.to_thread(cognitive_status, self.store, scope.owner)
        else:
            result = await asyncio.to_thread(_configure_cognition, self.store, scope.owner, command)
        await self._require(scope, authorization, permission)
        return result

    async def merge(self, scope, authorization, command):
        await self._require(scope, authorization, 'memory:manage')
        result = await asyncio.to_thread(_merge, self.store, scope.owner, command)
        await self._require(scope, authorization, 'memory:manage')
        return result
