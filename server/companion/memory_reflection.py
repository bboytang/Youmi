"""Versioned reflection actions, validated by the existing evidence boundary.

This adapter does not execute actions or start a model request. The worker keeps
ownership of completion authorization, request accounting and atomic fact writes.
"""

import json
import re

from .extraction import validate_output
from .proposal import ExtractionError
from .memory_artifacts import KINDS, validate_actions


MEMORY_FIELDS = frozenset({
    'category', 'content', 'source_index', 'source_quote', 'kind', 'subject',
    'value', 'sensitive', 'entities', 'keywords', 'trigger_terms', 'anchor',
    'importance_weight', 'importance_evidence', 'relations', 'reconciliation',
})
RECONCILIATION_FIELDS = frozenset({
    'form', 'kind', 'target_id', 'old_quote', 'evidence', 'old_value', 'new_value', 'anchor',
})


def validate_proposal(value, sources, context):
    """Accept legacy extraction or explicit V1 actions; never guess a version.

    Return the same validated schema used by durable jobs, so restart/retry does
    not require storing a second authority or replaying raw reflection output.
    """
    raw = value
    if isinstance(value, str):
        if len(value) > 24000:
            raise ExtractionError('parse', 'Extraction is too large')
        fenced = re.fullmatch(r'(?:```(?:json)?\s*\n)?(.*?)\n```', value.strip(), re.S | re.I)
        try:
            raw = json.loads(fenced[1] if fenced else value)
        except (ValueError, RecursionError):
            return validate_output(value, sources, context)
    if not isinstance(raw, dict) or not {'reflection_version', 'memory_actions'}.intersection(raw):
        if getattr(context, 'reflection', None) and not getattr(context, 'reflection_fastpath', False):
            raise ExtractionError('field_schema', 'Enabled reflection requires version 1 actions')
        return validate_output(value, sources, context)
    if (set(raw) - {'reflection_version', 'memory_actions', 'trait', 'observations'}
            or type(raw.get('reflection_version')) is not int or raw['reflection_version'] != 1):
        raise ExtractionError('field_schema', 'Unsupported reflection contract')
    actions = raw.get('memory_actions')
    if not isinstance(actions, list) or len(actions) > 16:
        raise ExtractionError('field_schema', 'Reflection requires at most 16 actions')
    memories, artifacts = [], []
    for action in actions:
        if isinstance(action, dict) and isinstance(action.get('action'), str) and action['action'] in KINDS:
            artifacts.append(action)
            continue
        if (not isinstance(action, dict) or set(action) != {'action', 'memory'}
                or not isinstance(action['action'], str) or action['action'] not in {'remember', 'revise'}):
            raise ExtractionError('field_schema', 'Unsupported reflection action')
        memory = action['memory']
        if not isinstance(memory, dict) or set(memory) - MEMORY_FIELDS:
            raise ExtractionError('field_schema', 'Invalid reflection memory fields')
        proof = memory.get('reconciliation')
        if action['action'] == 'revise':
            if not isinstance(proof, dict) or set(proof) - RECONCILIATION_FIELDS:
                raise ExtractionError('field_schema', 'Revision requires source-grounded reconciliation')
        elif 'reconciliation' in memory:
            raise ExtractionError('field_schema', 'Remember cannot revise an existing fact')
        # Deliberately do not truncate by cognition: explicit changes must all
        # reach evidence validation before any writes take place.
        memories.append(memory)
    normalized = {'memories': memories, 'trait': raw.get('trait')}
    if 'observations' in raw:
        normalized['observations'] = raw['observations']
    result = validate_output(normalized, sources, context)
    if artifacts:
        if getattr(context, 'reflection', None) and not context.reflection['maintenance_due']:
            raise ExtractionError('field_schema', 'Maintenance is not due for this task')
        derived = validate_actions(artifacts, context)
        referenced = {proof['id'] for item in derived for proof in item['evidence']}
        changed = {item['reconciliation']['target_id'] for item in result['memories'] if 'reconciliation' in item}
        known = {fact['id']: fact for fact in context}
        if (referenced & changed or any(record['content'] == known[identifier]['content']
                for record in result['memories'] for identifier in referenced)):
            raise ExtractionError('source_semantic', 'Batch changes its own artifact evidence')
        result['artifacts'] = derived
    return result
