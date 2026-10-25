"""Actual installed Doubao framing/parser with WS fixtures; no provider network."""
import base64
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import wave

from server.companion.astrbot_media import asr_pcm, pcm_wav, transcribe


class ASRProtocolTests(unittest.IsolatedAsyncioTestCase):
    def protocol(self):
        path = Path(os.environ['YOUMI_ASR_TEST_SOURCE'])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
            'ba5bfb455ecf961800ca8b66035bd8fd0f3e1b6e29e23b6732b53456e0d25dd0')
        modules = {}
        for name, fields in {
            'config.logger': {'setup_logging': lambda: MagicMock()},
            'core.providers.asr.base': {'ASRProviderBase': type('UnneededStreamQueue', (), {})},
            'core.providers.asr.dto.dto': {'InterfaceType': SimpleNamespace(STREAM='stream')},
        }.items():
            modules[name] = ModuleType(name)
            modules[name].__dict__.update(fields)
        spec = importlib.util.spec_from_file_location('fixed_doubao_asr', path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules', modules):
            spec.loader.exec_module(module)
        return module

    async def exercise(self, mode='ok'):
        protocol = self.protocol()
        frames, replies = [], []
        def reply(payload, final=False):
            raw = json.dumps(payload).encode()
            return bytes([0x11, 0x93 if final else 0x91, 0x10, 0]) + (1).to_bytes(4, 'big') + len(raw).to_bytes(4, 'big') + raw
        class Socket:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def send(self, data):
                frames.append(data)
                if data[1] >> 4 == 1:
                    replies.append(reply({}))
                elif data[1] & 2:
                    replies.append(bytes([0x11, 0xf0, 0, 0]) + bytes(8) if mode == 'error' else
                        reply({'result': {'text': '' if mode == 'empty' else '现在几点了？'}}, True))
            async def recv(self): return replies.pop(0)
        module = ModuleType('core.providers.asr.doubao_stream')
        module.ASRProvider = protocol.ASRProvider
        cfg = {'selected_module': {'ASR': 'existing'}, 'ASR': {'existing': {'type': 'doubao_stream',
            'appid': 'fixture-app', 'access_token': 'fixture-only', 'resource_id': 'existing-resource'}}}
        encoded = base64.b64encode(pcm_wav(bytes(48000))).decode()
        with patch.dict('sys.modules', {'core.providers.asr.doubao_stream': module}), \
                patch('websockets.connect', return_value=Socket()) as connect:
            text = await transcribe(cfg, encoded)
        self.assertEqual(text, '现在几点了？')
        request = json.loads(gzip.decompress(frames[0][8:]))
        self.assertEqual(request['audio']['rate'], 16000)
        self.assertEqual(connect.call_args.kwargs['additional_headers']['X-Api-Resource-Id'], 'existing-resource')
        pcm = b''.join(gzip.decompress(f[8:]) for f in frames[1:])
        self.assertAlmostEqual(len(pcm), 32000, delta=2)
        self.assertEqual(len(gzip.decompress(frames[-1][8:])), 0)

    async def test_actual_provider_frames_auth_resampling_and_final_text(self):
        await self.exercise()

    async def test_provider_failure_is_not_user_evidence(self):
        with self.assertRaisesRegex(ValueError, 'stt_protocol_error'):
            await self.exercise('error')

    async def test_empty_transcription_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'stt_empty_result'):
            await self.exercise('empty')

    async def test_bad_base64_truncated_and_oversized_audio_are_rejected(self):
        good = pcm_wav(bytes(48000))
        for data in ['!', base64.b64encode(b'not-a-wave').decode(), base64.b64encode(good[:-4]).decode(), 'a' * (4 * 1024 * 1024 + 1),
                     base64.b64encode(pcm_wav(bytes(24000 * 2 * 31))).decode()]:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                asr_pcm(data)
