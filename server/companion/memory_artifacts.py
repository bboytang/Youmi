"""Evidence-backed profiles, notes and maintenance previews; no model authority."""

import asyncio
import hashlib
import json
import re

from .memory import MemoryError, _safe_text
from .memory_state import YoumiMemoryState
from .proposal import ExtractionError


PROFILE_KEYS = frozenset({'alias', 'attribute', 'skill', 'relationship', 'project'})
KINDS = {'profile': 'profile', 'note': 'note', 'propose_retirement': 'retirement', 'propose_merge': 'merge'}
CAPACITY = 128


def versions(store, owner):
    return {(r['kind'], r['item_key']): r['version'] for r in store.db.execute(
        'SELECT kind,item_key,version FROM youmi_memory_artifacts WHERE owner=? LIMIT ?', (owner, CAPACITY + 1))}


def validate_actions(actions, context):
    known, seen, result = {r['id']: r for r in context}, set(), []
    current = getattr(context, 'artifact_versions', {})
    for action in actions:
        if set(action) != {'action', 'key', 'title', 'evidence'}:
            raise ExtractionError('field_schema', 'Invalid artifact action')
        kind = KINDS[action['action']]
        key, title, proofs = action['key'], action['title'], action['evidence']
        if (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}', key)
                or (kind == 'profile' and key not in PROFILE_KEYS) or (kind, key) in seen):
            raise ExtractionError('field_schema', 'Invalid or duplicate artifact key')
        seen.add((kind, key))
        title = _safe_text(title, 64, 'artifact title')
        if not isinstance(proofs, list) or not 1 <= len(proofs) <= 8:
            raise ExtractionError('field_schema', 'Artifact requires 1 to 8 sources')
        if (kind == 'retirement' and len(proofs) != 1) or (kind == 'merge' and len(proofs) != 2):
            raise ExtractionError('field_schema', 'Invalid maintenance source count')
        evidence, identifiers = [], set()
        for proof in proofs:
            if not isinstance(proof, dict) or set(proof) != {'id', 'quote'}:
                raise ExtractionError('field_schema', 'Invalid artifact evidence')
            identifier = proof['id']
            fact = known.get(identifier) if isinstance(identifier, str) else None
            if fact and fact.get('pinned') and kind in {'retirement', 'merge'}:
                raise ExtractionError('source_semantic', 'Pinned memories cannot be maintenance candidates')
            if (fact is None or identifier in identifiers or not fact.get('evidence')
                    or proof['quote'] != fact['content']):
                raise ExtractionError('source_semantic', 'Artifact requires complete owned factual evidence')
            # Keep the full sentence and negation; model summaries never become facts.
            _safe_text(proof['quote'], 512, 'artifact evidence')
            identifiers.add(identifier)
            evidence.append(dict(id=identifier, content_version=fact['content_version'],
                evidence_version=fact['evidence_version']))
        if not any(title in known[item['id']]['content'] for item in evidence):
            raise ExtractionError('source_semantic', 'Artifact title must occur in its evidence')
        result.append(dict(kind=kind, key=key, title=title, version=current.get((kind, key), 0), evidence=evidence))
    if len(set(current) | seen) > CAPACITY:
        raise ExtractionError('field_schema', 'Artifact capacity exceeded')
    return result


def commit_in_transaction(store, job, actions):
    """Only the authorized completed job's surrounding transaction may call this."""
    if not store.db.in_transaction:
        raise ValueError('Artifacts require the completed job transaction')
    db, owner = store.db, job['owner']
    for item in actions:
        previous = db.execute('SELECT id,version FROM youmi_memory_artifacts WHERE owner=? AND kind=? AND item_key=?',
            (owner, item['kind'], item['key'])).fetchone()
        if (previous['version'] if previous else 0) != item['version']:
            raise MemoryError('evidence_changed', 'Artifact changed')
        for proof in item['evidence']:
            if item['kind'] in {'retirement', 'merge'} and db.execute('SELECT 1 FROM youmi_memory_pins WHERE memory_id=?', (proof['id'],)).fetchone():
                raise MemoryError('evidence_changed', 'Pinned memory cannot be retired or merged')
            target = db.execute('SELECT x.content_version,x.evidence_version FROM memories m '
                'JOIN mia_memory_meta x ON x.memory_id=m.id WHERE m.owner=? AND m.id=? '
                "AND m.confirmed=1 AND x.state='active' AND EXISTS (SELECT 1 FROM sources s WHERE s.memory_id=m.id)",
                (owner, proof['id'])).fetchone()
            if target is None or list(target) != [proof['content_version'], proof['evidence_version']]:
                raise MemoryError('evidence_changed', 'Artifact evidence changed')
        # Replacement of the whole view keeps the ID and creation time, while
        # cascading old links. It cannot preserve a partly outdated note.
        identifier = hashlib.sha256(json.dumps([owner, item['kind'], item['key']]).encode()).hexdigest()[:32]
        created = db.execute('SELECT created_at FROM youmi_memory_artifacts WHERE id=?', (identifier,)).fetchone()
        db.execute('DELETE FROM youmi_memory_artifacts WHERE id=? AND owner=?', (identifier, owner))
        if db.execute('SELECT COUNT(*) FROM youmi_memory_artifacts WHERE owner=?', (owner,)).fetchone()[0] >= CAPACITY:
            raise MemoryError('invalid_input', 'Artifact capacity exceeded')
        now = int(store.clock())
        db.execute('INSERT INTO youmi_memory_artifacts VALUES (?,?,?,?,?,?,?,?,?)',
            (identifier, owner, item['kind'], item['key'], item['title'], item['version'] + 1, job['id'],
             created[0] if created else now, now))
        for position, proof in enumerate(item['evidence']):
            db.execute('INSERT INTO youmi_memory_artifact_evidence VALUES (?,?,?,?,?)',
                (identifier, proof['id'], proof['content_version'], proof['evidence_version'], position))
    return bool(actions)


class YoumiMemoryArtifacts:
    """Explicit internal operations; no default prompt injection or HTTP routes."""
    def __init__(self, store):
        self.store = store

    def _version(self, owner):
        row = self.store.db.execute('SELECT o.revision,s.content_epoch,COALESCE(f.memory_recall,1) '
            'FROM owners o JOIN mia_scope_versions s ON s.owner=o.owner '
            'LEFT JOIN mia_feature_settings f ON f.owner=o.owner WHERE o.owner=?', (owner,)).fetchone()
        return tuple(row) if row else (0, 0, 1)

    def _list(self, owner):
        with self.store.lock:
            # One read snapshot also works when another process edits evidence.
            with self.store.db:
                self.store.db.execute('BEGIN')
                version = self._version(owner)
                if not version[2]:
                    return version, []
                result = []
                for item in self.store.db.execute('SELECT * FROM youmi_memory_artifacts WHERE owner=? ORDER BY kind,item_key', (owner,)):
                    sources = [dict(row) for row in self.store.db.execute(
                        'SELECT m.id,m.content,e.content_version,e.evidence_version FROM youmi_memory_artifact_evidence e '
                        'JOIN memories m ON m.id=e.memory_id JOIN mia_memory_meta x ON x.memory_id=m.id '
                        "WHERE e.artifact_id=? AND m.owner=? AND m.confirmed=1 AND x.state='active' "
                        'AND x.content_version=e.content_version AND x.evidence_version=e.evidence_version ORDER BY e.position',
                        (item['id'], owner))]
                    count = self.store.db.execute('SELECT COUNT(*) FROM youmi_memory_artifact_evidence WHERE artifact_id=?',
                        (item['id'],)).fetchone()[0]
                    if not sources or len(sources) != count:
                        continue
                    result.append(dict(id=item['id'], kind=item['kind'], key=item['item_key'], title=item['title'],
                        version=item['version'], evidence=sources, body='\n'.join(r['content'] for r in sources),
                        preview_only=item['kind'] in {'retirement', 'merge'}))
                return version, result

    async def list(self, scope, authorization):
        await YoumiMemoryState._require(scope, authorization, 'memory:read')
        version, result = await asyncio.to_thread(self._list, scope.owner)
        await YoumiMemoryState._require(scope, authorization, 'memory:read')
        with self.store.lock:
            if self._version(scope.owner) != version:
                raise MemoryError('memory_changed', 'Artifact evidence changed during read')
        return result

    def _remove(self, owner, identifier, version):
        with self.store.lock, self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            changed = self.store.db.execute('DELETE FROM youmi_memory_artifacts WHERE owner=? AND id=? AND version=?',
                (owner, identifier, version)).rowcount
            if not changed:
                raise MemoryError('conflict', 'Artifact changed or is unavailable')
            self.store.db.execute('UPDATE owners SET revision=revision+1 WHERE owner=?', (owner,))
            self.store._cancel_jobs(owner)
        with self.store.lock:
            self.store._purge()
        return {'deleted': True}

    async def remove(self, scope, authorization, identifier, version):
        await YoumiMemoryState._require(scope, authorization, 'memory:manage')
        identifier = _safe_text(identifier, 64, 'artifact id')
        if type(version) is not int or version < 1:
            raise MemoryError('invalid_input', 'Invalid artifact version')
        result = await asyncio.to_thread(self._remove, scope.owner, identifier, version)
        await YoumiMemoryState._require(scope, authorization, 'memory:manage')
        return result
