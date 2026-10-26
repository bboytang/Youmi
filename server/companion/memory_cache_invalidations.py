"""Internal durable cleanup work; no paths, activation or deletion executor.

Only a trusted lifecycle coordinator may acknowledge a generation, AFTER every
registered partition is physically removed under its writer lock. This module
alone does not make private disk caches safe or make a forget operation complete.
"""

from .memory import MemoryError


class YoumiMemoryCacheInvalidations:
    def __init__(self, store):
        self.store = store

    def pending(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise MemoryError('invalid_input', 'Invalid invalidation page size')
        with self.store.lock:
            return [dict(row) for row in self.store.db.execute(
                'SELECT owner,generation FROM youmi_memory_cache_invalidations '
                'WHERE cleaned_generation<generation ORDER BY owner LIMIT ?', (limit,))]

    def acknowledge(self, owner, generation):
        """CAS after successful cleanup; an intervening write must remain pending.

        Never call from an uncommitted fact transaction: a later fact rollback
        must not roll back acknowledgement of a physical cleanup independently.
        Duplicate/stale acknowledgements return False. No current runtime calls
        this helper; the production coordinator remains to be implemented.
        """
        if (not isinstance(owner, str) or not owner or len(owner) > 256
                or type(generation) is not int or not 1 <= generation < 2**63):
            raise MemoryError('invalid_input', 'Invalid invalidation generation')
        with self.store.lock:
            if self.store.db.in_transaction:
                raise MemoryError('invalid_input', 'Cleanup acknowledgement requires its own transaction')
            with self.store.db:
                return bool(self.store.db.execute(
                    'UPDATE youmi_memory_cache_invalidations SET cleaned_generation=? '
                    'WHERE owner=? AND generation=? AND cleaned_generation<generation',
                    (generation, owner, generation)).rowcount)
