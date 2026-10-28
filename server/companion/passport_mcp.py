"""Device-only MCP tools bound to a live authorized Passport turn."""

import asyncio
import hashlib
import json
import re
import time

from .authorization import PrivateAuthorizationRequired
from .passport_tickets import passport_tickets


class PassportMCP:
    def __init__(self, tickets, *, clock=time.monotonic):
        self.tickets, self.clock = tickets, clock
        self._declared, self._calls = {}, {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _tools(turn):
        handler = getattr(turn.connection, 'func_handler', None)
        executor = getattr(handler, 'device_mcp_executor', None)
        if executor is None:
            return {}, None
        raw = executor.get_tools()
        if not isinstance(raw, dict) or len(raw) > 32:
            raise ValueError('invalid_device_tools')
        tools = {}
        for name, definition in raw.items():
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', name):
                raise ValueError('invalid_device_tool')
            description = getattr(definition, 'description', None)
            function = description.get('function') if isinstance(description, dict) else None
            if (not isinstance(function, dict) or function.get('name') != name
                    or not isinstance(function.get('description'), str)
                    or len(function['description']) > 2048
                    or not isinstance(function.get('parameters'), dict)
                    or function['parameters'].get('type') != 'object'):
                raise ValueError('invalid_device_tool')
            item = {'name': 'passport_device_' + hashlib.sha256(name.encode()).hexdigest()[:24],
                    'description': function['description'], 'parameters': function['parameters']}
            if len(json.dumps(item, ensure_ascii=False).encode()) > 16384 or item['name'] in tools:
                raise ValueError('invalid_device_tool')
            tools[item['name']] = (name, item)
        return tools, executor

    def _prune(self):
        now = self.clock()
        self._declared = {key: value for key, value in self._declared.items() if value[0] > now}
        self._calls = {key: value for key, value in self._calls.items() if value > now}

    async def list_tools(self, handle, turn_key, validate):
        turn = await self.tickets.resolve(handle)
        async with self._lock:
            self.tickets.check_connection(turn)
            self.tickets.check_active(handle, turn)
            validate()
            self._prune()
            if len(self._declared) >= 128 and (turn_key, handle) not in self._declared:
                raise RuntimeError('passport_mcp_capacity')
            tools, _ = self._tools(turn)
            fingerprint = json.dumps(tools, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            previous = self._declared.get((turn_key, handle))
            if previous and previous[1] != fingerprint:
                raise RuntimeError('passport_mcp_changed')
            self._declared[(turn_key, handle)] = (self.clock() + 300, fingerprint)
            return [item for _, item in tools.values()]

    async def execute(self, handle, turn_key, call_id, name, arguments, validate):
        if (not isinstance(name, str) or not re.fullmatch(r'passport_device_[0-9a-f]{24}', name)
                or not isinstance(call_id, str) or not re.fullmatch(r'[A-Za-z0-9_:.-]{1,128}', call_id)
                or not isinstance(arguments, dict) or len(json.dumps(arguments, ensure_ascii=False).encode()) > 8192):
            raise ValueError('invalid_device_tool_call')
        turn = await self.tickets.resolve(handle)
        async with self._lock:
            self.tickets.check_connection(turn)
            self.tickets.check_active(handle, turn)
            validate()
            self._prune()
            declared = self._declared.get((turn_key, handle))
            tools, executor = self._tools(turn)
            fingerprint = json.dumps(tools, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            if not declared or declared[1] != fingerprint or name not in tools or executor is None:
                raise PrivateAuthorizationRequired()
            key = (turn_key, call_id)
            if key in self._calls:
                raise RuntimeError('passport_mcp_duplicate')
            if len(self._calls) >= 1024:
                raise RuntimeError('passport_mcp_capacity')
            self._calls[key] = self.clock() + 300
            original = tools[name][0]
        # A timeout or lost response may follow a real hardware action. Never retry it.
        response = await asyncio.wait_for(executor.execute(turn.connection, original, arguments), 15)
        await self.tickets.require_live(turn)
        self.tickets.check_active(handle, turn)
        validate()
        action = getattr(getattr(response, 'action', None), 'name', '')
        if action in {'ERROR', 'NOTFOUND'}:
            return {'ok': False, 'text': '设备工具执行失败。'}
        text = getattr(response, 'result', None) or getattr(response, 'response', None) or '设备工具已执行。'
        if not isinstance(text, str) or len(text) > 4096:
            raise ValueError('invalid_device_tool_result')
        return {'ok': True, 'text': text}


passport_mcp = PassportMCP(passport_tickets)
