"""Shared per-agent retrieval boundary for App, device and QQ.

Absent a selection, recall preserves the existing index. Explicit owner selection
activates qualified hybrid retrieval with the managed cache. Completed-turn jobs
remain the only fact writer; emotion and persona retain their existing owners.
"""


class YoumiMemoryRuntime:
    def __init__(self, index, *, shadow_cache_dir=None, managed_shadow_cache=False):
        if type(managed_shadow_cache) is not bool or (managed_shadow_cache and shadow_cache_dir is not None):
            raise ValueError('Invalid shadow cache mode')
        self._index = index
        self._shadow = None
        self._engine_shadow = None
        self._shadow_cache_dir = shadow_cache_dir
        self._managed_shadow_cache = managed_shadow_cache

    @property
    def store(self):
        return self._index.store

    async def retrieve(self, scope, authorization, query):
        from .embedding import EmbeddingUnavailable
        from .memory_engine import status
        await self._index._require(scope, authorization, 'memory:read')
        with self.store.lock:
            selection = status(self.store, scope.owner)
        if selection['engine'] == 'legacy':
            result = await self._index.retrieve(scope, authorization, query)
        else:
            if self._engine_shadow is None:
                from .memory_shadow import YoumiMemoryShadow
                self._engine_shadow = YoumiMemoryShadow(self._index, managed=True)
            result = dict(await self._engine_shadow.retrieve(scope, authorization, query))
            result['shadow_only'] = False
        await self._index._require(scope, authorization, 'memory:read')
        with self.store.lock:
            if status(self.store, scope.owner) != selection:
                raise EmbeddingUnavailable('memory_changed')
        return result

    async def shadow_retrieve(self, scope, authorization, query, *, use_vectors=True):
        # Explicit evaluation only. The normal reply path never calls this and
        # does not import Tantivy or require its optional dependency.
        if self._shadow is None:
            from .memory_shadow import YoumiMemoryShadow
            self._shadow = YoumiMemoryShadow(self._index, cache_dir=self._shadow_cache_dir, managed=self._managed_shadow_cache)
        return await self._shadow.retrieve(scope, authorization, query, use_vectors=use_vectors)

    async def _require(self, scope, authorization, permission):
        # ContextBuilder also checks before/after assembling private context.
        # Preserve revocation errors; never turn them into an empty recall.
        return await self._index._require(scope, authorization, permission)

    async def list_artifacts(self, scope, authorization):
        from .memory_artifacts import YoumiMemoryArtifacts
        return await YoumiMemoryArtifacts(self.store).list(scope, authorization)

    async def reflection_settings(self, scope, authorization, command):
        from .memory_reflection_policy import YoumiReflectionPolicy
        return await YoumiReflectionPolicy(self.store).manage(scope, authorization, command)

    async def emotion_linkage(self, scope, authorization, command):
        from .memory_emotion import YoumiMemoryEmotion
        return await YoumiMemoryEmotion(self.store).manage(scope, authorization, command)

    async def delete_artifact(self, scope, authorization, identifier, version):
        from .memory_artifacts import YoumiMemoryArtifacts
        return await YoumiMemoryArtifacts(self.store).remove(scope, authorization, identifier, version)

    def _check(self, scope, expected, db=None):
        return self._index._check(scope, expected, db)
