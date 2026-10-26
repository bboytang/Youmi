"""Read-only, owner-scoped projection of the existing authoritative memory DB."""

import asyncio
from contextlib import closing
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from .authorization import AuthenticatedScope, PrivateAuthorizationRequired
from .embedding import EmbeddingUnavailable


MAX_RECORDS = 10000
MAX_CORPUS_BYTES = 8 * 1024 * 1024
BASE = (' FROM memories m JOIN mia_memory_meta x ON x.memory_id=m.id '
        "WHERE m.owner=? AND x.persona_id=? AND m.confirmed=1 AND x.state='active'")
COLUMNS = ('m.id,m.content,m.category,m.created_at,m.updated_at,'
           'x.content_version,x.evidence_version,x.entities,x.keywords,x.trigger_terms,x.emotional_weight')


@dataclass(frozen=True)
class YoumiMemoryCorpus:
    owner: str
    persona_id: str
    version: tuple
    enabled: bool
    records: tuple


class YoumiMemoryRepository:
    def __init__(self, store):
        self.store = store
        with store.lock:
            path = store.db.execute('PRAGMA database_list').fetchone()['file']
        self._uri = Path(path).as_uri() + '?mode=ro'

    async def _require(self, scope, authorization):
        if (not isinstance(scope, AuthenticatedScope) or scope.persona_id != 'mia'
                or scope.owner != f'user:{scope.user_id}:agent:{scope.agent_id}'):
            raise PrivateAuthorizationRequired()
        await authorization.require(scope, 'memory:read')

    @staticmethod
    def _state(db, scope):
        row = db.execute('SELECT o.revision,s.content_epoch,COALESCE(f.memory_recall,1) '
            'FROM owners o JOIN mia_scope_versions s ON s.owner=o.owner '
            'LEFT JOIN mia_feature_settings f ON f.owner=o.owner WHERE o.owner=?', (scope.owner,)).fetchone()
        return (tuple(row[:2]), bool(row[2])) if row else ((0, 0), True)

    def _capture(self, scope):
        with closing(sqlite3.connect(self._uri, uri=True)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN')
            version, enabled = self._state(db, scope)
            records, size = [], 0
            if enabled:
                for row in db.execute('SELECT ' + COLUMNS + BASE + ' ORDER BY m.id LIMIT ?',
                                      (scope.owner, scope.persona_id, MAX_RECORDS + 1)):
                    record = dict(row)
                    size += sum(len(value.encode('utf-8')) for value in record.values() if isinstance(value, str))
                    if len(records) == MAX_RECORDS or size > MAX_CORPUS_BYTES:
                        raise EmbeddingUnavailable('memory_shadow_budget_exceeded')
                    records.append(record)
            return YoumiMemoryCorpus(scope.owner, scope.persona_id, version, enabled, tuple(records))

    async def capture(self, scope, authorization):
        await self._require(scope, authorization)
        corpus = await asyncio.to_thread(self._capture, scope)
        await self.validate(scope, authorization, corpus)
        return corpus

    def _validate(self, scope, corpus):
        if (scope.owner, scope.persona_id) != (corpus.owner, corpus.persona_id):
            raise PrivateAuthorizationRequired()
        with self.store.lock:
            if self._state(self.store.db, scope) != (corpus.version, corpus.enabled):
                raise EmbeddingUnavailable('memory_changed')

    async def validate(self, scope, authorization, corpus):
        await self._require(scope, authorization)
        await asyncio.to_thread(self._validate, scope, corpus)

    def _get_by_ids(self, scope, corpus, ids):
        if (scope.owner, scope.persona_id) != (corpus.owner, corpus.persona_id):
            raise PrivateAuthorizationRequired()
        with closing(sqlite3.connect(self._uri, uri=True)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN')
            if self._state(db, scope) != (corpus.version, corpus.enabled):
                raise EmbeddingUnavailable('memory_changed')
            if not corpus.enabled or not ids:
                return []
            marks = ','.join('?' for _ in ids)
            rows = db.execute('SELECT ' + COLUMNS + BASE + f' AND m.id IN ({marks})',
                              (scope.owner, scope.persona_id, *ids)).fetchall()
            if len(rows) != len(ids):
                # Foreign, deleted and non-retrievable IDs are indistinguishable.
                raise EmbeddingUnavailable('memory_changed')
            records = {}
            for row in rows:
                record = dict(row)
                source = db.execute('SELECT s.excerpt,s.session_id,s.fingerprint,sm.turn_id,sm.message_id,sm.source_type '
                    'FROM sources s LEFT JOIN mia_source_meta sm ON sm.memory_id=s.memory_id '
                    'AND sm.session_id=s.session_id AND sm.fingerprint=s.fingerprint '
                    'WHERE s.memory_id=? ORDER BY s.created_at DESC,s.rowid DESC LIMIT 1', (row['id'],)).fetchone()
                counts = db.execute('SELECT COUNT(DISTINCT session_id),COUNT(DISTINCT fingerprint) '
                                    'FROM sources WHERE memory_id=?', (row['id'],)).fetchone()
                record.update(source=dict(source) if source else None,
                              evidence_session_count=counts[0], evidence_source_count=counts[1])
                snapshot = db.execute('SELECT t.affect_json,t.cognition_json,t.captured_at,s.content_version '
                    'FROM youmi_memory_source_state s JOIN youmi_memory_turn_state t ON t.owner=s.owner '
                    'AND t.session_id=s.session_id AND t.turn_id=s.turn_id '
                    'WHERE s.owner=? AND s.memory_id=? AND s.session_id=? AND s.fingerprint=?',
                    (scope.owner, row['id'], source['session_id'], source['fingerprint'])).fetchone() if source else None
                if snapshot:
                    record['state_snapshot'] = dict(kind='completed_turn', captured_at=snapshot['captured_at'],
                        content_version=snapshot['content_version'], affect=json.loads(snapshot['affect_json']) if snapshot['affect_json'] else None,
                        cognition=json.loads(snapshot['cognition_json']) if snapshot['cognition_json'] else None)
                else:
                    record['state_snapshot'] = None
                records[row['id']] = record
            return [records[memory_id] for memory_id in ids]

    async def get_by_ids(self, scope, authorization, corpus, ids):
        if (not isinstance(ids, list) or len(ids) > 24
                or any(not isinstance(value, str) or not 1 <= len(value) <= 128 for value in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError('Invalid memory IDs')
        await self._require(scope, authorization)
        records = await asyncio.to_thread(self._get_by_ids, scope, corpus, ids)
        await self.validate(scope, authorization, corpus)
        return records
