"""Bounded HTTP transport; messages and non-idempotent calls are never retried."""

import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp


class BridgeError(Exception):
    def __init__(self, code, status=503):
        self.code, self.status = code, status
        super().__init__(code)


class BridgeClient:
    def __init__(self, config):
        self.config = config
        self.base = config['youmi_base_url'].rstrip('/')
        url = urlsplit(self.base)
        if (url.scheme not in {'https', 'http'} or not url.hostname or url.username or url.password
                or url.query or url.fragment or (url.scheme == 'http' and not config.get('allow_internal_http', False))):
            raise ValueError('invalid_youmi_base_url')
        self.session = None

    async def close(self):
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def call(self, action, data):
        if self.session is None:
            self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=8),
                json_serialize=lambda value: json.dumps(value, ensure_ascii=False))
        try:
            token = Path(self.config['service_credential_file']).read_text(encoding='utf-8').strip()
        except (OSError, UnicodeError):
            raise BridgeError('service_credential_unavailable') from None
        timeout = 15 if action == 'passport-next' else self.config.get('timeout_tts', 30) if action in {'tts', 'transcribe'} else self.config.get(
            'timeout_prepare' if action == 'prepare' else 'timeout_complete', 15 if action == 'prepare' else 10)
        for attempt in range(3 if action in {'prepare', 'complete'} else 1):
            try:
                async with self.session.post(self.base + '/internal/astrbot/' + action,
                    json=dict(data, protocol_version=1), headers={'Authorization': 'Bearer ' + token},
                    timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=False) as response:
                    limit = (None if action == 'prepare' else 3 * 1024 * 1024 if action == 'tts'
                             else 256 * 1024 if action in {'check', 'claim'} else 65536)
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(8192):
                        raw.extend(chunk)
                        if limit is not None and len(raw) > limit:
                            raise BridgeError('response_too_large', 400)
                    if response.status != 200:
                        try:
                            code = json.loads(raw)['error']['code']
                        except (ValueError, KeyError, TypeError):
                            code = 'bridge_request_failed'
                        # Only bounded category codes can escape; never an HTTP body.
                        if not isinstance(code, str) or not code.replace('_', '').isalnum() or len(code) > 64:
                            code = 'bridge_request_failed'
                        raise BridgeError(code, response.status)
                    if action == 'tts':
                        if response.content_type != 'audio/wav' or not raw.startswith(b'RIFF'):
                            raise BridgeError('invalid_audio', 400)
                        return bytes(raw)
                    try:
                        result = json.loads(raw)
                    except ValueError:
                        raise BridgeError('invalid_bridge_response') from None
                    if not isinstance(result, dict):
                        raise BridgeError('invalid_bridge_response')
                    return result
            except (aiohttp.ClientError, asyncio.TimeoutError):
                error = BridgeError('bridge_network_unavailable')
            except BridgeError as caught:
                if caught.status not in {503, 504}:
                    raise
                error = caught
            if attempt == (2 if action in {'prepare', 'complete'} else 0):
                raise error
            await asyncio.sleep(.2 * 2 ** attempt)
