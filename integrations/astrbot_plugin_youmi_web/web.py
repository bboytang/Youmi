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
DEFAULT_FIELDS = ('title', 'DOI', 'author', 'published', 'abstract', 'given', 'family', 'name')
MAX_BODY = 1048576


def validate_options(fields, max_items, max_chars):
    if fields is not None and (not isinstance(fields, list) or len(fields) > 20 or
                               any(not isinstance(field, str) for field in fields)):
        raise ValueError('invalid_fields')
    if type(max_items) is not int or not 1 <= max_items <= 20:
        raise ValueError('invalid_max_items')
    if type(max_chars) is not int or not 1 <= max_chars <= 12000:
        raise ValueError('invalid_max_chars')


def summarize_json(text, fields, max_items, max_chars):
    try:
        data = json.loads(text)
        arrays = []
        array_count = 0
        removed_fields = 0
        changed = False

        def limit_arrays(value, count):
            if isinstance(value, list):
                return [limit_arrays(v, count) for v in value[:count]]
            if isinstance(value, dict):
                return {k: limit_arrays(v, count) for k, v in value.items()}
            return value

        count = max_items
        preliminary = limit_arrays(data, count)
        if len(json.dumps(preliminary, ensure_ascii=False, separators=(',', ':'))) > max_chars:
            count = 1

        def trim(value, path, item=False):
            nonlocal removed_fields, changed, array_count
            if isinstance(value, list):
                kept = value[:count]
                if len(value) > len(kept):
                    changed = True
                array_count += 1
                if len(arrays) < 20:
                    arrays.append({'path': path[:80], 'total': len(value), 'kept': len(kept),
                                   'removed_fields_per_item': [len(v) - sum(k in fields for k in v) if isinstance(v, dict) else 0 for v in kept]})
                return [trim(v, path + '[]', True) for v in kept]
            if isinstance(value, dict):
                keys = list(value)
                if item:
                    keys = [key for key in keys if key in fields]
                    removed_fields += len(value) - len(keys)
                    changed |= len(value) != len(keys)
                return {key: trim(value[key], path + '.' + key) for key in keys}
            return value

        result = trim(data, '$')

        def shorten(value, limit, key=''):
            nonlocal changed
            if isinstance(value, dict):
                return {k: shorten(v, limit, k) for k, v in value.items()}
            if isinstance(value, list):
                return [shorten(v, limit, key) for v in value]
            if isinstance(value, str):
                cap = min(limit, 400) if key.lower() == 'abstract' else limit
                if len(value) > cap:
                    changed = True
                    return value[:cap]
            return value

        result = shorten(result, max_chars)
        encoded = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
        if len(encoded) > max_chars:
            # Shrink long strings before resorting to a non-JSON prefix.
            result = shorten(result, max(1, max_chars // 20))
            encoded = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
        shape = {'fields': [key[:80] for key in list(data)[:20]] if isinstance(data, dict) else [],
                 'fields_omitted': max(0, len(data) - 20) if isinstance(data, dict) else 0,
                 'arrays': arrays, 'arrays_omitted': array_count - len(arrays), 'removed_fields': removed_fields}
        if isinstance(data, dict):
            total = data.get('total-results')
            if total is None and isinstance(data.get('message'), dict):
                total = data['message'].get('total-results')
            if isinstance(total, (int, float)) and not isinstance(total, bool):
                shape['total_results'] = total
        return encoded[:max_chars], changed or len(encoded) > max_chars, shape
    except (ValueError, RecursionError):
        return text[:max_chars], True, 'unknown'


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

    async def fetch(self, url, fields=None, max_items=3, max_chars=12000):
        validate_options(fields, max_items, max_chars)
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
                            content_type = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
                            if content_type == 'application/json' or content_type.endswith('+json'):
                                body = bytearray()
                                while len(body) <= MAX_BODY:
                                    chunk = await response.content.read(min(65536, MAX_BODY + 1 - len(body)))
                                    if not chunk:
                                        break
                                    body.extend(chunk)
                                incomplete = len(body) > MAX_BODY
                                text = bytes(body[:MAX_BODY]).decode(response.charset or 'utf-8', errors='replace')
                                result = {'url': url, 'status': response.status, 'title': '', 'links': []}
                                if not incomplete and len(text) <= max_chars:
                                    result.update(text=text, truncated=False)
                                elif incomplete:
                                    result.update(text=text[:max_chars], truncated=True, shape='unknown')
                                else:
                                    text, truncated, shape = summarize_json(text, fields if fields is not None else DEFAULT_FIELDS, max_items, max_chars)
                                    result.update(text=text, truncated=truncated, shape=shape)
                                return result
                            if 'html' not in content_type:
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
