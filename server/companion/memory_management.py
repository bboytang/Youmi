"""Console-only memory operations after the manager has proved the agent owner.

The console has full management rights for that agent. This is deliberately not
a device scope, model tool, or alternative authentication path.
"""

from .memory import MemoryError, _text


PAGE_SIZE = 50


def _fields(command, required, optional=()):
    if not isinstance(command, dict) or not set(required) <= set(command) or set(command) - set(required) - set(optional):
        raise MemoryError('invalid_input', 'Invalid memory management command')


def _offset(command):
    offset = command.get('offset', 0)
    if type(offset) is not int or not 0 <= offset <= 2147483647:
        raise MemoryError('invalid_input', 'Invalid page offset')
    return offset


def _revision(store, owner):
    row = store.db.execute('SELECT revision FROM owners WHERE owner=?', (owner,)).fetchone()
    return row[0] if row else 0


def _record(row):
    return dict(row, confirmed=bool(row['confirmed']), pinned=bool(row['pinned']))


def _read(store, owner, command):
    from .features import settings as feature_settings
    from .memory_emotion import settings as linkage_settings
    from .memory_reflection_policy import status as reflection_status
    from .memory_state import cognitive_status

    # One snapshot, including when another process corrects or forgets a source.
    with store.lock, store.db:
        store.db.execute('BEGIN')
        action = command['action']
        revision = _revision(store, owner)
        if action == 'status':
            _fields(command, {'action'})
            reflection = reflection_status(store, owner)
            reflection.pop('owner', None)
            from .memory_cache_lifecycle import YoumiManagedMemoryCache
            return dict(revision=revision, cache_cleanup=YoumiManagedMemoryCache.status(store.db, owner),
                features=feature_settings(store, owner),
                reflection=reflection, emotion_linkage=linkage_settings(store, owner),
                cognition=cognitive_status(store, owner),
                capabilities=dict(engine='legacy', emotion_ranking='shadow_only',
                    automatic_cognition=False, automatic_candidate_execution=False, permanent_memories=False))
        _fields(command, {'action', 'id'} if action == 'detail' else {'action'}, {'offset'})
        offset = _offset(command)
        base = ('SELECT m.id,m.category,m.content,m.confirmed,m.created_at,m.updated_at,'
                'x.content_version,x.evidence_version,x.state,EXISTS(SELECT 1 FROM youmi_memory_pins p WHERE p.memory_id=m.id) AS pinned FROM memories m '
                "JOIN mia_memory_meta x ON x.memory_id=m.id WHERE m.owner=? AND x.persona_id='mia'")
        if action == 'list':
            total = store.db.execute("SELECT COUNT(*) FROM memories m JOIN mia_memory_meta x ON x.memory_id=m.id "
                "WHERE m.owner=? AND x.persona_id='mia'", (owner,)).fetchone()[0]
            records = [_record(row) for row in store.db.execute(base +
                ' ORDER BY m.updated_at DESC,m.rowid DESC LIMIT ? OFFSET ?', (owner, PAGE_SIZE, offset))]
            return dict(memories=records, total=total, offset=offset, page_size=PAGE_SIZE, revision=revision)
        identifier = _text(command['id'], 64, 'memory id')
        row = store.db.execute(base + ' AND m.id=?', (owner, identifier)).fetchone()
        if row is None:
            raise MemoryError('not_found', 'Memory is unavailable')
        total = store.db.execute('SELECT COUNT(*) FROM sources WHERE memory_id=?', (identifier,)).fetchone()[0]
        sources = [dict(item) for item in store.db.execute(
            'SELECT s.session_id,s.excerpt,s.created_at,x.turn_id,x.message_id,x.source_type '
            'FROM sources s LEFT JOIN mia_source_meta x ON x.memory_id=s.memory_id '
            'AND x.session_id=s.session_id AND x.fingerprint=s.fingerprint WHERE s.memory_id=? '
            'ORDER BY s.created_at DESC,s.session_id,s.fingerprint LIMIT ? OFFSET ?', (identifier, PAGE_SIZE, offset))]
        replacements = [dict(item) for item in store.db.execute(
            'SELECT retired_id,survivor_id,retired_version,survivor_version,created_at '
            'FROM youmi_memory_replacements WHERE owner=? AND (retired_id=? OR survivor_id=?) '
            'ORDER BY created_at DESC,retired_id LIMIT ?', (owner, identifier, identifier, PAGE_SIZE))]
        return dict(memory=_record(row), sources=sources, source_total=total,
            offset=offset, page_size=PAGE_SIZE, replacements=replacements, revision=revision)


def manage(store, owner, command):
    """Called only by ManagementHandler with its server-verified owner."""
    if not isinstance(command, dict) or not isinstance(command.get('action'), str):
        raise MemoryError('invalid_input', 'Invalid memory management command')
    action = command['action']
    if action in {'status', 'list', 'detail'}:
        return _read(store, owner, command)
    if action == 'pin':
        from .memory_pins import configure
        return configure(store, owner, command)
    if action in {'edit', 'delete'}:
        _fields(command, {'action', 'id', 'expected', 'content', 'confirmed'} if action == 'edit'
            else {'action', 'id', 'expected'})
        if not isinstance(command['expected'], dict):
            raise MemoryError('invalid_input', 'Memory versions are required')
        if action == 'edit':
            if type(command['confirmed']) is not bool:
                raise MemoryError('invalid_input', 'Confirmation is required')
            store.edit_memory(owner, command['id'], command['content'], command['confirmed'], expected=command['expected'])
        else:
            store.delete_memory(owner, command['id'], expected=command['expected'])
        cleanup = store.cache_cleanup_status(owner)
        return dict(saved=True, cache_cleanup=cleanup) if cleanup['state'] != 'not_registered' else {'saved': True}
    if action in {'reflection', 'emotion_linkage', 'cognition'}:
        _fields(command, {'action', 'operation'})
        operation = command['operation']
        if not isinstance(operation, dict):
            raise MemoryError('invalid_input', 'Invalid configuration operation')
        if action == 'reflection':
            from .memory_reflection_policy import YoumiReflectionPolicy
            policy = YoumiReflectionPolicy(store)
            result = policy._status(owner) if operation == {'action': 'status'} else policy._configure(owner, operation)
            result.pop('owner', None)
            return result
        if action == 'emotion_linkage':
            from .memory_emotion import YoumiMemoryEmotion
            return YoumiMemoryEmotion(store)._manage(owner, operation)
        from .memory_state import _configure_cognition, cognitive_status
        if operation == {'action': 'status'}:
            return cognitive_status(store, owner)
        _fields(operation, {'action', 'version', 'enabled', 'dimensions'} if operation.get('action') == 'configure'
            else {'action', 'version', 'energy'})
        return _configure_cognition(store, owner, operation)
    if action in {'artifacts', 'delete_artifact'}:
        from .memory_artifacts import YoumiMemoryArtifacts
        artifacts = YoumiMemoryArtifacts(store)
        if action == 'artifacts':
            _fields(command, {'action'})
            _, items = artifacts._list(owner)
            return dict(artifacts=items)
        _fields(command, {'action', 'id', 'version'})
        identifier = _text(command['id'], 64, 'artifact id')
        if type(command['version']) is not int or command['version'] < 1:
            raise MemoryError('invalid_input', 'Invalid artifact version')
        return artifacts._remove(owner, identifier, command['version'])
    if action == 'merge':
        _fields(command, {'action', 'operation_id', 'retired_id', 'survivor_id', 'versions', 'revision'})
        from .memory_state import _merge
        return _merge(store, owner, command)
    raise MemoryError('invalid_input', 'Unknown memory management operation')
