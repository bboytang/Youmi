"""Opt-in device voice handoff; the normal device path remains the default."""

import asyncio
import json
import os

from .authorization import AuthenticatedScope, PrivateAuthorizationRequired
from .passport_dispatch import passport_dispatch
from .passport_tickets import PassportTickets, PassportTurn
from .persona import PersonaAgent, SAFE_REPLY


def enabled(connection):
    scope = getattr(connection, '_youmi_scope', None)
    try:
        devices = json.loads(os.environ.get('YOUMI_PASSPORT_ASTRBOT_DEVICES', '[]'))
    except (ValueError, TypeError):
        return False
    return (isinstance(scope, AuthenticatedScope) and not scope.device_id.startswith('astrbot-')
            and isinstance(devices, list) and all(isinstance(device, str) for device in devices)
            and scope.device_id in devices)


def utterance(query):
    if not isinstance(query, str):
        raise ValueError('invalid_passport_text')
    try:
        data = json.loads(query)
        if isinstance(data, dict) and isinstance(data.get('speaker'), str) and isinstance(data.get('content'), str):
            query = data['content']
    except ValueError:
        pass
    if not query.strip() or len(query) > 4096:
        raise ValueError('invalid_passport_text')
    return query


async def await_reply(connection, query, *, turn=None, dispatcher=None):
    turn = turn or PassportTurn(connection, connection._youmi_scope, connection._youmi_authorization,
                                connection.session_id, connection.sentence_id)
    PassportTickets.check_connection(turn)
    query = utterance(query)
    guard = PersonaAgent()
    guard.begin_turn(query)
    if guard.input_decision.blocked:
        await PassportTickets.require_live(turn)
        return turn, SAFE_REPLY
    waiter = await (dispatcher or passport_dispatch).submit(connection, query)
    try:
        while True:
            done, _ = await asyncio.wait({waiter}, timeout=0.25)
            PassportTickets.check_connection(turn)
            if done:
                if waiter.cancelled():
                    raise TimeoutError('passport_reply_timeout')
                text = waiter.result()
                await PassportTickets.require_live(turn)
                return turn, text
    finally:
        if not waiter.done():
            waiter.cancel()


def chat(connection, query):
    """Run in the upstream chat executor; never wait on the event-loop thread."""
    from concurrent.futures import TimeoutError as FutureTimeout
    from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType
    sentence = connection.sentence_id
    turn = PassportTurn(connection, getattr(connection, '_youmi_scope', None),
                        getattr(connection, '_youmi_authorization', None), connection.session_id, sentence)
    future = asyncio.run_coroutine_threadsafe(await_reply(connection, query, turn=turn), connection.loop)
    try:
        _, text = future.result(timeout=125)
    except (FutureTimeout, TimeoutError, ValueError, RuntimeError):
        future.cancel()
        text = '语音助手暂时连接不上，请稍后再试。'
    except PrivateAuthorizationRequired:
        return
    except Exception:
        future.cancel()
        text = '语音助手暂时连接不上，请稍后再试。'
    try:
        asyncio.run_coroutine_threadsafe(PassportTickets.require_live(turn), connection.loop).result(timeout=4)
        text = PersonaAgent().checked_text(text)
        connection.tts.tts_text_queue.put(TTSMessageDTO(sentence, SentenceType.FIRST, ContentType.ACTION))
        connection.tts.tts_text_queue.put(TTSMessageDTO(sentence, SentenceType.MIDDLE, ContentType.TEXT, text))
        connection.tts.store_tts_text(sentence, text)
        connection.tts.tts_text_queue.put(TTSMessageDTO(sentence, SentenceType.LAST, ContentType.ACTION))
    except Exception:
        return
