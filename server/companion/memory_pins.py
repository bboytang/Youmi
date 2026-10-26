"""Internal owner-console retention flags using canonical memory CAS."""

from .memory import MemoryError, _text


def configure(store, owner, command):
    if (not isinstance(command, dict) or set(command) != {'action', 'id', 'pinned', 'expected'}
            or command['action'] != 'pin' or type(command['pinned']) is not bool
            or not isinstance(command['expected'], dict)):
        raise MemoryError('invalid_input', 'Invalid memory pin command')
    identifier = _text(command['id'], 64, 'memory id')
    with store.lock, store.db:
        store.db.execute('BEGIN IMMEDIATE')
        store._memory_cas(owner, identifier, command['expected'])
        row = store.db.execute('SELECT m.confirmed,x.state FROM memories m JOIN mia_memory_meta x ON x.memory_id=m.id '
            "WHERE m.owner=? AND m.id=? AND x.persona_id='mia'", (owner, identifier)).fetchone()
        if row is None:
            raise MemoryError('not_found', 'Memory is unavailable')
        if command['pinned']:
            if not row['confirmed'] or row['state'] != 'active':
                raise MemoryError('conflict', 'Only a confirmed active memory can be pinned')
            changed = store.db.execute('INSERT OR IGNORE INTO youmi_memory_pins VALUES (?,?)', (identifier, int(store.clock()))).rowcount
        else:
            changed = store.db.execute('DELETE FROM youmi_memory_pins WHERE memory_id=?', (identifier,)).rowcount
        if changed:
            store._cancel_jobs(owner)
    return dict(saved=True, pinned=command['pinned'], cache_cleanup=store.cache_cleanup_status(owner))
