"""Collect the existing Huoshan provider's PCM into bounded OneBot WAV messages."""

import asyncio
import io
import uuid
import wave


SAMPLE_RATE = 24000
MAX_AUDIO_BYTES = SAMPLE_RATE * 2 * 60


def pcm_wav(pcm):
    if not isinstance(pcm, bytes) or not pcm or len(pcm) > MAX_AUDIO_BYTES or len(pcm) % 2:
        raise ValueError('invalid_audio')
    target = io.BytesIO()
    with wave.open(target, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm)
    return target.getvalue()


async def synthesize(models, text):
    selected = models['selected_module']['TTS']
    config = models['TTS'][selected]
    if config.get('type', selected) != 'huoshan_double_stream':
        raise ValueError('tts_adapter_not_supported')
    # Reuse the installed provider's configuration, voice selection, payload and
    # parser. No fake ConnectionHandler, monitor, queue, speaker constant or SDK.
    import websockets
    from core.providers.tts import huoshan_double_stream as protocol
    provider = protocol.TTSProvider(config, True)
    provider.audio_params['sample_rate'] = SAMPLE_RATE
    session = uuid.uuid4().hex
    header = protocol.Header(message_type=protocol.FULL_CLIENT_REQUEST,
        message_type_specific_flags=protocol.MsgTypeFlagWithEvent, serial_method=protocol.JSON).as_bytes()
    headers = {'X-Api-App-Key': provider.appId, 'X-Api-Access-Key': provider.access_token,
               'X-Api-Resource-Id': provider.resource_id, 'X-Api-Connect-Id': str(uuid.uuid4())}
    if not isinstance(provider.ws_url, str) or not provider.ws_url.startswith('wss://'):
        raise ValueError('tts_tls_required')
    async with websockets.connect(provider.ws_url, additional_headers=headers, max_size=MAX_AUDIO_BYTES,
                                  open_timeout=5, close_timeout=2) as ws:
        async def send(event, *, payload=b'{}', scoped=True):
            optional = protocol.Optional(event=event, sessionId=session if scoped else None).as_bytes()
            await provider.send_event(ws, header, optional, payload)

        async def receive():
            raw = await ws.recv()
            if not isinstance(raw, bytes) or len(raw) < 8 or raw[1] >> 4 == protocol.ERROR_INFORMATION:
                raise ValueError('tts_protocol_error')
            result = provider.parser_response(raw)
            if result.optional.event in {protocol.EVENT_ConnectionFailed, protocol.EVENT_SessionFailed,
                                          protocol.EVENT_SessionCanceled}:
                raise ValueError('tts_session_failed')
            if result.optional.sessionId and result.optional.sessionId != session:
                raise ValueError('tts_session_mismatch')
            return result

        await send(protocol.EVENT_Start_Connection, scoped=False)
        if (await receive()).optional.event != protocol.EVENT_ConnectionStarted:
            raise ValueError('tts_connection_not_started')
        await send(protocol.EVENT_StartSession, payload=provider.get_payload_bytes(
            event=protocol.EVENT_StartSession, speaker=provider.voice))
        if (await receive()).optional.event != protocol.EVENT_SessionStarted:
            raise ValueError('tts_session_not_started')
        await send(protocol.EVENT_TaskRequest, payload=provider.get_payload_bytes(
            event=protocol.EVENT_TaskRequest, text=text, speaker=provider.voice))
        await send(protocol.EVENT_FinishSession)
        pcm = bytearray()
        while True:
            result = await receive()
            if result.optional.event == protocol.EVENT_TTSResponse:
                if result.header.message_type != protocol.AUDIO_ONLY_RESPONSE or not isinstance(result.payload, bytes):
                    raise ValueError('tts_invalid_pcm')
                pcm.extend(result.payload)
                if len(pcm) > MAX_AUDIO_BYTES:
                    raise ValueError('tts_audio_too_large')
            elif result.optional.event == protocol.EVENT_SessionFinished:
                break
        await send(protocol.EVENT_FinishConnection, scoped=False)
    return pcm_wav(bytes(pcm))
