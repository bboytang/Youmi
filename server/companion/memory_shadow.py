"""Explicit read-only evaluation of the ported search; never the reply engine."""

import asyncio
from functools import partial
import sqlite3

from .embedding import EmbeddingUnavailable
from .memory_repository import YoumiMemoryRepository


class YoumiMemoryShadow:
    def __init__(self, index, *, cache_dir=None, managed=False):
        self.index = index
        self.repository = YoumiMemoryRepository(index.store)
        self._slots = asyncio.Semaphore(1)
        self.cache_dir = cache_dir
        with index.store.lock:
            self.database = index.store.db.execute('PRAGMA database_list').fetchone()['file']
        self.managed_cache = None
        if managed:
            from .memory_cache_lifecycle import YoumiManagedMemoryCache
            self.managed_cache = YoumiManagedMemoryCache(self.database)

    @staticmethod
    def _score(records, query, vector_scores, *, cache_path=None, managed_cache=None, corpus=None):
        try:
            from .memory_search import YoumiBM25Search
        except ImportError:
            raise EmbeddingUnavailable('memory_shadow_dependency_unavailable') from None
        from .retrieval import terms
        rows = [dict(id=row['id'], judgment=row['content'], tags=list(dict.fromkeys(
            [row['category']] + terms(row['entities']) + terms(row['keywords']) + terms(row['trigger_terms']))))
            for row in records]
        try:
            if managed_cache is not None:
                return managed_cache.search(corpus, rows, query, vector_scores)
            if cache_path is not None:
                from .memory_index_cache import search as cached_search
                return cached_search(cache_path, rows, query, vector_scores)
            search = YoumiBM25Search()
            search.rebuild(rows)
            return search.search(query, vector_scores)
        except EmbeddingUnavailable:
            raise
        except Exception:
            raise EmbeddingUnavailable('memory_shadow_search_unavailable') from None

    async def _search(self, records, query, vector_scores, *, corpus=None):
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=0.4)
        except asyncio.TimeoutError:
            raise EmbeddingUnavailable('memory_shadow_busy') from None
        try:
            score = self._score
            if self.managed_cache is not None:
                score = partial(score, managed_cache=self.managed_cache, corpus=corpus)
            elif self.cache_dir is not None:
                from .memory_index_cache import partition
                score = partial(score, cache_path=partition(self.cache_dir, self.database, corpus.owner, corpus.persona_id))
            future = asyncio.get_running_loop().run_in_executor(None, score, records, query, vector_scores)
        except BaseException:
            self._slots.release()
            raise
        def finished(result):
            self._slots.release()
            if not result.cancelled():
                result.exception()
        future.add_done_callback(finished)
        try:
            # Cancellation/timeouts do not admit a second native writer until
            # this one has actually finished, matching the encoder's slot rule.
            return await asyncio.wait_for(asyncio.shield(future), timeout=5)
        except asyncio.TimeoutError:
            raise EmbeddingUnavailable('memory_shadow_timeout') from None

    async def retrieve(self, scope, authorization, query, *, use_vectors=True):
        if (not isinstance(query, str) or not query.strip() or len(query) > 4096
                or type(use_vectors) is not bool):
            raise ValueError('Invalid shadow query')
        corpus = await self.repository.capture(scope, authorization)
        vector_scores, vector_status = {}, 'not_requested'
        scores = []
        if corpus.enabled and corpus.records:
            if use_vectors:
                try:
                    from .retrieval import SEMANTIC_FLOOR
                    vector = await self.index.candidates(scope, authorization, query, limit=24, minimum=SEMANTIC_FLOOR)
                    if vector['snapshot'] != {'revision': corpus.version[0], 'content_epoch': corpus.version[1]}:
                        raise EmbeddingUnavailable('memory_changed')
                    owned_ids = {row['id'] for row in corpus.records}
                    vector_scores = {row['id']: row['cosine'] for row in vector['candidates'] if row['id'] in owned_ids}
                    vector_status = 'ready'
                except EmbeddingUnavailable as error:
                    if str(error) == 'memory_changed':
                        raise
                    vector_status = 'unavailable'
        if (corpus.enabled and corpus.records) or self.cache_dir is not None or self.managed_cache is not None:
            scores = await self._search(corpus.records, query, vector_scores, corpus=corpus)
        records = await self.repository.get_by_ids(scope, authorization, corpus, [row['id'] for row in scores])
        results = [dict(record, retrieval=score) for record, score in zip(records, scores)]
        from .memory_emotion import rerank
        try:
            with self.index.store.lock:
                results = rerank(self.index.store, scope.owner, results, query)
        except (sqlite3.Error, ValueError, KeyError, TypeError):
            pass  # Coupling failure keeps the original qualified search order.
        await self.repository.validate(scope, authorization, corpus)
        return {'shadow_only': True, 'engine': 'youmi_bm25_rrf',
                'snapshot': {'revision': corpus.version[0], 'content_epoch': corpus.version[1]},
                'memory_status': 'disabled' if not corpus.enabled else 'matched' if results else 'no_match',
                'vector_status': vector_status, 'results': results}
