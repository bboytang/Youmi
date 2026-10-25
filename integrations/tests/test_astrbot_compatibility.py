"""Fixed upstream classes/hooks/OneBot serializer; all LLM/network delivery are fixtures."""

import base64
import copy
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from PIL import Image as PillowImage

from astrbot.core.agent.message import TextPart
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.astr_agent_hooks import MainAgentHooks
from astrbot.core.message.components import Image, Plain, Record
from astrbot.core.message.message_event_result import MessageChain, MessageEventResult, ResultContentType
from astrbot.core.pipeline.context_utils import call_event_hook
from astrbot.core.pipeline.respond.stage import RespondStage
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.provider.provider import Provider
from astrbot.core.star.star import StarMetadata, star_map
from astrbot.core.star.star_handler import EventType, StarHandlerMetadata, star_handlers_registry
from astrbot_plugin_meme_manager.main import MemeSender

from integrations.astrbot_plugin_youmi.client import BridgeClient, BridgeError
from integrations.astrbot_plugin_youmi.media import prune_media, selected_image_file
from integrations.astrbot_plugin_youmi.main import YoumiConnector
from integrations.astrbot_plugin_youmi.turn_state import CompletionOutbox, TurnState
from server.companion.astrbot_media import pcm_wav
from server.companion.persona import PersonaAgent


@pytest_asyncio.fixture(autouse=True)
async def close_native_database_pool(monkeypatch):
    # Native sends schedule asynchronous metrics; disable telemetry in isolated
    # tests instead of leaving database/network tasks beyond the test loop.
    monkeypatch.setenv('ASTRBOT_DISABLE_METRICS', '1')
    yield
    # Native tool/admin checks initialize this pool on the current test loop.
    from astrbot.core import db_helper
    await db_helper.engine.dispose()


class HTTPFixture:
    def __init__(self):
        self.calls = []
        self.fail_complete = False
        self.prepared = {'turn_key': 'key', 'turn_token': 'token', 'system_prompt': 'SOLE_YOUMI_CORE',
                         'dynamic_context': {'facts': [], 'memory_status': 'no_match'}}

    async def call(self, action, data):
        self.calls.append((action, copy.deepcopy(data)))
        if action == 'prepare':
            return self.prepared
        if action == 'check':
            text = PersonaAgent().checked_text(data['final_text'])
            return {'checked_text': text, 'replaced': text != data['final_text']}
        if action == 'tts':
            return pcm_wav(b'\0\0' * 2400)
        if action == 'complete' and self.fail_complete:
            raise BridgeError('fixture_network_unavailable')
        return {'status': 'completed'}


@pytest.fixture
def setup(tmp_path):
    sent = []
    bot = SimpleNamespace(send_private_msg=AsyncMock(side_effect=lambda **kwargs: sent.append(kwargs)))
    obj = AstrBotMessage()
    obj.type = MessageType.FRIEND_MESSAGE
    obj.self_id, obj.session_id, obj.message_id = '12345', '67890', '-42'
    obj.sender = MessageMember('67890', 'fixture')
    obj.message, obj.message_str = [Plain('真实用户原话')], '真实用户原话'
    obj.raw_message = {'message': [{'type': 'text', 'data': {'text': '真实用户原话'}}]}
    event = AiocqhttpMessageEvent(obj.message_str, obj, PlatformMetadata('aiocqhttp', 'fixture', 'qq-ci'), '67890', bot)
    async def send_message(umo, chain):
        await event.send(chain)
        return True
    context = SimpleNamespace(get_using_provider_async=AsyncMock(return_value=SimpleNamespace(get_model=lambda: 'ci-model')),
                              send_message=send_message, get_config=lambda **kwargs: {'provider_settings': {}})
    connector = object.__new__(YoumiConnector)
    connector.context, connector.config = context, {'qq_platform_id': 'qq-ci', 'scheduled_bot_uin': '12345',
        'text_voice_policy': 'text', 'max_dynamic_context_chars': 24000}
    connector.directory, connector.outbox = tmp_path, CompletionOutbox(tmp_path / 'outbox')
    connector.client = HTTPFixture()

    # Native Meme fixture with actual image files and parser/selection/decorating methods.
    mapping = {'happy': '开心'}
    memes = tmp_path / 'memes'
    (memes / 'happy').mkdir(parents=True)
    PillowImage.new('RGB', (2, 2)).save(memes / 'happy' / 'meme.gif', format='GIF')
    meme = object.__new__(MemeSender)
    meme.config = {'generation': {'markup': {'enable_alternative': False, 'enable_repeated_detection': False},
                                  'matching': {'enable_loose_matching': False}}}
    meme.context = context
    meme.category_mapping, meme.category_mapping_string = mapping, 'happy: 开心'
    meme.trigger_scope, meme.emotions_probability = 'only_chat_llm', 100
    meme.emotion_llm_enabled, meme.remove_invalid_alternative_markup = False, False
    meme.convert_static_to_gif, meme.send_image_as_base64 = False, False
    meme.enable_mixed_message, meme.mixed_message_probability = True, 100
    meme.streaming_compatibility, meme.filter_all_tags = True, True
    meme.content_cleanup_rule = r'&&[a-zA-Z]*&&'
    meme._semantic_mode_active = lambda event: False
    meme._resolve_runtime_pack_context = lambda **kwargs: {'category_mapping': mapping, 'pack_dir': tmp_path,
                                                          'memes_dir': memes}
    meme._get_runtime_memes_dir_for_event = lambda event: memes
    event.set_extra('meme_manager_llm_request_origin', 'chat')
    return connector, meme, event, bot, sent


@pytest.fixture
def hooks(setup):
    connector, meme, event, bot, sent = setup
    previous_handlers = list(star_handlers_registry._handlers)
    previous_map = dict(star_handlers_registry.star_handlers_map)
    previous_stars = dict(star_map)
    star_handlers_registry._handlers.clear()
    star_handlers_registry.star_handlers_map.clear()
    order = []
    def add(module, name, event_type, priority, method):
        star_map[module] = StarMetadata(name=module, activated=True)
        async def invoke(*args):
            order.append(name)
            return await method(*args)
        star_handlers_registry.append(StarHandlerMetadata(event_type, module + '.' + name, name, module,
                                                         invoke, [], extras_configs={'priority': priority}))
    add('youmi', 'youmi_waiting', EventType.OnWaitingLLMRequestEvent, 100000, connector.waiting)
    add('meme', 'meme_waiting', EventType.OnWaitingLLMRequestEvent, 99999, meme._mark_llm_request_origin_impl)
    add('meme', 'meme_response', EventType.OnLLMResponseEvent, 99999, meme._resp_impl)
    add('meme', 'meme_decorate', EventType.OnDecoratingResultEvent, 99999, meme._on_decorating_result_impl)
    add('youmi', 'youmi_decorate', EventType.OnDecoratingResultEvent, -100000, connector.decorate)
    add('meme', 'meme_after', EventType.OnAfterMessageSentEvent, 0, meme._after_message_sent_impl)
    add('youmi', 'youmi_after', EventType.OnAfterMessageSentEvent, -100000, connector.after)
    add('youmi', 'youmi_done', EventType.OnAgentDoneEvent, -100000, connector.done)
    add('youmi', 'youmi_tool', EventType.OnUsingLLMToolEvent, 100000, connector.tool)
    add('youmi', 'youmi_tool_done', EventType.OnLLMToolRespondEvent, -100000, connector.tool_done)
    try:
        yield order
    finally:
        star_handlers_registry._handlers[:] = previous_handlers
        star_handlers_registry.star_handlers_map.clear()
        star_handlers_registry.star_handlers_map.update(previous_map)
        star_map.clear()
        star_map.update(previous_stars)


async def final_reply(setup, text):
    connector, meme, event, bot, sent = setup
    response = LLMResponse(role='assistant', completion_text=text)
    await MainAgentHooks().on_agent_done(SimpleNamespace(context=SimpleNamespace(event=event)), response)
    event.set_result(MessageEventResult(chain=response.result_chain.chain if response.result_chain else [Plain(response.completion_text)],
                                     result_content_type=ResultContentType.LLM_RESULT))
    await call_event_hook(event, EventType.OnDecoratingResultEvent)
    # The actual native respond stage must have nothing left to send.
    await RespondStage().process(event)
    return response


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['悠米的回复：你好 &&happy&&', '&&happy&&'])
@pytest.mark.parametrize('mixed', [True, False])
async def test_real_meme_order_gif_image_only_and_no_second_send(setup, hooks, text, mixed):
    connector, meme, event, bot, sent = setup
    meme.enable_mixed_message = mixed
    event.set_extra('youmi_turn', TurnState(connector.client.prepared, 'text'))
    await final_reply(setup, text)
    segments = [part for call in sent for part in call['message']]
    images = [part for part in segments if part['type'] == 'image']
    assert len(images) == 1
    gif = base64.b64decode(images[0]['data']['file'].split('://', 1)[1])
    assert gif.startswith(b'GIF')
    assert '&&happy&&' not in str(segments)
    assert '悠米的回复' not in str(segments)
    assert hooks.index('meme_response') < hooks.index('youmi_done') < hooks.index('meme_decorate') < hooks.index('youmi_decorate')
    assert hooks.index('meme_after') < hooks.index('youmi_after')
    assert len([call for call in connector.client.calls if call[0] == 'complete']) == 1
    count = len(sent)
    for _ in range(3): await connector.after(event)
    await RespondStage().process(event)
    assert len(sent) == count
    assert len([call for call in connector.client.calls if call[0] == 'complete']) == 1


@pytest.mark.asyncio
async def test_voice_and_text_native_onebot_serialization_and_cleanup(setup, hooks):
    connector, meme, event, bot, sent = setup
    connector.config['text_voice_policy'] = 'text_and_voice'
    event.set_extra('youmi_turn', TurnState(connector.client.prepared, 'asr'))
    await final_reply(setup, '你好 &&happy&&')
    segments = [part for call in sent for part in call['message']]
    assert sorted(part['type'] for part in segments) == ['image', 'record', 'text']
    assert len([call for call in connector.client.calls if call[0] == 'complete']) == 1
    assert not list(connector.directory.glob('youmi-*.wav'))


@pytest.mark.asyncio
async def test_send_exception_is_failed_and_never_successful_completion(setup, hooks):
    connector, meme, event, bot, sent = setup
    bot.send_private_msg.side_effect = RuntimeError('fixture QQ offline')
    event.set_extra('youmi_turn', TurnState(connector.client.prepared, 'text'))
    await final_reply(setup, '你好')
    completions = [body for action, body in connector.client.calls if action == 'complete']
    assert len(completions) == 1 and completions[0]['result'] == 'failed'
    assert event.get_result() is None


@pytest.mark.asyncio
async def test_request_object_reuse_and_new_request_keep_tool_data_intact(setup):
    connector, meme, event, bot, sent = setup
    event.set_extra('youmi_primary_chat', True)
    tools = ToolSet([FunctionTool(name='web_search', description='fixture search', parameters={'type': 'object'})])
    contexts = [{'role': 'user', 'content': 'earlier'}, {'role': 'assistant', 'tool_calls': [
        {'id': 'call-1', 'type': 'function', 'function': {'name': 'web_search', 'arguments': '{"q":"fixture"}'}}]},
        {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'fixture result'}]
    original = copy.deepcopy(contexts)
    req = ProviderRequest(model='ci-model', prompt='actual', system_prompt='native tool and Meme instructions',
                          contexts=contexts, func_tool=tools, extra_user_content_parts=[TextPart(text='other plugin context')])
    event.set_extra('youmi_turn', TurnState(connector.client.prepared, 'text'))
    for request in [req, req, ProviderRequest(model='ci-model', contexts=contexts, func_tool=tools)]:
        await connector.request(event, request)
        assert request.system_prompt.count('SOLE_YOUMI_CORE') == 1
        assert len([part for part in request.extra_user_content_parts if part.text.startswith('YOUMI_DYNAMIC_CONTEXT_V1')]) == 1
        assert request.func_tool is tools and request.contexts == original
    assert req.extra_user_content_parts[0].text == 'other plugin context'
    assert not [call for call in connector.client.calls if call[0] == 'prepare']


@pytest.mark.asyncio
async def test_image_caption_never_becomes_original_user_evidence(setup):
    connector, meme, event, bot, sent = setup
    event.set_extra('youmi_primary_chat', True)
    event.message_obj.raw_message = {'message': [{'type': 'text', 'data': {'text': '看看这个'}},
                                               {'type': 'image', 'data': {}}]}
    event.message_obj.message = [Plain('看看这个'), Plain('视觉模型推断：用户喜欢蓝色')]
    request = ProviderRequest(model='ci-model', prompt='visual prompt', image_urls=['fixture-image'])
    with patch('astrbot.core.astr_main_agent._select_provider', new=AsyncMock(return_value=SimpleNamespace())):
        await connector.request(event, request)
    body = next(body for action, body in connector.client.calls if action == 'prepare')
    assert body['query_text'] == '看看这个' and body['source_kind'] == 'text'
    assert request.image_urls == ['fixture-image']


@pytest.mark.asyncio
async def test_failed_stt_sends_one_notice_without_fabricated_transcription(setup, hooks):
    connector, meme, event, bot, sent = setup
    event.set_extra('youmi_primary_chat', True)
    event.message_obj.raw_message = {'message': [{'type': 'record', 'data': {}}]}
    event.message_obj.message = [Record(file='fixture.wav')]
    with patch('astrbot.core.astr_main_agent._select_provider', new=AsyncMock(return_value=SimpleNamespace())):
        await connector.request(event, ProviderRequest(model='ci-model', prompt=''))
        await connector.request(event, ProviderRequest(model='ci-model', prompt=''))
    assert event.is_stopped()
    prepared = [body for action, body in connector.client.calls if action == 'prepare']
    assert len(prepared) == 1 and prepared[0]['source_kind'] == 'media'
    assert len(sent) == 1 and '改发文字' in str(sent)
    assert len([body for action, body in connector.client.calls if action == 'complete']) == 1


@pytest.mark.asyncio
async def test_snowluma_native_transcription_preserves_original_source_and_runs_once(setup, hooks):
    connector, meme, event, bot, sent = setup
    event.message_obj.raw_message = {'message': [{'type': 'record', 'data': {'file': 'original-ptt'}}]}
    original = copy.deepcopy(event.message_obj.raw_message)
    event.message_obj.message = [Record(file='original-ptt')]
    event.message_str = event.message_obj.message_str = ''
    bot.call_action = AsyncMock(return_value={'text': '真实语音转写'})
    await call_event_hook(event, EventType.OnWaitingLLMRequestEvent)
    await call_event_hook(event, EventType.OnWaitingLLMRequestEvent)
    assert hooks[:2] == ['youmi_waiting', 'meme_waiting']
    assert event.get_extra('meme_manager_llm_request_origin') == 'chat'
    bot.call_action.assert_awaited_once_with('fetch_ptt_text', message_id='-42')
    assert event.message_str == event.message_obj.message_str == '真实语音转写'
    assert event.message_obj.raw_message == original
    assert [part.text for part in event.get_messages() if isinstance(part, Plain)] == ['真实语音转写']
    assert not any(isinstance(part, Record) for part in event.get_messages())
    with patch('astrbot.core.astr_main_agent._select_provider', new=AsyncMock(return_value=SimpleNamespace())):
        await connector.request(event, ProviderRequest(model='ci-model', prompt=event.message_str))
    prepared = next(body for action, body in connector.client.calls if action == 'prepare')
    assert prepared['source_kind'] == 'asr' and prepared['query_text'] == '真实语音转写'
    assert not sent


@pytest.mark.asyncio
@pytest.mark.parametrize('result', [{}, {'text': ''}, {'text': 'x' * 4097}, TimeoutError()])
async def test_snowluma_native_transcription_failure_notices_once_without_model(setup, hooks, result):
    connector, meme, event, bot, sent = setup
    event.message_obj.raw_message = {'message': [{'type': 'record', 'data': {}}]}
    event.message_obj.message = [Record(file='original-ptt')]
    event.set_extra('meme_manager_llm_request_origin', None)
    bot.call_action = AsyncMock(side_effect=result) if isinstance(result, Exception) else AsyncMock(return_value=result)
    provider = SimpleNamespace(get_model=lambda: 'ci-model')
    with patch('astrbot.core.astr_main_agent._select_provider', new=AsyncMock(return_value=provider)):
        await connector.waiting(event)
        await connector.waiting(event)
    assert event.is_stopped() and bot.call_action.await_count == 1
    assert len(sent) == 1 and '改发文字' in str(sent)
    prepared = [body for action, body in connector.client.calls if action == 'prepare']
    assert len(prepared) == 1 and prepared[0]['source_kind'] == 'media'
    assert len([body for action, body in connector.client.calls if action == 'complete']) == 1


@pytest.mark.asyncio
async def test_snowluma_transcription_keeps_native_stt_and_plugin_requests(setup):
    connector, meme, event, bot, sent = setup
    bot.call_action = AsyncMock()
    await connector.waiting(event)  # Native STT already produced Plain components.
    event.message_obj.message = [Record(file='plugin-voice')]
    event.set_extra('provider_request', ProviderRequest(prompt='independent plugin'))
    await connector.waiting(event)
    bot.call_action.assert_not_awaited()
    assert isinstance(event.get_messages()[0], Record) and not connector.client.calls


@pytest.mark.asyncio
async def test_snowluma_ambiguous_voices_do_not_guess_a_transcript(setup, hooks):
    connector, meme, event, bot, sent = setup
    event.message_obj.message = [Record(file='first'), Record(file='second')]
    bot.call_action = AsyncMock()
    provider = SimpleNamespace(get_model=lambda: 'ci-model')
    with patch('astrbot.core.astr_main_agent._select_provider', new=AsyncMock(return_value=provider)):
        await connector.waiting(event)
    bot.call_action.assert_not_awaited()
    assert event.is_stopped() and len(sent) == 1
    prepared = next(body for action, body in connector.client.calls if action == 'prepare')
    assert prepared['source_kind'] == 'media'


@pytest.mark.asyncio
async def test_snowluma_failure_notice_preserves_owner_authorization(setup):
    connector, meme, event, bot, sent = setup
    event.message_obj.message = [Record(file='voice')]
    bot.call_action = AsyncMock(return_value={})
    connector.client.call = AsyncMock(side_effect=BridgeError('owner_authorization_required', 403))
    provider = SimpleNamespace(get_model=lambda: 'ci-model')
    with patch('astrbot.core.astr_main_agent._select_provider', new=AsyncMock(return_value=provider)):
        await connector.waiting(event)
    assert event.is_stopped() and not sent
    assert connector.client.call.await_count == 1
    assert connector.client.call.await_args.args[0] == 'prepare'


@pytest.mark.asyncio
async def test_missing_original_text_cannot_create_user_evidence(setup):
    connector, meme, event, bot, sent = setup
    event.set_extra('youmi_primary_chat', True)
    event.message_obj.raw_message = {'message': '[CQ:image,file=fixture]'}
    event.message_obj.message = [Plain('preprocessor description')]
    with patch('astrbot.core.astr_main_agent._select_provider', new=AsyncMock(return_value=SimpleNamespace())):
        await connector.request(event, ProviderRequest(model='ci-model', prompt='visual prompt'))
    body = next(body for action, body in connector.client.calls if action == 'prepare')
    assert body['source_kind'] == 'media'
    assert 'preprocessor description' not in body['query_text']


class ToolProviderFixture(Provider):
    def __init__(self):
        super().__init__({}, {})
        self.calls = []

    def get_current_key(self): return 'fixture'
    def set_key(self, key): pass
    async def get_models(self): return ['ci-model']

    async def text_chat(self, **kwargs):
        self.calls.append(copy.deepcopy([message.model_dump() if hasattr(message, 'model_dump') else message
                                         for message in kwargs['contexts']]))
        index = len(self.calls)
        if index <= 2:
            return LLMResponse(role='assistant', tools_call_name=['web_search'],
                tools_call_args=[{'q': 'query-' + str(index)}], tools_call_ids=['call-' + str(index)])
        return LLMResponse(role='assistant', completion_text='最终回复 &&happy&&')

    async def text_chat_stream(self, **kwargs):
        yield await self.text_chat(**kwargs)


class ToolExecutorFixture:
    @classmethod
    async def execute(cls, tool, run_context, **args):
        from mcp.types import CallToolResult, TextContent
        yield CallToolResult(content=[TextContent(type='text', text='result-' + args['q'])])


@pytest.mark.asyncio
async def test_actual_builtin_agent_two_tool_continuations_one_logical_turn(setup, hooks):
    connector, meme, event, bot, sent = setup
    event.set_extra('youmi_primary_chat', True)
    event.set_extra('youmi_turn', TurnState(connector.client.prepared, 'text'))
    tools = ToolSet([FunctionTool(name='web_search', description='fixture',
                    parameters={'type': 'object', 'properties': {'q': {'type': 'string'}}})])
    req = ProviderRequest(model='ci-model', prompt='真实用户原话', func_tool=tools)
    await connector.request(event, req)
    provider = ToolProviderFixture()
    context = ContextWrapper(context=SimpleNamespace(event=event))
    runner = ToolLoopAgentRunner()
    await runner.reset(provider=provider, request=req, run_context=context,
        tool_executor=ToolExecutorFixture(), agent_hooks=MainAgentHooks(), streaming=False)
    async for _ in runner.step_until_done(5): pass
    assert len(provider.calls) == 3
    for index, payload in enumerate(provider.calls):
        assert sum('SOLE_YOUMI_CORE' in str(message) for message in payload) == 1
        assert sum('YOUMI_DYNAMIC_CONTEXT_V1' in str(message) for message in payload) == 1
        results = [message for message in payload if message['role'] == 'tool']
        assert [message['tool_call_id'] for message in results] == ['call-' + str(i + 1) for i in range(index)]
    response = runner.get_final_llm_resp()
    event.set_result(MessageEventResult(chain=[Plain(response.completion_text)], result_content_type=ResultContentType.LLM_RESULT))
    await call_event_hook(event, EventType.OnDecoratingResultEvent)
    assert len([call for call in connector.client.calls if call[0] == 'complete']) == 1
    assert event.get_extra('youmi_turn').tools == ['web_search']


@pytest.mark.asyncio
async def test_non_llm_command_and_plugin_model_request_are_untouched(setup):
    connector, meme, event, bot, sent = setup
    req = ProviderRequest(prompt='auxiliary classification', system_prompt='plugin independent task')
    event.set_extra('provider_request', req)
    await connector.waiting(event)
    await connector.request(event, req)
    assert req.system_prompt == 'plugin independent task'
    event.set_result(MessageEventResult(chain=[Plain('command output')]))
    await connector.decorate(event)
    assert event.get_result().chain[0].text == 'command output'
    assert not connector.client.calls


@pytest.mark.asyncio
async def test_complete_outage_preserves_only_complete_outbox_without_resend(setup, hooks):
    connector, meme, event, bot, sent = setup
    connector.client.fail_complete = True
    event.set_extra('youmi_turn', TurnState(connector.client.prepared, 'text'))
    await final_reply(setup, '你好')
    assert len(list(connector.outbox.pending())) == 1
    count = len(sent)
    connector.client.fail_complete = False
    for body in list(connector.outbox.pending()):
        await connector.client.call('complete', body)
        connector.outbox.remove(body)
    assert not list(connector.outbox.pending()) and len(sent) == count


@pytest.mark.asyncio
async def test_native_cron_context_send_tool_meme_and_no_fake_user_turn(setup):
    from astrbot.core.agent.message import Message
    from astrbot.core.cron.events import CronMessageEvent
    from astrbot.core.tools.message_tools import SendMessageToUserTool
    from mcp.types import CallToolResult, TextContent
    connector, meme, original, bot, sent = setup
    meme.trigger_scope = 'chat_and_plugin_llm'
    connector.context.get_registered_star = lambda name: SimpleNamespace(star_cls=meme, activated=True)
    event = CronMessageEvent(context=connector.context, session=original.session, message='定时主动联系')
    run_context = ContextWrapper(context=SimpleNamespace(event=event, context=connector.context),
                                 messages=[Message(role='system', content='native cron tool instructions')])
    await connector.scheduled(event, run_context)
    prepare = [body for action, body in connector.client.calls if action == 'prepare'][0]
    assert prepare['origin'] == 'scheduled' and prepare['source_kind'] == 'scheduled'
    assert prepare['bot_uin'] == '12345' and prepare['sender_uin'] == '67890'
    assert run_context.messages[0]._no_save and run_context.messages[-1]._no_save
    assert 'native cron tool instructions' in str(run_context.messages)
    tool = SendMessageToUserTool()
    args = {'messages': [{'type': 'plain', 'text': '悠米的回复：该休息了 &&happy&&'}]}
    await connector.tool(event, tool, args)
    # Test the original tool, including its session authorization and path checks.
    result = await tool.call(run_context, **args)
    assert result == 'Message sent to session ' + event.unified_msg_origin
    await connector.tool_done(event, tool, args, CallToolResult(content=[TextContent(type='text', text=result)]))
    await connector.done(event, run_context, LLMResponse(role='assistant', completion_text='内部调度摘要'))
    completions = [body for action, body in connector.client.calls if action == 'complete']
    assert len(completions) == 1 and completions[0]['final_text'] == '该休息了'
    segments = [part for call in sent for part in call['message']]
    assert sorted(part['type'] for part in segments) == ['image', 'text']
    assert '&&happy&&' not in str(segments)


@pytest.mark.asyncio
async def test_send_tool_check_failure_fails_closed_and_preserves_other_tools(setup):
    connector, meme, event, bot, sent = setup
    event.set_extra('youmi_turn', TurnState(connector.client.prepared, 'text'))
    connector.client.call = AsyncMock(side_effect=BridgeError('authorization_required', 403))
    args = {'messages': [{'type': 'plain', 'text': 'private reply'}]}
    await connector.tool(event, SimpleNamespace(name='send_message_to_user'), args)
    assert args == {} and not sent
    other = {'query': 'retain original tool parameters'}
    await connector.tool(event, SimpleNamespace(name='web_search'), other)
    assert other == {'query': 'retain original tool parameters'}


@pytest.mark.asyncio
async def test_split_plain_tool_output_checked_as_one_visible_text(setup):
    connector, meme, event, bot, sent = setup
    event.set_extra('youmi_turn', TurnState(connector.client.prepared, 'text'))
    args = {'messages': [{'type': 'plain', 'text': '我是'}, {'type': 'plain', 'text': 'ChatGPT'},
                         {'type': 'image', 'url': 'https://example.invalid/keep-original'}]}
    await connector.tool(event, SimpleNamespace(name='send_message_to_user'), args)
    assert args['messages'][0]['text'] != '我是'
    assert args['messages'][-1] == {'type': 'image', 'url': 'https://example.invalid/keep-original'}


@pytest.mark.asyncio
async def test_tool_image_conversion_failure_removes_all_owned_temp_files(setup, tmp_path, monkeypatch):
    connector, meme, event, bot, sent = setup
    connector.context.get_registered_star = lambda name: SimpleNamespace(star_cls=meme, activated=True)
    event.set_extra('youmi_turn', TurnState(connector.client.prepared, 'text'))
    temporary = tmp_path / 'meme-conversion.tmp'
    temporary.write_bytes(b'caller-owned conversion')
    source = tmp_path / 'memes/happy/meme.gif'
    image = Image.fromFileSystem(str(source))
    broken = SimpleNamespace(convert_to_file_path=AsyncMock(side_effect=ValueError('injected conversion failure')))
    meme.compat_prepare_message = AsyncMock(return_value={'cleaned_chain': MessageChain([Plain('你好')]),
        'images': [image, broken], 'temp_files': [str(temporary)]})
    target = tmp_path / 'tool-temp'
    monkeypatch.setattr('astrbot.core.utils.astrbot_path.get_astrbot_temp_path', lambda: str(target))
    args = {'messages': [{'type': 'plain', 'text': '你好 &&happy&&'}]}
    await connector.tool(event, SimpleNamespace(name='send_message_to_user'), args)
    assert args == {} and not sent
    assert not temporary.exists()
    assert not list(target.glob('youmi-meme-*'))
    assert source.exists()


@pytest.mark.asyncio
async def test_agent_interruption_cleans_tool_files_without_tool_end(setup, hooks, tmp_path, monkeypatch):
    from astrbot.core.tools.message_tools import SendMessageToUserTool
    connector, meme, event, bot, sent = setup
    connector.context.get_registered_star = lambda name: SimpleNamespace(star_cls=meme, activated=True)
    event.set_extra('youmi_turn', TurnState(connector.client.prepared, 'text'))
    target = tmp_path / 'tool-temp'
    monkeypatch.setattr('astrbot.core.utils.astrbot_path.get_astrbot_temp_path', lambda: str(target))
    args = {'messages': [{'type': 'plain', 'text': '你好 &&happy&&'}]}
    await connector.tool(event, SendMessageToUserTool(), args)
    assert list(target.glob('youmi-meme-*'))
    response = LLMResponse(role='assistant', completion_text=ToolLoopAgentRunner.USER_INTERRUPTION_MESSAGE)
    await MainAgentHooks().on_agent_done(SimpleNamespace(context=SimpleNamespace(event=event)), response)
    assert not list(target.glob('youmi-meme-*'))
    assert not event.get_extra('youmi_turn').agent_done and not sent
    assert not [body for action, body in connector.client.calls if action == 'complete']


def test_restart_cleanup_expires_only_owned_media_and_preserves_recent_files(tmp_path):
    audio, images, library = [tmp_path / name for name in ('audio', 'images', 'library')]
    for directory in (audio, images, library):
        directory.mkdir()
    now = 2000000000
    old = [audio / 'youmi-expired.wav', images / 'youmi-meme-expired.gif']
    keep = [audio / 'youmi-current.wav', images / 'youmi-meme-current.gif',
            audio / 'other.wav', images / 'other.gif', library / 'youmi-meme-original.gif']
    for path in old + keep:
        path.write_bytes(b'fixture')
        timestamp = now if 'current' in path.name else now - 301
        os.utime(path, (timestamp, timestamp))
    assert prune_media(audio, images, now=now) == 2
    assert not any(path.exists() for path in old)
    assert all(path.exists() for path in keep)
    assert prune_media(audio, images, now=now) == 0


def test_selected_image_growing_after_stat_stays_bounded_and_leaves_no_copy(tmp_path):
    source, target = tmp_path / 'growing.gif', tmp_path / 'temporary'
    with source.open('wb') as output:
        output.truncate(20 * 1024 * 1024 + 1)
    original_stat = Path.stat
    def stat(path, *args, **kwargs):
        return SimpleNamespace(st_size=1) if path == source else original_stat(path, *args, **kwargs)
    with patch.object(Path, 'stat', autospec=True, side_effect=stat):
        with pytest.raises(ValueError, match='unsupported_meme_image'):
            selected_image_file(source, target)
    assert source.exists() and not list(target.iterdir())


@pytest.mark.asyncio
async def test_missing_service_secret_is_retryable_and_transport_recovers(tmp_path):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    seen = []
    async def completed(request):
        assert request.headers['Authorization'] == 'Bearer ' + 's' * 43
        seen.append(await request.json())
        return web.json_response({'status': 'completed'})
    app = web.Application()
    app.router.add_post('/internal/astrbot/complete', completed)
    async with TestServer(app) as server:
        credential = tmp_path / 'service-token'
        client = BridgeClient({'youmi_base_url': str(server.make_url('')).rstrip('/'),
            'allow_internal_http': True, 'service_credential_file': str(credential)})
        try:
            with pytest.raises(BridgeError) as caught:
                await client.call('complete', {'turn_token': 'opaque'})
            assert caught.value.status == 503 and str(caught.value) == 'service_credential_unavailable'
            assert not seen
            credential.write_text('s' * 43)
            assert (await client.call('complete', {'turn_token': 'opaque'}))['status'] == 'completed'
            assert len(seen) == 1 and seen[0]['protocol_version'] == 1
        finally:
            await client.close()
