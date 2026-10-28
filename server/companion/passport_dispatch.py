"""Bounded in-memory handoff of authenticated device utterances to AstrBot."""

import asyncio
from collections import OrderedDict
import time

from .authorization import PrivateAuthorizationRequired
from .passport_tickets import passport_tickets


class PassportDispatch:
    def __init__(self, tickets, *, clock=time.monotonic, capacity=32):
        if type(capacity) is not int or not 0 < capacity <= 128:
            raise ValueError('invalid_dispatch_capacity')
        self.tickets, self.clock, self.capacity = tickets, clock, capacity
        self._events = OrderedDict()
        self._seen = {}
        self._waiters = {}
        self._requests = {}
        self._accepted = {}
        self._condition = asyncio.Condition()

    def _expire(self, ticket, waiter):
        if self._events.pop(ticket, None) is not None and not waiter.done():
            waiter.cancel()

    def _clear_waiter(self, key, ticket, waiter, timer):
        if self._waiters.get(key) is waiter:
            self._waiters.pop(key, None)
            self._requests.pop(key, None)
        queued = self._events.pop(ticket, None)
        if queued is not None:
            queued[2].cancel()
            asyncio.create_task(self.tickets.discard(ticket))
        timer.cancel()

    async def submit(self, connection, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 4096:
            raise ValueError('invalid_passport_text')
        ticket = await self.tickets.issue(connection)
        try:
            turn = await self.tickets.validate_pending(ticket)
            async with self._condition:
                now = self.clock()
                self._seen = {key: expiry for key, expiry in self._seen.items() if expiry > now}
                key = (turn.session_id, turn.turn_id)
                if key in self._seen:
                    raise RuntimeError('duplicate_passport_turn')
                if len(self._events) >= self.capacity or len(self._seen) >= self.capacity * 4:
                    raise RuntimeError('passport_dispatch_capacity')
                self._seen[key] = now + 300
                waiter = asyncio.get_running_loop().create_future()
                timer = asyncio.get_running_loop().call_later(30, self._expire, ticket, waiter)
                self._events[ticket] = (text, now + 30, timer, waiter)
                self._waiters[key] = waiter
                self._requests[key] = text
                wait_timer = asyncio.get_running_loop().call_later(120, waiter.cancel)
                waiter.add_done_callback(lambda _: self._clear_waiter(key, ticket, waiter, wait_timer))
                self._condition.notify(1)
                return waiter
        except BaseException:
            await self.tickets.discard(ticket)
            raise

    async def next_event(self, *, timeout=10):
        deadline = self.clock() + timeout
        while True:
            async with self._condition:
                while not self._events:
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        return None
                    try:
                        await asyncio.wait_for(self._condition.wait(), remaining)
                    except asyncio.TimeoutError:
                        return None
                ticket, (text, expires, timer, waiter) = self._events.popitem(last=False)
                timer.cancel()
            if waiter.done() or expires <= self.clock():
                await self.tickets.discard(ticket)
                waiter.cancel()
                continue
            try:
                turn = await self.tickets.validate_pending(ticket)
            except PrivateAuthorizationRequired:
                await self.tickets.discard(ticket)
                waiter.cancel()
                continue
            if waiter.done():
                await self.tickets.discard(ticket)
                continue
            return {'ticket': ticket, 'session_id': turn.session_id, 'turn_id': turn.turn_id, 'text': text}

    async def reply(self, handle, text, *, validate=None, receipt=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 4096:
            raise ValueError('invalid_passport_reply')
        turn = await self.tickets.resolve(handle)
        async with self._condition:
            self.tickets.check_connection(turn)
            self.tickets.check_active(handle, turn)
            waiter = self._waiters.get((turn.session_id, turn.turn_id))
            if waiter is None or waiter.done():
                raise RuntimeError('passport_turn_not_waiting')
            if validate is not None:
                validate()
            if receipt is not None:
                now = self.clock()
                self._accepted = {key: expiry for key, expiry in self._accepted.items() if expiry > now}
                if receipt in self._accepted or len(self._accepted) >= self.capacity * 4:
                    raise RuntimeError('passport_receipt_capacity')
                self._accepted[receipt] = now + 300
            waiter.set_result(text)

    def accepted(self, receipt):
        return self._accepted.get(receipt, 0) > self.clock()

    async def request_for(self, handle):
        """Return only the device-captured input for this live, claimed turn."""
        turn = await self.tickets.resolve(handle)
        async with self._condition:
            self.tickets.check_connection(turn)
            self.tickets.check_active(handle, turn)
            key = (turn.session_id, turn.turn_id)
            waiter = self._waiters.get(key)
            if waiter is None or waiter.done() or key not in self._requests:
                raise PrivateAuthorizationRequired()
            return turn, self._requests[key]


passport_dispatch = PassportDispatch(passport_tickets)
