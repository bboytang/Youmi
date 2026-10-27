"""Scoped, unauthenticated web retrieval through the dedicated proxy."""
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urljoin
from zoneinfo import ZoneInfo

import aiohttp
from bs4 import BeautifulSoup

PROXY = 'http://172.18.0.1:31985'
PROXY_CREDENTIALS = Path('/AstrBot/data/plugin_data/astrbot_plugin_youmi_web/proxy.json')


def checked_url(url):
    if len(url) > 2048:
        raise ValueError('url_too_long')
    u = urlsplit(url)
    try:
        port = u.port
    except ValueError:
        raise ValueError('invalid_port') from None
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
        raise ValueError('url_not_allowed')
    return u._replace(fragment='').geturl()


def extract(body):
    soup = BeautifulSoup(body, 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'svg', 'nav', 'footer']):
        tag.decompose()
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    links = [{'title': a.get_text(' ', strip=True)[:200], 'url': a.get('href', '')[:2048]} for a in soup.select('li.b_algo h2 a[href]')[:10]]
    text = ' '.join(soup.get_text(' ', strip=True).split())[:12000]
    return title[:300], text, links


class Web:
    def __init__(self, log_directory=Path('/AstrBot/data/plugin_data/astrbot_plugin_youmi_web')):
        self.log_directory = log_directory
        self.lock = asyncio.Lock()
        self.last = 0.0

    async def log(self, host, status):
        def write():
            self.log_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = self.log_directory / 'web-access.jsonl'
            line = json.dumps({'time': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'), 'host': host, 'status': status}, ensure_ascii=False) + '\n'
            with path.open('a', encoding='utf-8') as f:
                f.write(line)
        await asyncio.to_thread(write)

    async def fetch(self, url):
        url = checked_url(url)
        async with self.lock:
            credentials = json.loads(PROXY_CREDENTIALS.read_text(encoding='utf-8'))
            auth = aiohttp.BasicAuth(credentials['username'], credentials['password'])
            async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), timeout=aiohttp.ClientTimeout(total=25), headers={'User-Agent': 'Mozilla/5.0 (compatible; XiaoxiaoWeb/1.0)'}, trust_env=False) as session:
                for _ in range(4):
                    await asyncio.sleep(max(0, 2 - (time.monotonic() - self.last)))
                    self.last = time.monotonic()
                    host = urlsplit(url).hostname
                    status = 'error'
                    try:
                        async with session.get(url, proxy=PROXY, proxy_auth=auth, allow_redirects=False) as response:
                            status = str(response.status)
                            if response.status in (301, 302, 303, 307, 308):
                                url = checked_url(urljoin(url, response.headers['Location']))
                                continue
                            if response.status != 200:
                                return {'url': url, 'status': response.status, 'error': 'http_error'}
                            if 'html' not in response.headers.get('Content-Type', '').lower():
                                return {'url': url, 'status': response.status, 'error': 'unsupported_content_type'}
                            body = bytearray()
                            async for chunk in response.content.iter_chunked(65536):
                                body.extend(chunk)
                                if len(body) > 1048576:
                                    return {'url': url, 'status': response.status, 'error': 'page_too_large'}
                            title, text, links = extract(bytes(body))
                            return {'url': url, 'status': response.status, 'title': title, 'text': text, 'links': links}
                    finally:
                        await self.log(host, status)
                return {'error': 'too_many_redirects'}
