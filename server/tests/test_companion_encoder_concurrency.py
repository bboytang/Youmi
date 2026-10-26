"""Synthetic native holds verify ownership/bounds, never real BGE performance."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace
import unittest

import numpy as np

from server.companion.embedding import DIMENSION, EmbeddingUnavailable, SemanticEncoder, _NativeSlots
from server.companion.jobs import NativeLane


class HeldRuntime:
    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()
        self.guard, self.tokenizing = threading.Lock(), threading.Lock()
        self.active, self.peak, self.calls = 0, 0, 0
        self.failure = None

    def encode(self, text):
        if not self.tokenizing.acquire(blocking=False):
            raise AssertionError('Tokenizer was used concurrently')
        try:
            if self.failure == 'tokenizer':
                raise ValueError('synthetic tokenizer failure')
            return SimpleNamespace(ids=[ord(char) for char in text],
                attention_mask=[1] * len(text), type_ids=[0] * len(text))
        finally:
            self.tokenizing.release()

    def run(self, outputs, values):
        before = {key: value.copy() for key, value in values.items()}
        with self.guard:
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active == 2:
                self.entered.set()
        try:
            if not self.release.wait(3):
                raise AssertionError('Synthetic native hold was not released')
            for key, value in values.items():
                np.testing.assert_array_equal(value, before[key])
            if self.failure == 'native':
                raise ValueError('synthetic inference failure')
            vector = np.zeros((1, 1, DIMENSION), dtype=np.float32)
            vector[0, 0, :2] = (1, len(values['input_ids'][0]))
            if self.failure == 'nonfinite':
                vector[0, 0, 0] = np.nan
            return [vector]
        finally:
            with self.guard:
                self.active -= 1


def encoder(runtime):
    value = SemanticEncoder.__new__(SemanticEncoder)
    value._np, value._tokenizer, value._session = np, runtime, runtime
    value._inputs = {'input_ids', 'attention_mask', 'token_type_ids'}
    value._lock = threading.Lock()
    value._native_slots = _NativeSlots()
    return value


class EncoderConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    def assert_two_calls(self, value, runtime):
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(value.encode, '甲', True)
            second = pool.submit(value.encode, '乙乙', False)
            try:
                self.assertTrue(runtime.entered.wait(2))
                with self.assertRaisesRegex(EmbeddingUnavailable, 'embedding_busy'):
                    value.encode('第三条')
                self.assertEqual(runtime.peak, 2)
                self.assertEqual(runtime.calls, 2)
            finally:
                runtime.release.set()
            left, right = first.result(2), second.result(2)
            self.assertFalse(np.array_equal(left, right))
            self.assertAlmostEqual(float(np.linalg.norm(left)), 1, places=6)

    def test_two_native_calls_overlap_with_independent_inputs_and_third_is_busy(self):
        runtime = HeldRuntime()
        self.assert_two_calls(encoder(runtime), runtime)

    def test_tokenizer_inference_and_invalid_vector_errors_release_capacity(self):
        for failure in ('tokenizer', 'native', 'nonfinite'):
            with self.subTest(failure=failure):
                runtime = HeldRuntime()
                value = encoder(runtime)
                runtime.failure = failure
                runtime.release.set()
                with self.assertRaisesRegex(EmbeddingUnavailable, 'embedding_inference_failed'):
                    value.encode('失败输入')
                runtime.failure, runtime.calls = None, 0
                runtime.release.clear()
                self.assert_two_calls(value, runtime)

    async def test_cancel_and_timeout_retain_global_slots_for_direct_worker_calls(self):
        runtime = HeldRuntime()
        value = encoder(runtime)
        first = asyncio.create_task(asyncio.to_thread(value.encode, '前台一', True))
        second = asyncio.create_task(asyncio.to_thread(value.encode, '前台二', True))
        lane = NativeLane()
        try:
            self.assertTrue(await asyncio.to_thread(runtime.entered.wait, 2))
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(second), 0.01)
            with self.assertRaisesRegex(EmbeddingUnavailable, 'embedding_busy'):
                await lane.call(lambda: value.encode('后台直接编码'), 1)
            self.assertEqual(runtime.calls, 2)
        finally:
            runtime.release.set()
            await asyncio.gather(first, second, return_exceptions=True)
        self.assertTrue(lane.done.is_set())
        self.assertEqual((await asyncio.to_thread(value.encode, '恢复后')).shape, (DIMENSION,))
