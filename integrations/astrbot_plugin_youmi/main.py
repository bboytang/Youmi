"""Youmi context and exactly one final dispatch through AstrBot's existing adapter."""

import asyncio
import json
from pathlib import Path
import uuid

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.agent.message import Message, TextPart
from astrbot.core.message.components import Plain, Record
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.pipeline.context_utils import call_event_hook
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.star_handler import EventType

from .client import BridgeClient, BridgeError
from .media import original_text, save_wav, selected_image_file, source_kind
from .turn_state import CompletionOutbox, CONTEXT_MARKER, TurnState, merge_system


class YoumiConnector(Star):
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config
        self.client = BridgeClient(config)
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path
        self.directory = Path(get_astrbot_data_path()) / 'plugin_data' / 'astrbot_plugin_youmi'
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.outbox = CompletionOutbox(self.directory / 'completion-outbox')
        self.retry_task = None

    async def initialize(self):
        self.retry_task = asyncio.create_task(self.retry_completions())

    async def terminate(self):
        if self.retry_task:
            self.retry_task.cancel()
            await asyncio.gather(self.retry_task, return_exceptions=True)
        await self.client.close()

    def supports(self, event):
        return (event.get_platform_id() == self.config['qq_platform_id']
                and not event.get_group_id() and event.get_platform_name() in {'aiocqhttp', 'cron'})

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100000)
    async def message(self, event: AstrMessageEvent):
        if self.supports(event):
            # This runs before InternalAgentSubStage reads enable_streaming.
            event.set_extra('enable_streaming', False)
            event.set_extra('enable_reasoning', False)

    @filter.on_waiting_llm_request(priority=100000)
    async def waiting(self, event: AstrMessageEvent):
        if self.supports(event):
            event.set_extra('youmi_primary_chat', event.get_extra('provider_request') is None)

    @filter.command('youmi_memory')
    async def memory_command(self, event: AstrMessageEvent, action: str = 'list', memory_id: str = '', content: str = ''):
        if not self.supports(event) or event.get_platform_name() == 'cron':
            return
        event.stop_event()
        if action not in {'list', 'update', 'delete', 'pause', 'resume', 'feature_settings', 'events', 'emotion_status'}:
            yield event.plain_result('用法：/youmi_memory list|delete <id>|update <id> <内容>|pause on/off|resume')
            return
        command = {'action': action}
        if action in {'update', 'delete'}:
            command['id'] = memory_id
        if action == 'update':
            command.update(content=content, confirmed=True)
        if action == 'pause':
            if memory_id not in {'on', 'off'}:
                yield event.plain_result('pause 需要 on 或 off；此开关作用于同一智能体的所有客户端。')
                return
            command['paused'] = memory_id == 'on'
        try:
            result = await self.client.call('manage', {'bot_uin': str(event.message_obj.self_id),
                'sender_uin': str(event.get_sender_id()), 'conversation_key': 'qq:private:' + str(event.get_sender_id()),
                'request': command})
            yield event.plain_result(json.dumps(result, ensure_ascii=False))
        except BridgeError as error:
            yield event.plain_result('记忆管理未完成：' + error.code)

    async def prepare(self, event, model, query, budget, kind, origin='user'):
        attempt = event.get_extra('youmi_attempt_id')
        if not attempt:
            attempt = uuid.uuid4().hex
            event.set_extra('youmi_attempt_id', attempt)
        bot = str(event.message_obj.self_id) if origin == 'user' else self.config['scheduled_bot_uin']
        return await self.client.call('prepare', {
            'bot_uin': bot, 'sender_uin': str(event.get_sender_id()),
            'conversation_key': 'qq:private:' + str(event.get_sender_id()),
            'source_message_id': str(event.message_obj.message_id), 'attempt_id': attempt,
            'query_text': query, 'model': model, 'external_input_bytes': budget,
            'origin': origin, 'source_kind': kind})

    @filter.on_llm_request(priority=100000)
    async def request(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.supports(event) or not event.get_extra('youmi_primary_chat', False):
            return
        try:
            state = event.get_extra('youmi_turn')
            if state is None:
                kind = source_kind(event.message_obj.raw_message)
                text = ''.join(part.text for part in event.get_messages() if isinstance(part, Plain)).strip()
                if kind == 'text':
                    text = original_text(event.message_obj.raw_message)
                    if not text:
                        kind = 'media'
                if kind == 'asr' and (not text or any(isinstance(part, Record) for part in event.get_messages())):
                    event.set_extra('youmi_stt_failed', True)
                    kind, text = 'media', ''
                # Do not persist a vision model's caption as a user's self report.
                query = text or '收到用户媒体，请依据本轮媒体内容回应；媒体推断不是用户自述。'
                from astrbot.core.astr_main_agent import _select_provider
                provider = await _select_provider(event, self.context)
                model = req.model or provider.get_model()
                budget = len(json.dumps({'history': req.contexts, 'system': req.system_prompt,
                    'tools': req.func_tool.openai_schema() if req.func_tool else [],
                    'extra': [part.model_dump() if hasattr(part, 'model_dump') else part
                              for part in req.extra_user_content_parts]}, ensure_ascii=False).encode())
                budget += 8192 * (len(req.image_urls) + len(req.audio_urls))
                budget += 8192  # Reserve subsequent native plugin prompt additions, including Meme rules.
                prepared = await self.prepare(event, model, query, budget, kind)
                state = TurnState(prepared, kind)
                event.set_extra('youmi_turn', state)
            if event.get_extra('youmi_stt_failed'):
                await self.notice(event, state, '这段语音没能识别成功，请改发文字或稍后重试。')
                event.stop_event()
                event.clear_result()
                return
            req.system_prompt = merge_system(req.system_prompt, state.prepared['system_prompt'])
            req.extra_user_content_parts[:] = [part for part in req.extra_user_content_parts
                if not (isinstance(part, TextPart) and part.text.startswith(CONTEXT_MARKER))]
            dynamic = CONTEXT_MARKER + json.dumps({'youmi_context': state.prepared['dynamic_context']}, ensure_ascii=False)
            if len(dynamic) > self.config.get('max_dynamic_context_chars', 24000):
                raise BridgeError('dynamic_context_too_large', 400)
            req.extra_user_content_parts.append(TextPart(text=dynamic).mark_as_temp())
        except Exception as error:
            event.stop_event()
            event.clear_result()
            logger.warning('Youmi prepare stopped (%s)', error.code if isinstance(error, BridgeError) else 'dependency_unavailable')

    async def notice(self, event, state, text):
        """A failed transcription is a media notice, never a fabricated user turn."""
        if state.sent:
            return
        checked = await self.client.call('check', {'turn_token': state.prepared['turn_token'], 'final_text': text})
        state.final_text, state.components = checked['checked_text'], ['plain']
        await self.client.call('claim', {'turn_token': state.prepared['turn_token'],
            'completion_id': state.completion_id, 'final_text': state.final_text})
        try:
            await event.send(MessageChain([Plain(state.final_text)]))
        except Exception:
            await self.complete(state, 'failed')
            raise
        state.sent = True
        await call_event_hook(event, EventType.OnAfterMessageSentEvent)
        await self.complete(state)

    @filter.on_agent_begin(priority=100000)
    async def scheduled(self, event: AstrMessageEvent, run_context):
        if not self.supports(event) or event.get_platform_name() != 'cron' or event.get_extra('youmi_turn'):
            return
        if not self.config.get('enable_active_message_context', True):
            return
        try:
            provider = await self.context.get_using_provider_async(event.unified_msg_origin)
            budget = len(json.dumps([message.model_dump() for message in run_context.messages],
                                    ensure_ascii=False, default=str).encode()) + self.config.get('scheduled_tool_budget', 8192)
            prepared = await self.prepare(event, provider.get_model(), event.message_str[:4096] or '定时主动联系',
                                          budget, 'scheduled', 'scheduled')
            event.set_extra('youmi_turn', TurnState(prepared, 'scheduled'))
            system = Message(role='system', content=prepared['system_prompt'])
            system._no_save = True
            run_context.messages.insert(0, system)
            part = TextPart(text=CONTEXT_MARKER + json.dumps({'youmi_context': prepared['dynamic_context']},
                                                          ensure_ascii=False)).mark_as_temp()
            message = Message(role='user', content=[part])
            message._no_save = True
            run_context.messages.append(message)
        except Exception:
            event.set_extra('youmi_scheduled_failed', True)
            logger.warning('Youmi scheduled context unavailable; visible send tool will be rejected')

    @filter.on_using_llm_tool(priority=100000)
    async def tool(self, event: AstrMessageEvent, tool, tool_args):
        state = event.get_extra('youmi_turn')
        if state and tool.name not in state.tools:
            state.tools.append(tool.name)
        if self.supports(event) and tool.name == 'send_message_to_user' and isinstance(tool_args, dict):
            if not state or event.get_extra('youmi_scheduled_failed'):
                if event.get_platform_name() == 'cron':
                    tool_args.clear()
                return
            try:
                parts = tool_args.get('messages', [])
                # The native Meme public compatibility API owns tag parsing and
                # image choice for tool/plugin sends too. Respect its scope.
                lookup = getattr(self.context, 'get_registered_star', None)
                metadata = lookup('meme_manager') if callable(lookup) else None
                meme = metadata.star_cls if metadata and metadata.activated else None
                origin = event.get_extra('meme_manager_llm_request_origin', 'plugin')
                if meme:
                    attach = origin == 'chat' or getattr(meme, 'trigger_scope', '') == 'chat_and_plugin_llm'
                    updated = []
                    for part in parts:
                        if part.get('type') != 'plain':
                            updated.append(part)
                            continue
                        prepared = await meme.compat_prepare_message(event, part.get('text', ''))
                        updated.extend({'type': 'plain', 'text': item.text} for item in prepared['cleaned_chain'].chain
                                       if isinstance(item, Plain))
                        if attach:
                            for image in prepared['images']:
                                from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
                                path = await asyncio.to_thread(selected_image_file, await image.convert_to_file_path(),
                                                               get_astrbot_temp_path())
                                prepared['temp_files'].append(str(path))
                                updated.append({'type': 'image', 'path': str(path)})
                        files = event.get_extra('youmi_tool_files', {})
                        files.setdefault(id(tool_args), []).extend(prepared['temp_files'])
                        event.set_extra('youmi_tool_files', files)
                    parts = tool_args['messages'] = updated
                plain = [part for part in parts if part.get('type') == 'plain']
                text = ''.join(part.get('text', '') for part in plain)
                checked = await self.client.call('check', {'turn_token': state.prepared['turn_token'], 'final_text': text})
                if checked['checked_text'] != text and plain:
                    plain[0]['text'] = checked['checked_text']
                    for part in plain[1:]:
                        part['text'] = ''
                    # The native tool rejects empty Plain; keep all media.
                    tool_args['messages'] = [part for part in parts if part.get('type') != 'plain' or part.get('text')]
            except Exception:
                # AstrBot logs/catches hook errors; mutate the outgoing visible
                # send call to missing required arguments before returning.
                tool_args.clear()
                logger.warning('Youmi visible send tool rejected: context unavailable')

    @filter.on_llm_tool_respond(priority=-100000)
    async def tool_done(self, event: AstrMessageEvent, tool, tool_args, tool_result):
        if tool.name != 'send_message_to_user':
            return
        state = event.get_extra('youmi_turn')
        try:
            if state and tool_result and not getattr(tool_result, 'isError', False):
                text = '\n'.join(getattr(part, 'text', '') for part in getattr(tool_result, 'content', []))
                if text.strip() == 'Message sent to session ' + event.unified_msg_origin:
                    state.tool_texts.append(''.join(part.get('text', '') for part in tool_args.get('messages', [])
                                                  if part.get('type') == 'plain'))
                    state.tool_components.extend(part.get('type', '') for part in tool_args.get('messages', []))
        finally:
            files = event.get_extra('youmi_tool_files', {}).pop(id(tool_args), [])
            for path in files:
                Path(path).unlink(missing_ok=True)

    @filter.on_agent_done(priority=-100000)
    async def done(self, event: AstrMessageEvent, run_context, response):
        state = event.get_extra('youmi_turn')
        if state is None or response is None or response.role != 'assistant':
            return
        # This pinned runner calls on_agent_done for aborted output too.
        from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
        state.agent_done = response.completion_text != ToolLoopAgentRunner.USER_INTERRUPTION_MESSAGE
        # Cron sends through a native tool; an empty normal final response may
        # do the same. Its actual tool return, not after_message_sent, is proof
        # of adapter acceptance. Prepare already reserved this event durably.
        if (state.agent_done and state.tool_components and (state.source_kind == 'scheduled'
                or not response.completion_text and not response.result_chain)):
            state.final_text = '\n'.join(state.tool_texts)
            state.components = list(dict.fromkeys(state.tool_components))
            try:
                await self.client.call('claim', {'turn_token': state.prepared['turn_token'],
                    'completion_id': state.completion_id, 'final_text': state.final_text})
                state.sent = True
                await self.complete(state)
            except BridgeError as error:
                logger.warning('Youmi native tool completion rejected (%s)', error.code)

    @filter.on_decorating_result(priority=-100000)
    async def decorate(self, event: AstrMessageEvent):
        state = event.get_extra('youmi_turn')
        if state is None or state.source_kind == 'scheduled':
            return
        result = event.get_result()
        if result is None or not result.is_llm_result():
            return
        if not state.agent_done:
            event.clear_result()
            return
        if state.sent:
            event.clear_result()
            return
        audio_path = None
        claimed = False
        try:
            text = ''.join(part.text for part in result.chain if isinstance(part, Plain))
            checked = await self.client.call('check', {'turn_token': state.prepared['turn_token'], 'final_text': text})
            visible = checked['checked_text']
            plain = [part for part in result.chain if isinstance(part, Plain)]
            if visible != text:
                if plain:
                    plain[0].text = visible
                    for part in plain[1:]:
                        part.text = ''
                elif visible:
                    result.chain.append(Plain(visible))
            state.final_text = visible
            policy = self.config.get('text_voice_policy', 'reply_to_voice')
            if visible.strip() and (policy == 'text_and_voice' or policy == 'reply_to_voice' and state.source_kind == 'asr'):
                try:
                    audio = await self.client.call('tts', {'turn_token': state.prepared['turn_token'], 'text': visible})
                    audio_path = save_wav(audio, self.directory)
                    result.chain.append(Record.fromFileSystem(str(audio_path)))
                except Exception:
                    logger.warning('Youmi TTS unavailable; using checked text')
            state.components = list(dict.fromkeys(str(part.type.value).lower() for part in result.chain))
            await self.client.call('claim', {'turn_token': state.prepared['turn_token'],
                'completion_id': state.completion_id, 'final_text': visible})
            claimed = True
            # AstrBot generates the response and its adapter sends it once. We
            # own final dispatch because RespondStage swallows send exceptions.
            # Record is sent separately just as in that pinned native stage.
            records = [part for part in result.chain if isinstance(part, Record)]
            remaining = [part for part in result.chain if not isinstance(part, Record)]
            already = event.get_extra('_send_message_to_user_current_session_plain_texts', [])
            for record in records:
                await event.send(MessageChain([record]))
            if remaining and not (visible.strip() in already and all(isinstance(part, Plain) for part in remaining)):
                await event.send(MessageChain(remaining))
            state.sent = True
            # Preserve Meme pending image delivery and other native after hooks.
            await call_event_hook(event, EventType.OnAfterMessageSentEvent)
            await self.complete(state)
        except Exception as error:
            if claimed and not state.sent:
                await self.complete(state, 'failed')
            logger.warning('Youmi final dispatch stopped (%s)', error.code if isinstance(error, BridgeError) else 'delivery_uncertain')
        finally:
            # Empty result makes RespondStage skip a second send/after callback.
            event.clear_result()
            if audio_path:
                audio_path.unlink(missing_ok=True)

    @filter.after_message_sent(priority=-100000)
    async def after(self, event: AstrMessageEvent):
        state = event.get_extra('youmi_turn')
        # Hook alone cannot establish successful adapter acceptance.
        if state and state.agent_done and state.sent:
            await self.complete(state)

    async def complete(self, state, result='adapter_accepted'):
        if state.complete_ack:
            return
        body = state.completion(result)
        self.outbox.put(body)
        try:
            await self.client.call('complete', body)
            self.outbox.remove(body)
            state.complete_ack = True
            if self.config.get('diagnostics_enabled', False):
                logger.info('Youmi completion trace=%s result=%s components=%s tools=%s',
                    state.prepared['turn_key'], result, ','.join(state.components), ','.join(state.tools[:64]))
        except BridgeError as error:
            if error.status not in {503, 504}:
                self.outbox.remove(body)
            logger.warning('Youmi completion pending or rejected (%s)', error.code)

    async def retry_completions(self):
        while True:
            for body in list(self.outbox.pending()):
                try:
                    await self.client.call('complete', body)
                    self.outbox.remove(body)
                except BridgeError as error:
                    if error.status not in {503, 504}:
                        self.outbox.remove(body)
            await asyncio.sleep(5)
