"""Shared context integration without modifying the existing context contract."""

import asyncio
from dataclasses import replace
import json
import sqlite3

from .context import ContextBuilder, ContextError, packed
from . import memory_emotion


class YoumiEmotionContextBuilder(ContextBuilder):
    def _assemble(self, scope, turn_id, limits, snapshot, inputs):
        # Private appraisal evidence is not a second block in the reply prompt.
        current = inputs.get('youmi_affect')
        if not current or '_memory_link_context' not in current:
            return super()._assemble(scope, turn_id, limits, snapshot, inputs)
        public = dict(inputs, youmi_affect={k: v for k, v in current.items() if k != '_memory_link_context'})
        bundle = super()._assemble(scope, turn_id, limits, snapshot, public)
        return replace(bundle, inputs_json=packed(inputs))

    async def build(self, scope, authorization, turn_id, query, **kwargs):
        bundle = await super().build(scope, authorization, turn_id, query, **kwargs)
        inputs = json.loads(bundle.inputs_json)
        if not (inputs.get('youmi_affect') or {}).get('affect_system'):
            return bundle
        selected = set(bundle.diagnostics()['selected_fact_ids'])
        def capture():
            with self.index.store.lock:
                if self._versions(scope) != bundle.snapshot:
                    raise ContextError('context_changed')
                return memory_emotion.evidence_context(self.index.store, scope.owner,
                    [fact for fact in inputs['facts'] if fact['id'] in selected])
        try:
            authority = await asyncio.to_thread(capture)
        except (sqlite3.Error, ValueError):
            authority = None
        if authority:
            inputs['youmi_affect']['_memory_link_context'] = authority
            bundle = replace(bundle, inputs_json=packed(inputs))
        await self.validate(bundle, authorization)
        return bundle

    async def with_affect(self, bundle, authorization, proposal):
        await self.validate(bundle, authorization)
        if '_memory_changes' in proposal:
            inputs = json.loads(bundle.inputs_json)
            def prepare():
                with self.index.store.lock:
                    if self._versions(bundle.scope) != bundle.snapshot:
                        raise ContextError('context_changed')
                    return memory_emotion.prepare(self.index.store, bundle.scope.owner, proposal,
                        (inputs.get('youmi_affect') or {}).get('_memory_link_context'))
            try:
                prepared = await asyncio.to_thread(prepare)
            except (sqlite3.Error, ValueError):
                prepared = {k: v for k, v in proposal.items() if k != '_memory_changes'}
            proposal.clear()
            proposal.update(prepared)
        return await super().with_affect(bundle, authorization, proposal)
