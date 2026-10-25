"""Owner-scoped switches; retained data is never erased by disabling a feature."""

from .memory import MemoryError


DEFAULTS = {'memory_recall': True, 'personality_growth': True, 'interaction_events': True, 'companion_emotion': False}


def settings(store, owner):
    with store.lock:
        row = store.db.execute('SELECT memory_recall,personality_growth,interaction_events,companion_emotion '
                               'FROM mia_feature_settings WHERE owner=?', (owner,)).fetchone()
        return {key: bool(row[key]) if row else value for key, value in DEFAULTS.items()}


def update(store, owner, values):
    if (not isinstance(values, dict) or not values or set(values) - DEFAULTS.keys()
            or any(type(value) is not bool for value in values.values())):
        raise MemoryError('invalid_input', 'Invalid feature settings')
    with store.lock, store.db:
        owner = store._owner(owner)
        store.db.execute('INSERT OR IGNORE INTO mia_feature_settings(owner) VALUES (?)', (owner,))
        for name, value in values.items():
            store.db.execute('UPDATE mia_feature_settings SET ' + name + '=? WHERE owner=?', (value, owner))
        if 'companion_emotion' in values:
            from . import emotional_system
            system = emotional_system.status(store, owner)
            if system['version']:
                emotional_system._write(store, owner, values['companion_emotion'], system['dimensions'],
                                        system['values'], system['version'])
                emotional_system._record(store, owner, 'configure', {'enabled': values['companion_emotion']})
        store.db.execute('UPDATE owners SET revision=revision+1 WHERE owner=?', (owner,))
        store._cancel_jobs(owner)
    return settings(store, owner)


def status(store, owner):
    from .emotions import consent
    from .affect import state
    from .character import status as character_status
    with store.lock, store.db:
        store._owner(owner)
        return {'character': character_status(store, owner), 'features': settings(store, owner), 'paused': store.snapshot(owner)['paused'],
                'emotion_tracking': consent(store, owner), 'mia_emotion': state(store, owner),
                'counts': {name: store.db.execute('SELECT COUNT(*) FROM ' + table + ' WHERE owner=?',
                    (owner,)).fetchone()[0] for name, table in
                    [('memories', 'memories'), ('traits', 'traits'), ('events', 'mia_events')]}}
