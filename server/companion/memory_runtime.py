"""Shared memory boundary for App, device and QQ context construction.

Normal recall delegates to the existing index without changing its results,
authorization, epochs or failures. Explicit shadow recall evaluates the ported
search without activating it; writes still use the completed-turn ledger and
validated JobStore path.
Emotion and persona remain owned by their existing services, not this runtime.
"""


class YoumiMemoryRuntime:
    def __init__(self, index):
        self._index = index
        self._shadow = None

    @property
    def store(self):
        return self._index.store

    async def retrieve(self, scope, authorization, query):
        return await self._index.retrieve(scope, authorization, query)

    async def shadow_retrieve(self, scope, authorization, query, *, use_vectors=True):
        # Explicit evaluation only. The normal reply path never calls this and
        # does not import Tantivy or require its optional dependency.
        if self._shadow is None:
            from .memory_shadow import YoumiMemoryShadow
            self._shadow = YoumiMemoryShadow(self._index)
        return await self._shadow.retrieve(scope, authorization, query, use_vectors=use_vectors)

    async def _require(self, scope, authorization, permission):
        # ContextBuilder also checks before/after assembling private context.
        # Preserve revocation errors; never turn them into an empty recall.
        return await self._index._require(scope, authorization, permission)

    def _check(self, scope, expected, db=None):
        return self._index._check(scope, expected, db)
