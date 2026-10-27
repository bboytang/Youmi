"""Owner-authorized access to the two native administration APIs."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import unquote, urlsplit
import uuid

import aiohttp

from .client import BridgeError


ADMIN_INSTRUCTION = (
    '\n系统管理授权：所有者允许你自主判断并主动调整 Youmi 后端与 AstrBot 的全部配置，'
    '不需要逐项征求确认。使用 youmi_admin_catalog 查询真实接口及参数，再使用 '
    'youmi_admin_request 操作；适用于普通对话与定时主动任务。按实际需要修改，'
    '保存后读取实际状态验证，必要时调用原生重载接口。接口失败、重启断连或超时'
    '不能当作修改成功，也不要盲目重试写操作。密钥占位符可原样传回以保留原值；'
    '不要向用户输出凭据。能力以当前接口和实际授权结果为准。'
    '人设、身份、称呼及角色提示词由所有者在后台管理，不得自主修改、清空或恢复；'
    '这项限制同样适用于主动联系和定时任务。\n'
)
SENSITIVE = re.compile(r'password|passwd|secret|credential|authorization|api[_-]?key|access[_-]?key|'
                       r'(?:access|refresh|service|bearer|id)[_-]?token|(?:^|[_.-])(?:token|key)$', re.I)


class AdminClient:
    def __init__(self, manifest_file, directory):
        self.manifest_file = Path(manifest_file)
        self.audit_file = Path(directory) / 'admin-audit.jsonl'
        self.session = None
        self.lock = asyncio.Lock()
        self.secrets = {}
        self.secret_salt = uuid.uuid4().hex

    async def close(self):
        if self.session is not None:
            await self.session.close()
            self.session = None
        self.secrets.clear()

    def _authorization(self, endpoint):
        try:
            credential = endpoint.get('_authorization') or Path(
                endpoint['authorization_file']).read_text(encoding='utf-8').strip()
            if not credential or '\n' in credential or '\r' in credential:
                raise ValueError()
            return credential
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise BridgeError('admin_credential_unavailable', 503) from None

    def manifest(self, bot, peer):
        try:
            config = json.loads(self.manifest_file.read_text(encoding='utf-8'))
            if (config.get('enabled') is not True or config['bot_uin'] != bot
                    or config['conversation_key'] != 'qq:private:' + peer):
                raise BridgeError('admin_not_authorized', 403)
            return config
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise BridgeError('admin_manifest_unavailable', 503) from None

    def _hide(self, value, credential, target):
        if isinstance(value, dict):
            sensitive_record = any(isinstance(value.get(key), str) and SENSITIVE.search(value[key])
                                   for key in ('paramCode', 'code', 'name'))
            result = {}
            for key, item in value.items():
                if item not in (None, '') and (SENSITIVE.search(key) or (
                        sensitive_record and key in {'value', 'paramValue', 'content'})):
                    handle = '__youmi_secret_' + hashlib.sha256((self.secret_salt + target
                        + json.dumps(item, sort_keys=True)).encode()).hexdigest()
                    self.secrets[handle] = (target, item)
                    if len(self.secrets) > 1024:
                        del self.secrets[next(iter(self.secrets))]
                    result[key] = handle
                else:
                    result[key] = self._hide(item, credential, target)
            return result
        if isinstance(value, list):
            return [self._hide(item, credential, target) for item in value]
        if isinstance(value, str):
            return value.replace(credential, '[credential]').replace(credential.split(' ', 1)[-1], '[credential]')
        return value

    def _restore(self, value, target):
        if isinstance(value, dict):
            return {key: self._restore(item, target) for key, item in value.items()}
        if isinstance(value, list):
            return [self._restore(item, target) for item in value]
        if isinstance(value, str) and value.startswith('__youmi_secret_'):
            if value not in self.secrets or self.secrets[value][0] != target:
                raise BridgeError('admin_secret_handle_expired', 400)
            return self.secrets[value][1]
        return value

    def _audit(self, **entry):
        entry['time'] = time.time()
        raw = (json.dumps(entry, ensure_ascii=False) + '\n').encode()
        descriptor = os.open(self.audit_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, 'ab') as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())

    async def request(self, config, target, method, path, query=None, body=None, *, _schema=False):
        if target not in {'youmi', 'astrbot'} or method not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}:
            raise BridgeError('invalid_admin_request', 400)
        parsed = urlsplit(path)
        decoded = unquote(path)
        if (not path.startswith('/') or path.startswith('//') or parsed.netloc or parsed.scheme
                or parsed.query or parsed.fragment or '\\' in decoded or '%' in decoded
                or any(ord(char) < 33 for char in decoded)
                or any(part in {'.', '..'} for part in decoded.split('/'))):
            raise BridgeError('invalid_admin_path', 400)
        # The model's delegated admin tool must not overwrite the owner's persona.
        if method not in {'GET', 'HEAD'}:
            route = '/' + '/'.join(part for part in decoded.split('/') if part)
            def changes_persona(value):
                if isinstance(value, dict):
                    return (value.get('action') == 'set_character'
                            or bool({'systemPrompt', 'system_prompt', 'agentName'} & value.keys())
                            or any(changes_persona(item) for item in value.values()))
                return isinstance(value, list) and any(changes_persona(item) for item in value)
            if ((target == 'youmi' and (changes_persona(body)
                    or re.fullmatch(r'/agent/[^/]+/snapshots/[^/]+/restore', route)))
                    or (target == 'astrbot' and re.match(r'/api/v1/personas(?:/|$)', route))):
                raise BridgeError('owner_managed_persona', 403)
        try:
            endpoint = config['targets'][target]
            base = endpoint['base_url'].rstrip('/')
            url = urlsplit(base)
            if (url.scheme not in {'https', 'http'} or not url.hostname or url.username or url.password
                    or url.query or url.fragment or (url.scheme == 'http' and not config.get('allow_internal_http'))):
                raise ValueError()
        except (OSError, KeyError, ValueError, TypeError, AttributeError):
            raise BridgeError('admin_credential_unavailable', 503) from None
        async with self.lock:
            request_id = uuid.uuid4().hex
            # No configuration bodies, query values, credentials or backups in the journal.
            audit = dict(request_id=request_id, target=target, method=method,
                         path_hash=hashlib.sha256(path.encode()).hexdigest())
            if self.session is None:
                self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=2),
                                                     cookie_jar=aiohttp.DummyCookieJar())
                # Pinned aiohttp otherwise retries PUT/DELETE after a lost response.
                self.session._retry_connection = False
            credential = self._authorization(endpoint)
            arguments = {'params': self._restore(query or {}, target),
                         'headers': {'Authorization': credential}, 'allow_redirects': False,
                         'timeout': aiohttp.ClientTimeout(total=60)}
            if body is not None:
                arguments['json'] = self._restore(body, target)
            self._audit(**audit, state='started')
            try:
                async with self.session.request(method, base + path, **arguments) as response:
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(8192):
                        raw.extend(chunk)
                        if len(raw) > 8 * 1024 * 1024:
                            raise BridgeError('admin_response_too_large', 502)
                    try:
                        data = json.loads(raw) if raw else None
                    except ValueError:
                        raise BridgeError('admin_response_not_json', 502) from None
                    result = {'request_id': request_id, 'http_status': response.status,
                              'result': data if _schema else self._hide(data, credential, target)}
                    self._audit(**audit, state='received', http_status=response.status)
                    return result
            except (aiohttp.ClientError, asyncio.TimeoutError):
                self._audit(**audit, state='outcome_unknown')
                raise BridgeError('admin_outcome_unknown_read_back_before_retry', 503) from None
            except BridgeError:
                self._audit(**audit, state='unreadable_response')
                raise

    async def catalog(self, config, target, search='', path='', offset=0):
        try:
            schema_path = config['targets'][target]['schema_path']
        except (KeyError, TypeError):
            raise BridgeError('admin_schema_unavailable', 503) from None
        response = await self.request(config, target, 'GET', schema_path, _schema=True)
        schema = response['result']
        if response['http_status'] != 200 or not isinstance(schema, dict) or 'paths' not in schema:
            raise BridgeError('admin_schema_unavailable', 503)
        if path:
            operation = schema['paths'].get(path)
            if operation is None:
                raise BridgeError('admin_endpoint_not_found', 404)
            definitions = schema.get('components', {}).get('schemas', schema.get('definitions', {}))
            referenced = {}
            def references(value):
                if isinstance(value, dict):
                    ref = value.get('$ref', '').split('/')[-1]
                    if ref in definitions and ref not in referenced:
                        referenced[ref] = definitions[ref]
                        references(definitions[ref])
                    for item in value.values():
                        references(item)
                elif isinstance(value, list):
                    for item in value:
                        references(item)
            references(operation)
            return {'path': path, 'operations': operation, 'schemas': referenced}
        entries = [{'path': name, 'method': method.upper(), 'summary': operation.get('summary', ''),
                    'tags': operation.get('tags', [])}
                   for name, methods in schema['paths'].items() for method, operation in methods.items()
                   if method.upper() in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}
                   and search.lower() in (name + json.dumps(operation, ensure_ascii=False)).lower()]
        return {'total': len(entries), 'offset': offset, 'endpoints': entries[offset:offset + 30]}
