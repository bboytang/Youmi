"""One bounded, model-free idle cleanup lane owned by MemoryWorker."""

import asyncio
import logging
import math
import threading

from .memory_cache_lifecycle import YoumiManagedMemoryCache


class YoumiMemoryCleanupPump:
    def __init__(self, database, *, interval=5, batch_size=10):
        if (type(interval) not in (int, float) or not math.isfinite(interval) or interval < .01
                or type(batch_size) is not int or not 1 <= batch_size <= 100):
            raise ValueError('Invalid cleanup schedule')
        self.cache = YoumiManagedMemoryCache(database)
        self.interval, self.batch_size = interval, batch_size
        self.cursor = ''
        self.stopping = threading.Event()
        self.thread = None

    @property
    def alive(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.alive:
            return not self.stopping.is_set()
        self.stopping.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name='youmi-memory-cache-cleanup')
        self.thread.start()
        return True

    def _run(self):
        while not self.stopping.wait(self.interval):
            try:
                result = self.cache.recover(cursor=self.cursor, limit=self.batch_size)
                self.cursor = result['cursor']
            except Exception as error:
                # No exception body, source excerpts or filesystem paths in logs.
                logging.getLogger(__name__).warning('Youmi cache cleanup unavailable (%s)', type(error).__name__)

    def stop(self):
        self.stopping.set()

    async def wait(self, timeout):
        # Do not join on the event loop or occupy its shared executor. Cancellation
        # never resets the stop signal, drops the native thread or admits a second
        # lane while late IO still owns the stable partition lock.
        deadline = asyncio.get_running_loop().time() + max(0, timeout)
        while self.alive:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(.01, remaining))
        return True
