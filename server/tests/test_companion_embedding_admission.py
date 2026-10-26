"""Fair bounded native admission; old waiters must not lose to fresh work."""

import sys
import threading
import time
import unittest

from server.companion import embedding


class EmbeddingAdmissionTests(unittest.TestCase):
    def slots(self):
        factory = getattr(embedding, '_NativeSlots', None)
        return factory() if factory else threading.BoundedSemaphore(2)

    def waiting(self, slots, count):
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            with slots._cond:
                if len(slots._cond._waiters) >= count:
                    return
            time.sleep(.001)
        self.fail('Synthetic native waiter was not parked')

    def test_release_cannot_be_stolen_by_fresh_caller_ahead_of_parked_waiter(self):
        slots = self.slots()
        self.assertTrue(slots.acquire(timeout=0))
        self.assertTrue(slots.acquire(timeout=0))
        accepted = threading.Event()
        def waiter():
            if slots.acquire(timeout=1):
                accepted.set()
                slots.release()
        thread = threading.Thread(target=waiter)
        thread.start()
        self.waiting(slots, 1)
        previous = sys.getswitchinterval()
        try:
            # Release and a fresh attempt execute in the same Python time slice.
            # FIFO must hold even when the old waiter cannot run immediately.
            sys.setswitchinterval(1)
            slots.release()
            stolen = slots.acquire(timeout=0)
        finally:
            sys.setswitchinterval(previous)
        if stolen:
            slots.release()
        slots.release()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(accepted.is_set())
        self.assertFalse(stolen, 'Fresh encode stole the slot from an already parked request')

    def test_bound_is_two_active_and_four_waiters(self):
        slots = self.slots()
        slots.acquire(timeout=0); slots.acquire(timeout=0)
        results = []
        def wait():
            if slots.acquire(timeout=1):
                results.append(True)
                slots.release()
        threads = [threading.Thread(target=wait) for _ in range(4)]
        for thread in threads:
            thread.start()
        self.waiting(slots, 4)
        before = time.monotonic()
        self.assertFalse(slots.acquire(timeout=.4))
        self.assertLess(time.monotonic() - before, .1)
        slots.release(); slots.release()
        for thread in threads:
            thread.join(2)
        self.assertEqual(results, [True] * 4)
        with self.assertRaises(ValueError):
            slots.release()

    def test_timed_out_head_does_not_block_following_waiter(self):
        slots = self.slots()
        slots.acquire(timeout=0); slots.acquire(timeout=0)
        head, following = [], []
        first = threading.Thread(target=lambda: head.append(slots.acquire(timeout=.02)))
        first.start(); self.waiting(slots, 1)
        def next_waiter():
            accepted = slots.acquire(timeout=1)
            following.append(accepted)
            if accepted:
                slots.release()
        second = threading.Thread(target=next_waiter)
        second.start()
        first.join(1)
        self.assertEqual(head, [False])
        slots.release()
        second.join(2)
        slots.release()
        self.assertEqual(following, [True])
