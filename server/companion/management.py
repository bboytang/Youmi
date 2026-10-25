"""Console proxy on the existing HTTP server; the manager proves every owner."""

import asyncio
import hmac
import json

from aiohttp import web

from .authorization import PrivateAuthorization, PrivateAuthorizationRequired
from .features import status, update
from .memory import MemoryError


class ManagementHandler:
    def __init__(self, config, *, store=None, authorization=None):
        self.config, self.store, self.authorization = config, store, authorization

    async def handle(self, request):
        secret = self.config.get('manager-api', {}).get('secret', '')
        header = request.headers.get('Authorization', '')
        if not isinstance(secret, str) or not secret or not hmac.compare_digest(header, 'Bearer ' + secret):
            raise web.HTTPUnauthorized()
        raw = bytearray()
        async for chunk in request.content.iter_chunked(4096):
            raw.extend(chunk)
        authorization = self.authorization or PrivateAuthorization(self.config)
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or set(data) != {'operator_id', 'agent_id', 'request'}:
                raise ValueError()
            proof = {'operator_id': data['operator_id'], 'agent_id': data['agent_id']}
            grant = await authorization._request('/config/youmi-management/verify', proof)
            if not isinstance(grant, dict) or not isinstance(grant.get('owner'), str):
                raise PrivateAuthorizationRequired()
            owner = grant['owner']
            command = data['request']
            if not isinstance(command, dict):
                raise ValueError()
            action = command.get('action')
            if action == 'reset_mia_emotion':
                action = 'reset_youmi_emotion'  # Historical clients retain their reset operation.
            if action not in {'status', 'pause', 'set_feature_settings', 'emotion_consent', 'reset_youmi_emotion', 'memory_list', 'memory_delete', 'set_character', 'affect_system'}:
                raise ValueError()
            if self.store is None:
                from .youmi_adapter import _store
                store = _store()
            else:
                store = self.store
            def apply():
                if action == 'affect_system':
                    from .emotional_system import manage
                    operation = command.get('operation')
                    if not isinstance(operation, dict):
                        raise ValueError()
                    return manage(store, owner, operation)
                if action in {'memory_list', 'memory_delete'}:
                    offset = command.get('offset', 0)
                    if type(offset) is not int or not 0 <= offset <= 2147483647:
                        raise ValueError()
                    if action == 'memory_delete':
                        store.delete_memory(owner, command.get('id'))
                    with store.lock:
                        total = store.db.execute('SELECT COUNT(*) FROM memories WHERE owner=?', (owner,)).fetchone()[0]
                        records = store._records(store.db.execute(
                            'SELECT * FROM memories WHERE owner=? ORDER BY updated_at DESC,rowid DESC LIMIT 50 OFFSET ?',
                            (owner, offset)).fetchall())
                    return {'memories': records, 'total': total, 'offset': offset, 'page_size': 50}
                if action == 'set_character':
                    from .character import update as update_character
                    update_character(store, owner, command)
                elif action == 'reset_youmi_emotion':
                    from .affect import reset
                    reset(store, owner)
                elif action == 'set_feature_settings':
                    update(store, owner, command.get('features'))
                elif action != 'status':
                    store.handle(owner, command)
                return status(store, owner)
            result = await asyncio.to_thread(apply)
            if await authorization._request('/config/youmi-management/verify', proof) != grant:
                raise PrivateAuthorizationRequired()
            if request.path.startswith('/youmi/') and isinstance(result, dict):
                result.pop('mia_emotion', None)
            return web.json_response({'code': 0, 'data': result})
        except (PrivateAuthorizationRequired,):
            raise web.HTTPForbidden() from None
        except (ValueError, TypeError, KeyError, MemoryError):
            raise web.HTTPBadRequest() from None
        finally:
            if self.authorization is None:
                await authorization.close()
