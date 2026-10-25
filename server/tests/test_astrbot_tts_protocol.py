"""Real pinned provider framing/parser; dependency and WebSocket fixtures, no vendor call."""

import importlib.util
import io
import json
import os
import struct
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import wave

from server.companion.astrbot_media import synthesize


class HuoshanProtocolTests(unittest.IsolatedAsyncioTestCase):
    def protocol(self):
        # Stub only unrelated streaming queues/config logging. The complete
        # original provider file supplies its constructor, framing and parser.
        dependencies = {
            'config.logger': {'setup_logging': lambda: MagicMock()},
            'core.utils.util': {'check_model_key': lambda *args: None},
            'core.providers.tts.base': {'TTSProviderBase': type('UnusedQueueFixture', (), {'__init__': lambda self, *args: None})},
            'core.utils.tts': {'MarkdownCleaner': MagicMock(), 'convert_percentage_to_range': MagicMock()},
            'core.providers.tts.dto.dto': {'SentenceType': MagicMock(), 'ContentType': MagicMock(),
                                         'InterfaceType': SimpleNamespace(DUAL_STREAM='dual')},
        }
        modules = {}
        for name, values in dependencies.items():
            modules[name] = ModuleType(name)
            modules[name].__dict__.update(values)
        spec = importlib.util.spec_from_file_location('fixed_huoshan', os.environ['YOUMI_TTS_TEST_SOURCE'])
        module = importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules', modules):
            spec.loader.exec_module(module)
        return module

    async def exercise(self, mode):
        protocol = self.protocol()
        sent, pending = [], []
        pcm = b'\0\0' * 2400
        def field(value):
            return struct.pack('>i', len(value)) + value
        def frame(event, session=None, payload=None, audio=False):
            data = protocol.Header(message_type=protocol.AUDIO_ONLY_RESPONSE if audio else protocol.FULL_SERVER_RESPONSE,
                message_type_specific_flags=protocol.MsgTypeFlagWithEvent).as_bytes() + struct.pack('>i', event)
            if session is not None:
                data += field(session.encode())
            if payload is not None:
                data += field(payload)
            return data
        class Socket:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def send(self, data):
                event = int.from_bytes(data[4:8], 'big')
                sent.append(bytes(data))
                if event == protocol.EVENT_Start_Connection:
                    pending.append(frame(protocol.EVENT_ConnectionStarted, 'connection'))
                elif event == protocol.EVENT_StartSession:
                    size = int.from_bytes(data[8:12], 'big')
                    self.session = data[12:12 + size].decode()
                    pending.append(frame(protocol.EVENT_SessionStarted, self.session, b'{}'))
                elif event == protocol.EVENT_TaskRequest:
                    if mode == 'error':
                        pending.append(bytes([0x11, 0xf4, 0, 0]) + bytes(8))
                    else:
                        pending.append(frame(protocol.EVENT_TTSResponse, 'wrong-session' if mode == 'mismatch' else self.session,
                                             pcm, True))
                elif event == protocol.EVENT_FinishSession:
                    pending.append(frame(protocol.EVENT_SessionFinished, self.session, b'{}'))
            async def recv(self): return pending.pop(0)
        parent = ModuleType('core.providers.tts')
        parent.huoshan_double_stream = protocol
        models = {'selected_module': {'TTS': 'current'}, 'TTS': {'current': {'type': 'huoshan_double_stream',
            'speaker': 'configured-public', 'private_voice': 'configured-private', 'ws_url': 'wss://fixture.invalid',
            'appid': 'fixture', 'access_token': 'fixture', 'resource_id': 'seed-tts-2.0',
            'audio_params': {'speech_rate': 12}, 'additions': {'post_process': {'pitch': 3}}}}}
        with patch.dict('sys.modules', {'core.providers.tts': parent}), patch('websockets.connect', return_value=Socket()):
            audio = await synthesize(models, '实际要朗读的文本')
        task = next(data for data in sent if int.from_bytes(data[4:8], 'big') == protocol.EVENT_TaskRequest)
        offset = 12 + int.from_bytes(task[8:12], 'big')
        payload = json.loads(task[offset + 4:])['req_params']
        self.assertEqual(payload['speaker'], 'configured-private')
        self.assertEqual(payload['text'], '实际要朗读的文本')
        self.assertEqual(payload['audio_params'], {'speech_rate': 12, 'loudness_rate': 0, 'sample_rate': 24000, 'format': 'pcm'})
        self.assertEqual(json.loads(payload['additions'])['post_process']['pitch'], 3)
        with wave.open(io.BytesIO(audio)) as result:
            self.assertEqual(result.readframes(2400), pcm)

    async def test_existing_provider_voice_payload_frames_and_wav(self):
        await self.exercise('ok')

    async def test_upstream_error_is_not_interpreted_as_pcm(self):
        with self.assertRaisesRegex(ValueError, 'tts_protocol_error'):
            await self.exercise('error')

    async def test_cross_session_audio_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'tts_session_mismatch'):
            await self.exercise('mismatch')
