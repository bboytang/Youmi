"""Exercise the pinned upstream device MCP client/executor against a fake device."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# Audio and production logging are unrelated to the device MCP protocol here.
sys.modules['opuslib_next'] = types.ModuleType('opuslib_next')
logger = types.ModuleType('config.logger')
logger.setup_logging = lambda: MagicMock()
sys.modules['config.logger'] = logger

from core.providers.tools.device_mcp.mcp_executor import DeviceMCPExecutor
from core.providers.tools.device_mcp.mcp_handler import MCPClient
from server.companion.authorization import AuthenticatedScope
from server.companion.passport_mcp import PassportMCP
from server.companion.passport_tickets import PassportTickets


async def main():
    scope = AuthenticatedScope('user:1:agent:a', 1, 1, 'a', 'mia', 'device', 'client', 1, 1,
        datetime.now(timezone.utc) + timedelta(days=1), frozenset({'memory:read'}))
    authorization = SimpleNamespace(_closed=False, require=AsyncMock())
    mcp = MCPClient()
    await mcp.add_tool({'name': 'self.audio_speaker.set_volume', 'description': 'Set volume',
        'inputSchema': {'type': 'object', 'properties': {'volume': {'type': 'integer'}}, 'required': ['volume']}})
    await mcp.set_ready(True)
    messages = []
    fail = False

    async def send(raw):
        message = json.loads(raw)
        messages.append(message)
        result = {'isError': True, 'error': 'device failed'} if fail else {'content': [{'type': 'text', 'text': 'volume set'}]}
        await mcp.resolve_call_result(message['payload']['id'], result)

    conn = SimpleNamespace(read_config_from_api=True, _youmi_scope=scope, _youmi_authorization=authorization,
        client_abort=False, stop_event=asyncio.Event(), session_id='native-session', sentence_id='native-turn',
        mcp_client=mcp, features={'mcp': True}, websocket=SimpleNamespace(send=send))
    conn.func_handler = SimpleNamespace(device_mcp_executor=DeviceMCPExecutor(conn))
    tickets = PassportTickets()
    ticket = await tickets.issue(conn)
    handle, _ = await tickets.open(ticket)
    bridge = PassportMCP(tickets)
    listed = await bridge.list_tools(handle, 'native-turn-key', lambda: None)
    assert len(listed) == 1 and listed[0]['parameters']['required'] == ['volume']
    response = await bridge.execute(handle, 'native-turn-key', 'call-1', listed[0]['name'], {'volume': 3}, lambda: None)
    assert response == {'ok': True, 'text': 'volume set'}
    assert len(messages) == 1
    assert messages[0]['type'] == 'mcp'
    assert messages[0]['payload']['method'] == 'tools/call'
    assert messages[0]['payload']['params'] == {'name': 'self.audio_speaker.set_volume', 'arguments': {'volume': 3}}
    fail = True
    error = await bridge.execute(handle, 'native-turn-key', 'call-2', listed[0]['name'], {'volume': 5}, lambda: None)
    assert error == {'ok': False, 'text': '设备工具执行失败。'}


if __name__ == '__main__':
    asyncio.run(main())
