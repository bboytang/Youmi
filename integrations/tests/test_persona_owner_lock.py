"""Model delegation cannot overwrite owner-authored identity via native API aliases."""
from contextlib import asynccontextmanager
import json
from aiohttp import web
import pytest
from integrations.astrbot_plugin_youmi.admin import AdminClient
from integrations.astrbot_plugin_youmi.client import BridgeError

@asynccontextmanager
async def fixture(tmp_path):
    calls = []
    async def handler(request):
        calls.append((request.method, request.path))
        return web.json_response({'code': 0})
    app = web.Application()
    app.router.add_route('*', '/{path:.*}', handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    credential = tmp_path / 'credential'
    credential.write_text('Bearer fixture-only')
    endpoint = {'base_url': 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1]),
                'authorization_file': str(credential)}
    config = {'allow_internal_http': True, 'targets': {'youmi': endpoint, 'astrbot': endpoint}}
    client = AdminClient(tmp_path / 'unused-manifest', tmp_path)
    try:
        yield client, config, calls
    finally:
        await client.close()
        await runner.cleanup()

@pytest.mark.asyncio
@pytest.mark.parametrize('target,method,path,body', [
    ('youmi', 'POST', '/agent/a/youmi-settings', {'action': 'set_character', 'values': {}}),
    ('youmi', 'POST', '/agent/a/mia-settings', {'action': 'set_character', 'values': {}}),
    ('youmi', 'POST', '/agent/a/youmi-settings/', {'action': 'set_character'}),
    ('youmi', 'PUT', '/agent/a', {'systemPrompt': 'old persona'}),
    ('youmi', 'PUT', '/agent/a', {'agentName': 'old name'}),
    ('youmi', 'PATCH', '/agent/a', {'system_prompt': ''}),
    ('youmi', 'POST', '/batch', [{'request': {'action': 'set_character'}}]),
    ('youmi', 'POST', '/agent/a/snapshots/s/restore', {}),
    ('youmi', 'POST', '/agent//a/snapshots/s/restore/', {}),
    ('astrbot', 'PUT', '/api/v1/personas/default', {'system_prompt': 'old'}),
    ('astrbot', 'DELETE', '/api/v1/personas/default', None),
    ('astrbot', 'POST', '/api/v1/personas', {'system_prompt': 'old'}),
])
async def test_persona_writes_are_rejected_before_any_http_request(tmp_path, target, method, path, body):
    async with fixture(tmp_path) as (client, config, calls):
        with pytest.raises(BridgeError, match='owner_managed_persona') as error:
            await client.request(config, target, method, path, body=body)
        assert error.value.status == 403
        assert calls == []
        assert not client.audit_file.exists()

@pytest.mark.asyncio
@pytest.mark.parametrize('target,method,path,body', [
    ('youmi', 'POST', '/agent/a/youmi-settings', {'action': 'status'}),
    ('youmi', 'POST', '/agent/a/youmi-settings', {'action': 'set_feature_settings', 'features': {}}),
    ('youmi', 'PUT', '/agent/a', {'ttsRate': 1}),
    ('youmi', 'GET', '/agent/a', None),
    ('astrbot', 'GET', '/api/v1/personas/default', None),
    ('astrbot', 'PATCH', '/api/v1/cron/jobs/job', {'enabled': False}),
])
async def test_reads_and_unrelated_management_still_work(tmp_path, target, method, path, body):
    async with fixture(tmp_path) as (client, config, calls):
        result = await client.request(config, target, method, path, body=body)
        assert result['http_status'] == 200
        assert calls == [(method, path)]
