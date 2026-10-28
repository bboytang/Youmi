import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from astrbot.core.message.components import Image, Plain
from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.astr_agent_hooks import MainAgentHooks
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor

from integrations.astrbot_plugin_youmi.main import YoumiConnector
from integrations.astrbot_plugin_youmi.passport import PassportEvent, poll_passport
from integrations.astrbot_plugin_youmi.turn_state import CompletionOutbox
from integrations.tests.test_astrbot_compatibility import ToolProviderFixture, ToolExecutorFixture


class Client:
    def __init__(self):
        self.calls = []
        self.tools = []
        self.prepared = {'turn_key': 'key', 'turn_token': 'token', 'system_prompt': 'YOUMI_CORE',
                         'dynamic_context': {'facts': []}}

    async def call(self, action, body):
        self.calls.append((action, dict(body)))
        if action == 'passport-prepare':
            return self.prepared
        if action == 'check':
            return {'checked_text': body['final_text']}
        if action == 'passport-tools':
            return {'tools': self.tools}
        if action == 'passport-tool':
            return {'ok': True, 'text': '设备已执行'}
        return {'status': 'completed'}


@pytest.mark.asyncio
async def test_passport_native_event_injects_context_and_sends_only_claimed_final_reply(tmp_path):
    client = Client()
    event = PassportEvent({'session_id': 'session', 'turn_id': 'turn', 'text': '继续刚才的话题'}, 'h' * 43, client)
    connector = object.__new__(YoumiConnector)
    connector.client, connector.config = client, {'qq_platform_id': 'qq-ci', 'max_dynamic_context_chars': 24000}
    connector.context = SimpleNamespace(get_config=lambda *args: {})
    connector.outbox = CompletionOutbox(Path(tmp_path) / 'outbox')
    event.set_extra('youmi_primary_chat', True)
    request = ProviderRequest(model='ci-model', prompt=event.message_str, system_prompt='native instructions')
    with patch('astrbot.core.astr_main_agent._select_provider', new=AsyncMock(return_value=SimpleNamespace(get_model=lambda: 'ci-model'))):
        await connector.request(event, request)
    state = event.get_extra('youmi_turn')
    assert state.source_kind == 'asr'
    assert event.is_at_or_wake_command
    assert 'YOUMI_CORE' in request.system_prompt
    prepared = [body for action, body in client.calls if action == 'passport-prepare']
    assert len(prepared) == 1 and set(prepared[0]) == {'handle', 'attempt_id', 'model'}
    state.agent_done = True
    event.set_result(MessageEventResult(chain=[Plain('接着聊吧。')], result_content_type=ResultContentType.LLM_RESULT))
    await connector.decorate(event)
    actions = [action for action, _ in client.calls]
    assert actions == ['passport-prepare', 'passport-tools', 'check', 'claim', 'passport-reply', 'complete']
    assert event.get_result() is None and state.sent and state.complete_ack
    assert next(body for action, body in client.calls if action == 'passport-reply')['text'] == '接着聊吧。'


@pytest.mark.asyncio
async def test_passport_blocks_early_tool_send_and_rejects_non_text_output():
    client = Client()
    event = PassportEvent({'session_id': 'session', 'turn_id': 'turn', 'text': '你好'}, 'h' * 43, client)
    connector = object.__new__(YoumiConnector)
    connector.config = {'qq_platform_id': 'qq-ci'}
    args = {'messages': [{'type': 'plain', 'text': '提前发送'}]}
    await connector.tool(event, SimpleNamespace(name='send_message_to_user'), args)
    assert args == {}
    with pytest.raises(RuntimeError):
        await event.send(SimpleNamespace(chain=[Image.fromFileSystem('/tmp/none')]))


@pytest.mark.asyncio
async def test_poller_queues_real_event_after_matching_ticket_exchange():
    queue = asyncio.Queue()
    context = SimpleNamespace(get_event_queue=lambda: queue)
    class PollClient:
        async def call(self, action, body):
            if action == 'passport-next':
                await asyncio.sleep(0)
                return {'event': {'ticket': 't' * 43, 'session_id': 'session', 'turn_id': 'turn', 'text': '你好'}}
            if action == 'passport-open':
                return {'handle': 'h' * 43, 'session_id': 'session', 'turn_id': 'turn'}
            return {'closed': True}
    task = asyncio.create_task(poll_passport(context, PollClient()))
    event = await asyncio.wait_for(queue.get(), 2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert isinstance(event, PassportEvent) and event.message_str == '你好'


@pytest.mark.asyncio
async def test_passport_real_agent_tool_loop_finishes_once(tmp_path):
    client = Client()
    event = PassportEvent({'session_id': 'session', 'turn_id': 'turn', 'text': '查一下'}, 'h' * 43, client)
    connector = object.__new__(YoumiConnector)
    connector.client, connector.config = client, {'qq_platform_id': 'qq-ci', 'max_dynamic_context_chars': 24000}
    connector.context = SimpleNamespace(get_config=lambda *args: {})
    connector.outbox = CompletionOutbox(Path(tmp_path) / 'outbox')
    event.set_extra('youmi_primary_chat', True)
    tools = ToolSet([FunctionTool(name='web_search', description='fixture',
                    parameters={'type': 'object', 'properties': {'q': {'type': 'string'}}})])
    request = ProviderRequest(model='ci-model', prompt='查一下', func_tool=tools)
    with patch('astrbot.core.astr_main_agent._select_provider', new=AsyncMock(return_value=SimpleNamespace(get_model=lambda: 'ci-model'))):
        await connector.request(event, request)
    provider = ToolProviderFixture()
    runner = ToolLoopAgentRunner()
    await runner.reset(provider=provider, request=request, run_context=ContextWrapper(context=SimpleNamespace(event=event)),
                       tool_executor=ToolExecutorFixture(), agent_hooks=MainAgentHooks(), streaming=False)
    async for _ in runner.step_until_done(5):
        pass
    assert len(provider.calls) == 3
    response = runner.get_final_llm_resp()
    await connector.done(event, None, response)
    event.set_result(MessageEventResult(chain=[Plain(response.completion_text)], result_content_type=ResultContentType.LLM_RESULT))
    await connector.decorate(event)
    assert [action for action, _ in client.calls].count('passport-reply') == 1
    assert [action for action, _ in client.calls].count('complete') == 1
    assert '&&happy&&' not in next(body['text'] for action, body in client.calls if action == 'passport-reply')


@pytest.mark.asyncio
async def test_device_tool_schema_uses_bound_passport_turn(tmp_path):
    client = Client()
    name = 'passport_device_' + 'a' * 24
    client.tools = [{'name': name, 'description': '设置设备音量',
                     'parameters': {'type': 'object', 'properties': {'volume': {'type': 'integer'}}}}]
    event = PassportEvent({'session_id': 'session', 'turn_id': 'turn', 'text': '调小音量'}, 'h' * 43, client)
    connector = object.__new__(YoumiConnector)
    connector.client, connector.config = client, {'qq_platform_id': 'qq-ci', 'max_dynamic_context_chars': 24000}
    connector.context = SimpleNamespace(get_config=lambda *args: {})
    connector.outbox = CompletionOutbox(Path(tmp_path) / 'outbox')
    event.set_extra('youmi_primary_chat', True)
    request = ProviderRequest(model='ci-model', prompt=event.message_str)
    with patch('astrbot.core.astr_main_agent._select_provider', new=AsyncMock(return_value=SimpleNamespace(get_model=lambda: 'ci-model'))):
        await connector.request(event, request)
    tool = request.func_tool.get_tool(name)
    results = [item async for item in FunctionToolExecutor._execute_local(
        tool, ContextWrapper(context=SimpleNamespace(event=event)), volume=3)]
    assert results[0].content[0].text == '设备已执行'
    called = next(body for action, body in client.calls if action == 'passport-tool')
    assert called['name'] == name and called['arguments'] == {'volume': 3}
    assert called['handle'] == event.handle and called['turn_token'] == 'token'
