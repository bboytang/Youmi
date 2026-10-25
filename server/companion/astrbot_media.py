"""Collect the existing Huoshan provider's PCM into bounded OneBot WAV messages."""

import io
import base64
import gzip
import json
import uuid
import wave


SAMPLE_RATE = 24000
MAX_AUDIO_BYTES = SAMPLE_RATE * 2 * 60


def asr_pcm(encoded):
    if not isinstance(encoded, str) or not 0 < len(encoded) <= 4 * 1024 * 1024:
        raise ValueError('invalid_audio')
    audio = base64.b64decode(encoded, validate=True)
    if len(audio) > 3 * 1024 * 1024:
        raise ValueError('audio_too_large')
    try:
        with wave.open(io.BytesIO(audio), 'rb') as source:
            rate, frames = source.getframerate(), source.getnframes()
            if (source.getnchannels() != 1 or source.getsampwidth() != 2
                    or rate not in {8000, 16000, 24000, 48000} or not 0 < frames <= rate * 30):
                raise ValueError('unsupported_audio')
            pcm = source.readframes(frames)
            if len(pcm) != frames * 2:
                raise ValueError('truncated_audio')
    except (wave.Error, EOFError):
        raise ValueError('invalid_audio') from None
    if rate != 16000:
        import audioop
        pcm, _ = audioop.ratecv(pcm, 2, 1, rate, 16000, None)
    return pcm


async def transcribe(config, encoded):
    pcm = asr_pcm(encoded)
    current = config['ASR'][config['selected_module']['ASR']]
    if current.get('type') != 'doubao_stream':
        raise ValueError('stt_adapter_not_supported')
    import websockets
    from core.providers.asr.doubao_stream import ASRProvider
    provider = ASRProvider(current, True)
    # Same installed provider fields without construct_request's credential log.
    request = {'app': {'appid': provider.appid, 'token': provider.access_token},
        'user': {'uid': provider.uid}, 'audio': {'format': 'pcm', 'codec': 'pcm',
        'rate': 16000, 'bits': 16, 'channel': 1, 'sample_rate': 16000},
        'request': {'reqid': str(uuid.uuid4()), 'workflow': provider.workflow,
        'show_utterances': True, 'result_type': provider.result_type, 'sequence': 1,
        'end_window_size': provider.end_window_size, 'corpus': {
        'boosting_table_name': provider.boosting_table_name, 'correct_table_name': provider.correct_table_name}}}
    if provider.enable_multilingual and provider.language:
        request['audio']['language'] = provider.language
    if not isinstance(provider.ws_url, str) or not provider.ws_url.startswith('wss://'):
        raise ValueError('stt_tls_required')
    async with websockets.connect(provider.ws_url, additional_headers=provider.token_auth(),
            open_timeout=5, close_timeout=2, max_size=65536) as ws:
        async def send(header, payload):
            payload = gzip.compress(payload)
            await ws.send(header + len(payload).to_bytes(4, 'big') + payload)

        async def receive():
            raw = await ws.recv()
            if not isinstance(raw, bytes) or len(raw) < 8 or raw[1] >> 4 == 15:
                raise ValueError('stt_protocol_error')
            result = provider.parse_response(raw)
            if result.get('code') not in {None, 1000}:
                raise ValueError('stt_provider_error')
            return raw, result.get('payload_msg', {})

        await send(provider.generate_header(), json.dumps(request).encode())
        await receive()
        for offset in range(0, len(pcm), 3200):
            await send(provider.generate_audio_default_header(), pcm[offset:offset + 3200])
        await send(provider.generate_last_audio_default_header(), b'')
        text = ''
        for _ in range(128):
            raw, result = await receive()
            text = result.get('result', {}).get('text', '') or text
            if raw[1] & 2:
                if not isinstance(text, str) or not 0 < len(text.strip()) <= 4096:
                    raise ValueError('stt_empty_result')
                return text.strip()
        raise ValueError('stt_result_limit')


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
