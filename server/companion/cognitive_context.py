"""Shared bounded cognitive expression policy without changing persona or facts."""

from dataclasses import replace

from .context import ContextError, estimate, packed
from .memory_emotion_context import YoumiEmotionContextBuilder
from .memory_state import cognitive_status


class YoumiCognitiveContextBuilder(YoumiEmotionContextBuilder):
    def _versions(self, scope):
        previous = super()._versions(scope)
        state = cognitive_status(self.index.store, scope.owner)
        return (*previous, state['version'] if state['enabled'] else 0)

    def _assemble(self, scope, turn_id, limits, snapshot, inputs):
        bundle = super()._assemble(scope, turn_id, limits, snapshot, inputs)
        if snapshot is None:
            return bundle
        with self.index.store.lock:
            if self._versions(scope) != snapshot:
                raise ContextError('context_changed')
            state = cognitive_status(self.index.store, scope.owner)
        if not state['enabled']:
            return bundle
        effective = state['effective']
        request = bundle.request()
        # Prefix rather than append: the bridge can still isolate the existing
        # emotional guidance suffix without duplicating or removing it.
        guide = ('【认知表达策略】仅调节表达，已有角色人设、真实来源、工具授权和安全边界优先。'
            '表达篇幅倾向为' + str(effective['expression']) + '（0至1，越低越简洁，越高可提供更多相关细节）；'
            '创造倾向为' + str(effective['creativity']) + '（0至1，越低越稳健直白，越高可适度使用比喻和联想）。'
            '不为凑篇幅而啰嗦，不编造经历或事实，不朗读这些参数。\n')
        request['messages'][0]['content'] = guide + request['messages'][0]['content']
        request['max_tokens'] = max(1, min(limits.response_tokens,
            int(limits.response_tokens * (.25 + .75 * effective['expression']))))
        request['temperature'] = effective['creativity']
        diagnostics = bundle.diagnostics()
        allowed = limits.context_window - request['max_tokens']
        if diagnostics['input_limit'] is not None:
            allowed = min(allowed, diagnostics['input_limit'])
        if estimate(request) > allowed:
            # Cognitive advice is optional. Never sacrifice a complete persona,
            # relevant fact or valid tool sequence to make room for this prefix.
            diagnostics['cognition'] = dict(status='budget_omitted', version=state['version'], effective=effective)
            return replace(bundle, diagnostics_json=packed(diagnostics))
        diagnostics.update(estimated_input=estimate(request), response_tokens=request['max_tokens'],
            cognition=dict(status='applied', version=state['version'], effective=effective))
        return replace(bundle, request_json=packed(request), diagnostics_json=packed(diagnostics))
