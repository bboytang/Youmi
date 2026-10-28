"""AstrBot event for one authenticated, live Passport voice turn."""

import asyncio
import re

from astrbot.api import logger
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata


class PassportEvent(AstrMessageEvent):
    def __init__(self, event, handle, client):
        message = AstrBotMessage()
        message.type = MessageType.FRIEND_MESSAGE
        message.self_id = 'youmi-passport'
        message.session_id = event['session_id']
        message.message_id = event['turn_id']
        message.sender = MessageMember(event['session_id'], 'Passport')
        message.message_str = event['text']
        message.message = [Plain(event['text'])]
        # Never expose the ticket or service credential as message content.
        message.raw_message = None
        super().__init__(message.message_str, message,
                         PlatformMetadata('youmi_passport', 'Youmi Passport', 'youmi_passport',
                                          support_streaming_message=False, support_proactive_message=False),
                         message.session_id)
        self.client, self.handle = client, handle
        # The device has already explicitly started this private voice turn.
        self.is_at_or_wake_command = True

    async def send(self, message: MessageChain):
        state = self.get_extra('youmi_turn')
        if not state or not state.agent_done or not state.final_text or not message:
            raise RuntimeError('passport_reply_not_claimed')
        text = ''.join(part.text for part in message.chain if isinstance(part, Plain))
        if text != state.final_text or any(not isinstance(part, Plain) for part in message.chain):
            raise RuntimeError('passport_reply_mismatch')
        await self.client.call('passport-reply', {'handle': self.handle,
            'turn_token': state.prepared['turn_token'], 'completion_id': state.completion_id, 'text': text})


async def poll_passport(context, client):
    while True:
        handle = None
        try:
            response = await client.call('passport-next', {})
            event = response.get('event')
            if event is None:
                continue
            if (not isinstance(event, dict) or set(event) != {'ticket', 'session_id', 'turn_id', 'text'}
                    or not all(isinstance(event[key], str) for key in event)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{43}', event['ticket'])
                    or not all(re.fullmatch(r'[A-Za-z0-9_:.-]{1,128}', event[key]) for key in ('session_id', 'turn_id'))
                    or not event['text'].strip() or len(event['text']) > 4096):
                raise ValueError('invalid_passport_event')
            opened = await client.call('passport-open', {'ticket': event['ticket']})
            handle = opened.get('handle')
            if (not isinstance(handle, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', handle)
                    or opened.get('session_id') != event['session_id'] or opened.get('turn_id') != event['turn_id']):
                raise ValueError('invalid_passport_claim')
            item = PassportEvent(event, handle, client)
            context.get_event_queue().put_nowait(item)
            handle = None  # Ownership transfers to the queued event.
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning('Youmi Passport event handoff unavailable')
            await asyncio.sleep(5)
        finally:
            if handle:
                try:
                    await client.call('passport-close', {'handle': handle})
                except Exception:
                    pass
