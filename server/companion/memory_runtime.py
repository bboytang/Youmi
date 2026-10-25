"""Shared memory boundary for App, device and QQ context construction.

The first migration step delegates to the existing index without changing its
results, authorization, epochs or failures. No new engine is activated here;
memory writes still use the completed-turn ledger and validated JobStore path.
Emotion and persona remain owned by their existing services, not this runtime.
"""


class YoumiMemoryRuntime:
    def __init__(self, index):
        self._index = index

    @property
    def store(self):
        return self._index.store

    async def retrieve(self, scope, authorization, query):
        return await self._index.retrieve(scope, authorization, query)

    async def _require(self, scope, authorization, permission):
        # ContextBuilder also checks before/after assembling private context.
        # Preserve revocation errors; never turn them into an empty recall.
        return await self._index._require(scope, authorization, permission)

    def _check(self, scope, expected, db=None):
        return self._index._check(scope, expected, db)
