"""Seven-day owner-scoped completed turns for cross-device continuity."""

import json
import re

from .memory import SECRET


RETENTION = 7 * 24 * 60 * 60
MAX_CONTEXT_BYTES = 12000
MAX_TURNS = 12
MAX_SUMMARY_TURNS = 8
MAX_SUMMARY_SOURCE_BYTES = 24000


def _settings(store, owner):
    store._owner(owner)
    store.db.execute('INSERT OR IGNORE INTO youmi_shared_history_settings(owner) VALUES (?)', (owner,))
    return store.db.execute('SELECT paused,revision FROM youmi_shared_history_settings WHERE owner=?',
                            (owner,)).fetchone()


def _prune(store, now):
    owners = [row[0] for row in store.db.execute(
        'SELECT DISTINCT owner FROM youmi_shared_history_turns WHERE expires_at<=?', (now,))]
    store.db.execute('DELETE FROM youmi_shared_history_turns WHERE expires_at<=?', (now,))
    for owner in owners:
        store.db.execute('DELETE FROM youmi_shared_history_summaries WHERE owner=?', (owner,))
        store.db.execute('UPDATE youmi_shared_history_settings SET revision=revision+1 WHERE owner=?', (owner,))


def record_locked(store, owner, turn_key, channel, user_text, assistant_text):
    """Caller holds the store lock and transaction; duplicates must be identical."""
    if (not isinstance(turn_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', turn_key)
            or channel not in {'qq', 'passport'} or not isinstance(user_text, str) or not user_text.strip()
            or len(user_text) > 4096 or not isinstance(assistant_text, str) or not assistant_text.strip()
            or len(assistant_text) > 16000):
        raise ValueError('invalid_shared_history_turn')
    state = _settings(store, owner)
    if state['paused'] or SECRET.search(user_text) or SECRET.search(assistant_text):
        return False
    old = store.db.execute('SELECT owner,channel,user_text,assistant_text FROM youmi_shared_history_turns '
                           'WHERE turn_key=?', (turn_key,)).fetchone()
    if old is not None:
        if tuple(old) != (owner, channel, user_text, assistant_text):
            raise ValueError('shared_history_turn_conflict')
        return False
    now = int(store.clock())
    store.db.execute('INSERT INTO youmi_shared_history_turns '
        '(owner,turn_key,channel,user_text,assistant_text,created_at,expires_at) VALUES (?,?,?,?,?,?,?)',
        (owner, turn_key, channel, user_text, assistant_text, now, now + RETENTION))
    return True


def configure(store, owner, *, paused=None, clear=False):
    if paused is not None and type(paused) is not bool or type(clear) is not bool:
        raise ValueError('invalid_shared_history_setting')
    with store.lock, store.db:
        state = _settings(store, owner)
        if clear:
            store.db.execute('DELETE FROM youmi_shared_history_turns WHERE owner=?', (owner,))
            store.db.execute('DELETE FROM youmi_shared_history_summaries WHERE owner=?', (owner,))
        changed = clear or paused is not None and bool(state['paused']) != paused
        if changed:
            store.db.execute('UPDATE youmi_shared_history_settings SET paused=?,revision=revision+1 WHERE owner=?',
                             (int(paused) if paused is not None else state['paused'], owner))
        result = _settings(store, owner)
        return {'paused': bool(result['paused']), 'revision': result['revision']}


def capture(store, owner, channel):
    """Freeze opposite-channel turn IDs; never persist the texts in a request ledger."""
    if channel not in {'qq', 'passport'}:
        raise ValueError('invalid_shared_history_channel')
    with store.lock, store.db:
        _prune(store, int(store.clock()))
        state = _settings(store, owner)
        if state['paused']:
            return {'revision': state['revision'], 'paused': True, 'ids': [], 'summary_ids': [], 'expires_at': None}
        rows = store.db.execute('SELECT sequence,channel,user_text,assistant_text,created_at,expires_at '
            'FROM youmi_shared_history_turns WHERE owner=? AND channel<>? ORDER BY sequence DESC LIMIT ?',
            (owner, channel, MAX_TURNS + MAX_SUMMARY_TURNS)).fetchall()
        selected = []
        for row in rows:
            if len(selected) >= MAX_TURNS:
                break
            candidate = [dict(item) for item in reversed(selected + [row])]
            if len(json.dumps(candidate, ensure_ascii=False, separators=(',', ':')).encode()) > MAX_CONTEXT_BYTES:
                break
            selected.append(row)
        older = []
        for row in rows[len(selected):]:
            if len(older) >= MAX_SUMMARY_TURNS:
                break
            candidate = [dict(item) for item in reversed(older + [row])]
            if len(json.dumps(candidate, ensure_ascii=False, separators=(',', ':')).encode()) > MAX_SUMMARY_SOURCE_BYTES:
                break
            older.append(row)
        selected.reverse()
        older.reverse()
        return {'revision': state['revision'], 'paused': False, 'ids': [row['sequence'] for row in selected],
                'summary_ids': [row['sequence'] for row in older],
                'expires_at': min((row['expires_at'] for row in selected + older), default=None)}


def render(store, owner, snapshot):
    prune(store)
    with store.lock, store.db:
        now = int(store.clock())
        state = _settings(store, owner)
        if state['revision'] != snapshot['revision'] or bool(state['paused']) != snapshot['paused']:
            raise ValueError('shared_history_changed')
        result = []
        for sequence in snapshot['ids']:
            row = store.db.execute('SELECT sequence,channel,user_text,assistant_text,created_at,expires_at '
                'FROM youmi_shared_history_turns WHERE owner=? AND sequence=? AND expires_at>?',
                (owner, sequence, now)).fetchone()
            if row is None:
                raise ValueError('shared_history_changed')
            result.append(dict(row))
        return result


def summary_sources(store, owner, snapshot):
    return render(store, owner, dict(snapshot, ids=snapshot['summary_ids']))


def cached_summary(store, owner, channel, snapshot):
    if not snapshot['summary_ids'] or snapshot['paused']:
        return None
    # Validate provenance and expiration before consulting the cache.
    summary_sources(store, owner, snapshot)
    with store.lock:
        row = store.db.execute('SELECT source_ids,revision,summary,expires_at FROM youmi_shared_history_summaries '
                               'WHERE owner=? AND channel=?', (owner, channel)).fetchone()
        if (row is None or row['revision'] != snapshot['revision'] or row['expires_at'] <= int(store.clock())
                or json.loads(row['source_ids']) != snapshot['summary_ids']):
            return None
        return row['summary']


def save_summary(store, owner, channel, snapshot, summary):
    if (channel not in {'qq', 'passport'} or not snapshot['summary_ids'] or snapshot['paused']
            or not isinstance(summary, str) or not summary.strip() or len(summary) > 2000 or SECRET.search(summary)):
        raise ValueError('invalid_shared_history_summary')
    with store.lock, store.db:
        state = _settings(store, owner)
        if state['revision'] != snapshot['revision'] or state['paused']:
            raise ValueError('shared_history_changed')
        now = int(store.clock())
        expiries = []
        for sequence in snapshot['summary_ids']:
            row = store.db.execute('SELECT expires_at FROM youmi_shared_history_turns '
                'WHERE owner=? AND sequence=? AND channel<>? AND expires_at>?',
                (owner, sequence, channel, now)).fetchone()
            if row is None:
                raise ValueError('shared_history_changed')
            expiries.append(row['expires_at'])
        source_ids = json.dumps(snapshot['summary_ids'], separators=(',', ':'))
        existing = store.db.execute('SELECT source_ids FROM youmi_shared_history_summaries '
                                    'WHERE owner=? AND channel=?', (owner, channel)).fetchone()
        if existing is not None and max(json.loads(existing['source_ids'])) > max(snapshot['summary_ids']):
            return False
        store.db.execute('INSERT INTO youmi_shared_history_summaries(owner,channel,source_ids,revision,summary,expires_at) '
            'VALUES (?,?,?,?,?,?) ON CONFLICT(owner,channel) DO UPDATE SET source_ids=excluded.source_ids, '
            'revision=excluded.revision,summary=excluded.summary,expires_at=excluded.expires_at',
            (owner, channel, source_ids, snapshot['revision'], summary.strip(), min(expiries)))
        return True


def prune(store):
    with store.lock, store.db:
        now = int(store.clock())
        _prune(store, now)
        next_expiry = store.db.execute('SELECT MIN(expires_at) FROM youmi_shared_history_turns').fetchone()[0]
        return min(30, max(1, next_expiry - now)) if next_expiry is not None else 30
