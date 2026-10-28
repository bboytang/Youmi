"""Request a fresh browser-rendered page from the local Browserless service."""
import json
from pathlib import Path

import aiohttp

from .web import checked_url, extract

BROWSER_ENDPOINT = 'http://127.0.0.1:31988/chromium/content'
CREDENTIALS = Path('/AstrBot/data/plugin_data/astrbot_plugin_youmi_web/browserless.json')


async def render(url):
    url = checked_url(url)
    token = json.loads(CREDENTIALS.read_text(encoding='utf-8'))['token']
    params = {'token': token, 'launch': json.dumps({'args': ['--proxy-server=http://127.0.0.1:31987']})}
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), timeout=aiohttp.ClientTimeout(total=45), trust_env=False) as session:
        async with session.post(BROWSER_ENDPOINT, params=params,
                                json={'url': url, 'gotoOptions': {'waitUntil': 'domcontentloaded'}},
                                allow_redirects=False) as response:
            if response.status != 200:
                return {'url': url, 'error': 'browser_render_failed', 'status': response.status}
            content = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                content.extend(chunk)
                if len(content) > 2097152:
                    return {'url': url, 'error': 'page_too_large'}
            title, text, links = extract(bytes(content))
            return {'url': url, 'status': response.status, 'title': title, 'text': text, 'links': links}
