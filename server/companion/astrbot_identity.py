"""Server-owned QQ mapping to independent manager device grants, never QQ IDs as proof."""

import hashlib
import json
from pathlib import Path
import re

from .authorization import PrivateAuthorizationRequired


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identifier(value, maximum=128):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_:.-]{1,' + str(maximum) + '}', value):
        raise ValueError('invalid_identifier')
    return value


def secret_file(path):
    value = Path(path).read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', value):
        raise ValueError('invalid_secret_file')
    return value


class BridgeIdentity:
    def __init__(self, authorization, bindings):
        self.authorization, self.bindings = authorization, bindings

    async def resolve(self, data):
        bot, peer = data['bot_uin'], data['sender_uin']
        if (not isinstance(bot, str) or not re.fullmatch(r'[1-9][0-9]{4,19}', bot)
                or not isinstance(peer, str) or not re.fullmatch(r'[1-9][0-9]{4,19}', peer)
                or data['conversation_key'] != 'qq:private:' + peer):
            raise ValueError('invalid_qq_source')
        binding = next((item for item in self.bindings if item['bot_uin'] == bot
                        and item['conversation_key'] == data['conversation_key']), None)
        if binding is None:
            raise PrivateAuthorizationRequired()
        device, client = binding['device_id'], binding['client_id']
        if not device.startswith('astrbot-') or client != 'astrbot:' + bot:
            raise PrivateAuthorizationRequired()
        scope = await self.authorization.verify(device, client, secret_file(binding['credential_file']))
        await self.authorization.require(scope, 'memory:read')
        return scope

    async def config(self, scope):
        await self.authorization.require(scope, 'memory:read')
        from .authorization import _manager_scope
        models = await self.authorization._request('/config/mia-auth/bridge-config', scope.snapshot())
        if not isinstance(models, dict) or _manager_scope(models.get('mia_scope')) != scope:
            raise PrivateAuthorizationRequired()
        return models
