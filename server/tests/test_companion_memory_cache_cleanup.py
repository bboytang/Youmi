"""Idle recovery without conversations, model calls or unbounded native lanes."""

import asyncio
import importlib.util
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from server.companion.embedding import EmbeddingUnavailable
from server.companion.jobs import JobStore, MemoryWorker
from server.companion.memory_cache_cleanup import YoumiMemoryCleanupPump
from server.companion.memory_cache_lifecycle import YoumiManagedMemoryCache
from server.companion.memory_index_cache import _locked
from server.companion.memory_runtime import YoumiMemoryRuntime
from server.tests import test_companion_context as fixture


class PumpTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.ContextBuilderTests.setUp
    tearDown = fixture.ContextBuilderTests.tearDown
    fact = fixture.ContextBuilderTests.fact

    async def eventually(self, predicate, timeout=3):
        deadline = asyncio.get_running_loop().time() + timeout
        while not predicate():
            if asyncio.get_running_loop().time() >= deadline:
                self.fail('Expected cleanup progress before synthetic test deadline')
            await asyncio.sleep(.01)

    def pump(self):
        return YoumiMemoryCleanupPump(Path(self.temp.name) / 'memory.sqlite3', interval=.01, batch_size=1)

    @unittest.skipUnless(importlib.util.find_spec('tantivy'), 'Optional native shadow dependency')
    async def test_worker_idle_release_of_file_lock_cleans_without_conversation_or_model(self):
        identifier = self.fact('我喜欢热茶')
        await YoumiMemoryRuntime(self.index, managed_shadow_cache=True).shadow_retrieve(self.scope, self.auth, '热茶', use_vectors=False)
        pump = self.pump()
        cache = pump.cache
        calls = []
        async def resolve(scope):
            calls.append(scope)
            raise AssertionError('Idle cleanup must not call a provider')
        worker = MemoryWorker(JobStore(self.store), self.auth, resolve, encoder=self.encoder)
        worker.cleanup = pump
        with _locked(cache.directory(self.scope.owner)):
            self.store.delete_memory(self.scope.owner, identifier)
            await worker.start()
            self.assertEqual(self.store.cache_cleanup_status(self.scope.owner)['state'], 'pending')
            await asyncio.sleep(.04)
        try:
            await self.eventually(lambda: self.store.cache_cleanup_status(self.scope.owner)['state'] == 'complete')
            self.assertEqual([p.name for p in cache.directory(self.scope.owner).iterdir()], ['writer.lock'])
            self.assertEqual(calls, [])
        finally:
            self.assertTrue(await worker.close(timeout=1))
        self.assertFalse(pump.alive)

    async def test_empty_registry_does_not_create_root_or_request_native_search(self):
        pump = self.pump()
        with patch.object(YoumiManagedMemoryCache, '_root', side_effect=AssertionError('unregistered filesystem access')):
            pump.start()
            await asyncio.sleep(.05)
            pump.stop()
            self.assertTrue(await pump.wait(1))
        self.assertFalse(pump.cache.root.exists())

    async def test_one_lane_survives_cancelled_wait_and_refuses_overlapping_restart(self):
        pump = self.pump()
        entered, release = threading.Event(), threading.Event()
        calls = []
        pump.cache._root()
        directory = pump.cache.directory(self.scope.owner)
        def hung(**kwargs):
            calls.append(kwargs)
            with _locked(directory):
                entered.set()
                release.wait(3)
            return dict(cursor='retained')
        with patch.object(pump.cache, 'recover', hung):
            pump.start()
            try:
                await self.eventually(entered.is_set)
                original = pump.thread
                self.assertTrue(pump.start())  # Idempotent while running.
                pump.stop()
                self.assertFalse(await pump.wait(.02))
                waiting = asyncio.create_task(pump.wait(1))
                await asyncio.sleep(0)
                waiting.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await waiting
                self.assertFalse(pump.start())
                with self.assertRaises(EmbeddingUnavailable), _locked(directory, timeout=0):
                    pass
                self.assertIs(pump.thread, original)
                self.assertTrue(original.daemon)
                self.assertEqual(len(calls), 1)
            finally:
                release.set()
                self.assertTrue(await pump.wait(1))
        self.assertTrue(pump.stopping.is_set())
        self.assertEqual(pump.cursor, 'retained')

    async def test_transient_failure_retries_and_uses_batch_cursor(self):
        pump = self.pump()
        calls = []
        def retry(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise OSError('synthetic retry')
            return dict(cursor='next-owner')
        with patch.object(pump.cache, 'recover', retry):
            pump.start()
            try:
                await self.eventually(lambda: len(calls) >= 3)
            finally:
                pump.stop()
                self.assertTrue(await pump.wait(1))
        self.assertEqual(calls[0], dict(cursor='', limit=1))
        self.assertEqual(calls[1], dict(cursor='', limit=1))
        self.assertEqual(calls[2], dict(cursor='next-owner', limit=1))

    async def test_worker_shutdown_uses_shared_budget_and_late_io_owns_independent_connection(self):
        entered, release = threading.Event(), threading.Event()
        pump = self.pump()
        async def resolve(scope):
            raise AssertionError('No model work')
        worker = MemoryWorker(JobStore(self.store), self.auth, resolve)
        worker.cleanup = pump
        def slow(**kwargs):
            entered.set()
            release.wait(3)
            # This runs AFTER MemoryStore is closed, using its own connection.
            return YoumiManagedMemoryCache(Path(self.temp.name) / 'memory.sqlite3').recover(**kwargs)
        with patch.object(pump.cache, 'recover', slow):
            await worker.start()
            try:
                await self.eventually(entered.is_set)
                before = asyncio.get_running_loop().time()
                self.assertFalse(await worker.close(timeout=.03))
                self.assertLess(asyncio.get_running_loop().time() - before, .15)
                self.store.close()
            finally:
                release.set()
                self.assertTrue(await pump.wait(1))
        self.assertFalse(pump.alive)

    async def test_worker_cancelled_before_first_loop_stops_cleanup_lane(self):
        async def resolve(scope):
            raise AssertionError('No provider')
        worker = MemoryWorker(JobStore(self.store), self.auth, resolve)
        await worker.start()
        worker.task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await worker.task
        await asyncio.sleep(0)
        self.assertTrue(worker.cleanup.stopping.is_set())
        self.assertTrue(await worker.cleanup.wait(1))

    def test_schedule_rejects_busy_spin_and_unbounded_batch(self):
        for values in [dict(interval=0), dict(interval=True), dict(interval=float('nan')), dict(batch_size=101)]:
            with self.assertRaises(ValueError):
                YoumiMemoryCleanupPump(Path(self.temp.name) / 'memory.sqlite3', **values)
