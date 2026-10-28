"""One-use, in-process handoff from an authenticated live device connection."""

import asyncio
from dataclasses import dataclass
import re
import secrets
import time

from .authorization import AuthenticatedScope, PrivateAuthorizationRequired


@dataclass(frozen=True)
class PassportTurn:
    connection: object
    scope: AuthenticatedScope
    authorization: object
    session_id: str
    turn_id: str


class PassportTickets:
    """Tickets never contain credentials and cannot outlive the live connection."""

    def __init__(self, *, clock=time.monotonic, ttl=30, capacity=128):
        if not 0 < ttl <= 60 or type(capacity) is not int or not 0 < capacity <= 1024:
            raise ValueError('invalid_ticket_limits')
        self._clock, self._ttl, self._capacity = clock, ttl, capacity
        self._pending = {}
        self._active = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def check_connection(turn):
        conn = turn.connection
        scope = getattr(conn, '_youmi_scope', None)
        authorization = getattr(conn, '_youmi_authorization', None)
        if (not getattr(conn, 'read_config_from_api', False)
                or not isinstance(scope, AuthenticatedScope) or scope != turn.scope
                or not authorization or authorization is not turn.authorization
                or getattr(authorization, '_closed', False)
                or (getattr(conn, 'stop_event', None) is not None and conn.stop_event.is_set())
                or getattr(conn, 'client_abort', False)
                or getattr(conn, 'session_id', None) != turn.session_id
                or getattr(conn, 'sentence_id', None) != turn.turn_id):
            raise PrivateAuthorizationRequired()
        return authorization

    @staticmethod
    async def require_live(turn):
        authorization = PassportTickets.check_connection(turn)
        scope = turn.scope
        await authorization.require(scope, 'memory:read')
        # The connection can be interrupted or reused while manager validation awaits.
        PassportTickets.check_connection(turn)

    async def issue(self, connection):
        scope = getattr(connection, '_youmi_scope', None)
        session = getattr(connection, 'session_id', None)
        turn_id = getattr(connection, 'sentence_id', None)
        if (not isinstance(scope, AuthenticatedScope)
                or not all(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_:.-]{1,128}', value)
                           for value in (session, turn_id))):
            raise PrivateAuthorizationRequired()
        turn = PassportTurn(connection, scope, getattr(connection, '_youmi_authorization', None), session, turn_id)
        await self.require_live(turn)
        async with self._lock:
            now = self._clock()
            self._pending = {key: value for key, value in self._pending.items() if value[0] > now}
            if len(self._pending) >= self._capacity:
                raise RuntimeError('passport_ticket_capacity')
            ticket = secrets.token_urlsafe(32)
            self._pending[ticket] = (now + self._ttl, turn)
            return ticket

    async def consume(self, ticket):
        if not isinstance(ticket, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', ticket):
            raise PrivateAuthorizationRequired()
        async with self._lock:
            entry = self._pending.pop(ticket, None)
        if entry is None or entry[0] <= self._clock():
            raise PrivateAuthorizationRequired()
        await self.require_live(entry[1])
        # The deadline also applies while the manager is checking the scope.
        if entry[0] <= self._clock():
            raise PrivateAuthorizationRequired()
        return entry[1]

    async def validate_pending(self, ticket):
        async with self._lock:
            entry = self._pending.get(ticket)
        if entry is None or entry[0] <= self._clock():
            raise PrivateAuthorizationRequired()
        await self.require_live(entry[1])
        async with self._lock:
            if self._pending.get(ticket) is not entry or entry[0] <= self._clock():
                raise PrivateAuthorizationRequired()
        return entry[1]

    async def discard(self, ticket):
        async with self._lock:
            self._pending.pop(ticket, None)

    async def open(self, ticket):
        """Exchange an issued ticket for a bounded handle to the live turn."""
        turn = await self.consume(ticket)
        async with self._lock:
            now = self._clock()
            self._active = {key: value for key, value in self._active.items() if value[0] > now}
            if len(self._active) >= self._capacity:
                raise RuntimeError('passport_turn_capacity')
            handle = secrets.token_urlsafe(32)
            self._active[handle] = (now + 300, turn)
        return handle, turn

    async def resolve(self, handle):
        if not isinstance(handle, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', handle):
            raise PrivateAuthorizationRequired()
        async with self._lock:
            entry = self._active.get(handle)
            if entry is not None and entry[0] <= self._clock():
                self._active.pop(handle, None)
                entry = None
        if entry is None:
            raise PrivateAuthorizationRequired()
        await self.require_live(entry[1])
        async with self._lock:
            if self._active.get(handle) is not entry or entry[0] <= self._clock():
                self._active.pop(handle, None)
                raise PrivateAuthorizationRequired()
        return entry[1]

    async def close(self, handle):
        async with self._lock:
            self._active.pop(handle, None)

    def check_active(self, handle, turn):
        entry = self._active.get(handle)
        if entry is None or entry[0] <= self._clock() or entry[1] is not turn:
            raise PrivateAuthorizationRequired()


# WebSocket and aiohttp servers run on the same application event loop.
passport_tickets = PassportTickets()
