"""Bounded cognitive policy, independent of emotional state and fact authority.

The four-bit direction/elastic/tanh concepts are based on the pinned Angel
Memory SoulState design. This is an original canonical-storage integration;
energy regresses to zero, not to a physical parameter's baseline.
"""

import json
import logging
import math
import sqlite3

from .proposal import ExtractionError


DIMENSIONS = ('recall', 'impression', 'expression', 'creativity')


def effective(dimensions, energy):
    values = {}
    for key, config in dimensions.items():
        baseline = config['baseline']
        value = baseline
        if config['mode'] == 'dynamic':
            e = energy[key]
            width = config['maximum'] - baseline if e >= 0 else baseline - config['minimum']
            value += width * math.tanh(config['sensitivity'] * e)
        value = max(config['minimum'], min(config['maximum'], value))
        values[key] = int(round(value)) if key in {'recall', 'impression'} else round(value, 2)
    return values


def validate_adjustment(value, sources, context):
    state = getattr(context, 'cognition', None)
    if (not state or not state['enabled'] or not getattr(context, 'reflection', None)
            or not isinstance(value, dict) or set(value) != {'code', 'quote'}
            or not isinstance(value['code'], str) or len(value['code']) != 4
            or any(bit not in '01' for bit in value['code']) or len(sources) != 1
            or value['quote'] != sources[0]):
        raise ExtractionError('source_semantic', 'Cognitive adjustment requires the complete current source')
    return dict(code=value['code'], version=state['version'])


def commit_adjustment(store, job, structured):
    """Called inside the successful job transaction after source validation.

    The job/checkpoint is the idempotency boundary. A changed admin version skips
    this advice; a cognitive storage fault cannot roll back validated facts.
    """
    adjustment = structured.get('_cognitive_adjustment')
    if adjustment is None:
        return False
    if not store.db.in_transaction:
        raise ValueError('Cognition requires the successful task transaction')
    store.db.execute('SAVEPOINT youmi_cognitive_adjustment')
    changed = False
    try:
        from .memory_state import cognitive_status
        current = cognitive_status(store, job['owner'])
        if current['enabled'] and current['version'] == adjustment['version']:
            energy = dict(current['energy'])
            for key, bit in zip(DIMENSIONS, adjustment['code']):
                if current['dimensions'][key]['mode'] == 'dynamic':
                    old = energy[key]
                    delta = (1 if bit == '1' else -1) * math.exp(-.1 * abs(old)) - .1 * old
                    energy[key] = round(max(-20, min(20, old + delta)), 6)
            if energy != current['energy']:
                changed = bool(store.db.execute('UPDATE youmi_cognitive_state SET energy_json=?,version=version+1,updated_at=? '
                    'WHERE owner=? AND version=?', (json.dumps(energy), int(store.clock()), job['owner'], current['version'])).rowcount)
    except (sqlite3.Error, ValueError, KeyError, TypeError) as error:
        store.db.execute('ROLLBACK TO youmi_cognitive_adjustment')
        logging.getLogger(__name__).warning('Youmi cognition retained previous state (%s)', type(error).__name__)
    finally:
        store.db.execute('RELEASE youmi_cognitive_adjustment')
    return changed
