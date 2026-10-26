"""Owner-console engine selection; no schema downgrade or second fact writer."""

import json

from .memory import MemoryError


def status(store, owner):
    row = store.db.execute('SELECT engine,version FROM youmi_memory_engine WHERE owner=?', (owner,)).fetchone()
    return dict(row) if row else dict(engine='legacy', version=0)


def configure(store, owner, command):
    if (not isinstance(command, dict) or set(command) != {'action', 'engine', 'version'}
            or command['action'] != 'engine' or command['engine'] not in ('legacy', 'youmi')
            or type(command['version']) is not int or command['version'] < 0):
        raise MemoryError('invalid_input', 'Invalid engine selection')
    with store.lock, store.db:
        store.db.execute('BEGIN IMMEDIATE')
        current = status(store, owner)
        if current['version'] != command['version']:
            raise MemoryError('conflict', 'Memory engine changed')
        if current['engine'] == command['engine']:
            return current
        active = store.db.execute("SELECT 1 FROM mia_jobs WHERE owner=? AND state IN "
            "('pending','extracting','validated','retry_wait') LIMIT 1", (owner,)).fetchone()
        native = store.db.execute('SELECT 1 FROM mia_model_requests r JOIN mia_jobs j ON j.id=r.job_id '
            'WHERE j.owner=? AND r.native_finished_at IS NULL LIMIT 1', (owner,)).fetchone()
        turns = store.db.execute("SELECT scope_json FROM mia_astrbot_turns WHERE status IN ('prepared','sending') AND expires_at>?",
            (int(store.clock()),))
        if active or native or any(json.loads(row[0])['owner'] == owner for row in turns):
            raise MemoryError('conflict', 'Wait for active turns and memory tasks before switching')
        store._owner(owner)
        store.db.execute('INSERT INTO youmi_memory_engine VALUES (?,?,?,?) ON CONFLICT(owner) DO UPDATE SET '
            'engine=excluded.engine,version=excluded.version,updated_at=excluded.updated_at',
            (owner, command['engine'], current['version'] + 1, int(store.clock())))
        store.db.execute('UPDATE owners SET revision=revision+1 WHERE owner=?', (owner,))
        return status(store, owner)
