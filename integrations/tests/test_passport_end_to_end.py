"""Exercise the real bridge and pinned Agent together with a simulated device."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.astr_agent_hooks import MainAgentHooks
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType
from astrbot.core.provider.entities import LLMResponse, ProviderRequest

from integrations.astrbot_plugin_youmi.client import BridgeClient
from integrations.astrbot_plugin_youmi.main import YoumiConnector
from integrations.astrbot_plugin_youmi.passport import poll_passport
from integrations.astrbot_plugin_youmi.turn_state import CompletionOutbox
from integrations.tests.test_astrbot_compatibility import ToolProviderFixture

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'server' / 'tests'))
import test_passport_prepare as passport_fixture


class DeviceProvider(ToolProviderFixture):
    def __init__(self, tool_name):
        super().__init__()
        self.tool_name = tool_name

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs['contexts'])
        if len(self.calls) == 1:
            return LLMResponse(role='assistant', tools_call_name=[self.tool_name],
                tools_call_args=[{'volume': 3}], tools_call_ids=['device-call'])
        return LLMResponse(role='assistant', completion_text='音量已经调好了。')


@pytest.mark.asyncio
async def test_real_http_agent_and_simulated_device_complete_one_turn(tmp_path):
    fixture = passport_fixture.PassportPrepareTests(methodName='test_device_source_is_authoritative_and_scope_cannot_be_forged')
    await fixture.asyncSetUp()
    bridge = BridgeClient({'youmi_base_url': str(fixture.client.make_url('/')).rstrip('/'),
        'service_credential_file': str(fixture.root / 'token'), 'allow_internal_http': True})
    poller = None
    try:
        fixture.waiter.cancel()
        await asyncio.gather(fixture.waiter, return_exceptions=True)
        fixture.conn.sentence_id = 'second-turn'
        definition = {'type': 'function', 'function': {'name': 'set_volume', 'description': '设置音量',
            'parameters': {'type': 'object', 'properties': {'volume': {'type': 'integer'}}}}}
        executor = SimpleNamespace(get_tools=lambda: {'set_volume': SimpleNamespace(description=definition)},
            execute=AsyncMock(return_value=SimpleNamespace(action=SimpleNamespace(name='REQLLM'), result='音量已设置')))
        fixture.conn.func_handler = SimpleNamespace(device_mcp_executor=executor)
        fixture.waiter = await fixture.dispatch.submit(fixture.conn, '把音量调小')
        queue = asyncio.Queue()
        poller = asyncio.create_task(poll_passport(SimpleNamespace(get_event_queue=lambda: queue), bridge))
        event = await asyncio.wait_for(queue.get(), 5)
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)
        poller = None

        connector = object.__new__(YoumiConnector)
        connector.client = bridge
        connector.config = {'qq_platform_id': 'qq-ci', 'max_dynamic_context_chars': 24000}
        connector.context = SimpleNamespace(get_config=lambda *args: {})
        connector.outbox = CompletionOutbox(tmp_path / 'outbox')
        event.set_extra('youmi_primary_chat', True)
        request = ProviderRequest(model='ci-model', prompt=event.message_str)
        with patch('astrbot.core.astr_main_agent._select_provider', new=AsyncMock(return_value=SimpleNamespace(get_model=lambda: 'ci-model'))):
            await connector.request(event, request)
        assert event.get_extra('youmi_turn') is not None
        assert len(request.func_tool.tools) == 1
        provider = DeviceProvider(request.func_tool.tools[0].name)
        runner = ToolLoopAgentRunner()
        await runner.reset(provider=provider, request=request,
            run_context=ContextWrapper(context=SimpleNamespace(event=event)),
            tool_executor=FunctionToolExecutor, agent_hooks=MainAgentHooks(), streaming=False)
        async for _ in runner.step_until_done(5):
            pass
        response = runner.get_final_llm_resp()
        await connector.done(event, None, response)
        event.set_result(MessageEventResult(chain=[Plain(response.completion_text)], result_content_type=ResultContentType.LLM_RESULT))
        await connector.decorate(event)
        assert await asyncio.wait_for(fixture.waiter, 2) == '音量已经调好了。'
        executor.execute.assert_awaited_once_with(fixture.conn, 'set_volume', {'volume': 3})
        assert len(provider.calls) == 2
        assert event.get_extra('youmi_turn').complete_ack
        assert fixture.store.db.execute('SELECT COUNT(*) FROM mia_jobs').fetchone()[0] == 1
        assert fixture.store.db.execute('SELECT COUNT(*) FROM youmi_shared_history_turns').fetchone()[0] == 1
    finally:
        if poller is not None:
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)
        await bridge.close()
        await fixture.asyncTearDown()
