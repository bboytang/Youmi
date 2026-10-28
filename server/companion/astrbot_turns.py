"""Durable at-most-once send claims and recoverable completion in the existing SQLite."""

import json
import secrets

from .astrbot_identity import canonical, digest
from .context import ContextError
from .jobs import TurnCompleted


class TurnConflict(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


class TurnLedger:
    def __init__(self, store, builder, authorization, worker):
        self.store, self.builder = store, builder
        self.authorization, self.worker = authorization, worker

    def prune(self):
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE mia_astrbot_turns SET status=CASE WHEN status='completed' THEN status "
                "WHEN status='failed' THEN status ELSE 'expired' END,payload_json=NULL WHERE expires_at<=?",
                (int(self.store.clock()),))

    def find(self, key):
        self.prune()
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM mia_astrbot_turns WHERE turn_key=?', (key,)).fetchone()
            return dict(row) if row else None

    def create(self, key, data, scope, payload):
        now = int(self.store.clock())
        token = secrets.token_urlsafe(32)
        with self.store.lock, self.store.db:
            self.store.db.execute('INSERT OR IGNORE INTO mia_astrbot_turns '
                '(turn_key,token,attempt_id,request_hash,session_id,memory_suppressed,scope_json,status,payload_json,created_at,expires_at) '
                "VALUES (?,?,?,?,?,?,?,'prepared',?,?,?)",
                (key, token, data['attempt_id'], digest(data), payload['session'], int(payload['suppressed']),
                 canonical(scope.snapshot()), canonical(payload), now, now + 300))
        return self.find(key)

    async def authorize(self, row):
        scope = await self.authorization.restore(json.loads(row['scope_json']))
        await self.authorization.require(scope, 'memory:read')
        return scope

    def by_token(self, token, *, allow_expired=False):
        self.prune()
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM mia_astrbot_turns WHERE token=?', (token,)).fetchone()
        if row is None:
            raise TurnConflict('unknown_turn', 403)
        if row['status'] == 'expired' and not allow_expired:
            raise TurnConflict('turn_expired', 410)
        return dict(row)

    def validate(self, scope, payload):
        if tuple(payload['snapshot']) != self.builder._versions(scope):
            raise ContextError('context_changed')
        shared = payload.get('shared_history')
        if shared is not None:
            state = self.store.db.execute('SELECT paused,revision FROM youmi_shared_history_settings WHERE owner=?',
                                          (scope.owner,)).fetchone()
            if (state is None or state['revision'] != shared['revision'] or bool(state['paused']) != shared['paused']
                    or shared['expires_at'] is not None and shared['expires_at'] <= int(self.store.clock())):
                raise ContextError('context_changed')

    def claim(self, row, scope, completion, text):
        with self.store.lock, self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            current = self.store.db.execute('SELECT * FROM mia_astrbot_turns WHERE turn_key=?', (row['turn_key'],)).fetchone()
            if current['status'] != 'prepared':
                raise TurnConflict('duplicate_send')
            self.validate(scope, json.loads(current['payload_json']))
            self.store.db.execute("UPDATE mia_astrbot_turns SET status='sending',completion_id=?,text_hash=? WHERE turn_key=?",
                (completion, digest(text), row['turn_key']))

    def effects(self, row, scope, text):
        """Original affect/events and their ledger checkpoint share ONE transaction."""
        from .affect import commit
        from .events import complete
        with self.store.lock, self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            current = self.store.db.execute('SELECT * FROM mia_astrbot_turns WHERE turn_key=?', (row['turn_key'],)).fetchone()
            payload = json.loads(current['payload_json'])
            self.validate(scope, payload)
            if current['effects_json']:
                return json.loads(current['effects_json']), payload
            emotion, activated = 'unchanged', []
            if (payload['origin'] == 'user' and payload['source_kind'] in {'text', 'asr'}
                    and not payload['suppressed'] and not payload['skip_memory']):
                if payload['proposal']:
                    changed = commit(self.store, scope.owner, payload['session'], row['turn_key'],
                        payload['excerpt'], payload['proposal'], payload['affect_version'])
                    emotion = 'committed' if changed else 'unchanged'
                if payload['pending_events']:
                    activated = complete(self.store, scope.owner, payload['session'], row['turn_key'],
                                         payload['pending_events'], text[:512])
                if payload['excerpt']:
                    from .memory_state import try_capture_turn
                    try_capture_turn(self.store, scope.owner, payload['session'], row['turn_key'], payload['excerpt'])
                if payload.get('shared_input') and text.strip():
                    from .shared_history import record_locked
                    record_locked(self.store, scope.owner, row['turn_key'],
                                  'passport' if payload.get('passport_handle') else 'qq', payload['shared_input'], text)
            effects = {'emotion': emotion, 'events': activated}
            # Events may advance their own epoch; save it with the same commit.
            payload['snapshot'] = self.builder._versions(scope)
            self.store.db.execute('UPDATE mia_astrbot_turns SET effects_json=?,payload_json=? WHERE turn_key=?',
                (canonical(effects), canonical(payload), row['turn_key']))
            return effects, payload

    async def complete(self, row, scope, data):
        if row['completion_id'] != data['completion_id'] or row['text_hash'] != digest(data['final_text']):
            raise TurnConflict('completion_conflict')
        if row['status'] in {'completed', 'failed'}:
            return json.loads(row['result_json'])
        if row['status'] != 'sending':
            raise TurnConflict('turn_not_sending')
        await self.authorization.require(scope, 'memory:write')
        if data['result'] == 'failed':
            if row['effects_json']:
                raise TurnConflict('completion_conflict')
            return self.finish(row, {'status': 'failed', 'memory_job': {'status': 'not_submitted'},
                                     'emotion': 'unchanged', 'events': []})
        # No awaits while holding the store's shared SQLite transaction/lock.
        effects, payload = self.effects(row, scope, data['final_text'])
        memory = {'status': 'not_applicable'}
        if (payload['origin'] == 'user' and payload['source_kind'] in {'text', 'asr'} and payload['excerpt']
                and not payload['suppressed'] and not payload['skip_memory']):
            if self.worker is None or self.worker.closing:
                raise TurnConflict('worker_unavailable', 503)
            event = TurnCompleted(scope, payload['session'], row['turn_key'], row['turn_key'],
                                  payload['excerpt'], payload['snapshot'][0], payload['priority'])
            memory = await self.worker.submit(event)
            if memory['status'] in {'queue_full', 'closing', 'unavailable'}:
                raise TurnConflict('worker_unavailable', 503)
        return self.finish(row, {'status': 'completed', 'memory_job': memory, **effects})

    def finish(self, row, result):
        result = dict(result, turn_key=row['turn_key'])
        with self.store.lock, self.store.db:
            self.store.db.execute('UPDATE mia_astrbot_turns SET status=?,result_json=?,payload_json=NULL WHERE turn_key=?',
                (result['status'], canonical(result), row['turn_key']))
        return result
