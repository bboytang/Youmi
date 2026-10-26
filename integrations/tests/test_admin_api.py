"""Real HTTP requests against isolated native-API fixtures, without production changes."""

from contextlib import asynccontextmanager
import json

from aiohttp import web
import pytest

from integrations.astrbot_plugin_youmi.admin import AdminClient
from integrations.astrbot_plugin_youmi.client import BridgeError


@asynccontextmanager
async def api(tmp_path, handler):
    app = web.Application()
    app.router.add_route('*', '/{path:.*}', handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    credential = tmp_path / 'credential'
    credential.write_text('Bearer fixture-admin-credential')
    endpoint = {'base_url': 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1]),
                'authorization_file': str(credential), 'schema_path': '/schema'}
    manifest = {'enabled': True, 'bot_uin': '12345', 'conversation_key': 'qq:private:67890',
                'allow_internal_http': True, 'targets': {'youmi': endpoint, 'astrbot': endpoint}}
    manifest_file = tmp_path / 'manifest.json'
    manifest_file.write_text(json.dumps(manifest))
    admin = AdminClient(manifest_file, tmp_path)
    try:
        yield admin, manifest
    finally:
        await admin.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_full_config_save_preserves_secrets_and_audit_has_no_values(tmp_path):
    saved = {}
    async def handler(request):
        assert request.headers['Authorization'] == 'Bearer fixture-admin-credential'
        if request.method == 'PUT':
            saved.update(await request.json())
            return web.json_response({'code': 0})
        return web.json_response({'api_key': 'provider-private', 'volume': 10, 'max_tokens': 8192,
                                  'params': [{'paramCode': 'server.secret', 'paramValue': 'server-private'}]})
    async with api(tmp_path, handler) as (admin, manifest):
        result = await admin.request(manifest, 'youmi', 'GET', '/settings')
        config = result['result']
        assert config['max_tokens'] == 8192
        assert 'provider-private' not in json.dumps(result)
        assert 'server-private' not in json.dumps(result)
        config['volume'] = 20
        await admin.request(manifest, 'youmi', 'PUT', '/settings', body=config)
        assert saved == {'api_key': 'provider-private', 'volume': 20, 'max_tokens': 8192,
                         'params': [{'paramCode': 'server.secret', 'paramValue': 'server-private'}]}
        journal = admin.audit_file.read_text()
        assert all(secret not in journal for secret in ['provider-private', 'server-private', 'fixture-admin-credential'])
        assert [json.loads(line)['state'] for line in journal.splitlines()] == ['started', 'received'] * 2
        assert admin.audit_file.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['//other.example/a', 'https://other.example', '/../settings',
    '/%2e%2e/settings', '/%252e%252e/settings', '/a\\b', '/a?token=x', '/a#b', '/a\nb'])
async def test_cannot_redirect_credentials_using_path(tmp_path, path):
    async def handler(request):
        pytest.fail('invalid path reached the server')
    async with api(tmp_path, handler) as (admin, manifest):
        with pytest.raises(BridgeError, match='invalid_admin_path'):
            await admin.request(manifest, 'astrbot', 'GET', path)


@pytest.mark.asyncio
async def test_redirects_not_followed_and_http_errors_not_reported_as_success(tmp_path):
    calls = []
    async def handler(request):
        calls.append(request.path)
        if request.path == '/redirect':
            return web.json_response({'message': 'redirect'}, status=302, headers={'Location': '/secret'})
        return web.json_response({'code': 403, 'message': 'denied'}, status=403)
    async with api(tmp_path, handler) as (admin, manifest):
        assert (await admin.request(manifest, 'youmi', 'GET', '/redirect'))['http_status'] == 302
        result = await admin.request(manifest, 'youmi', 'PUT', '/settings', body={})
        assert result['http_status'] == 403 and result['result']['code'] == 403
        assert calls == ['/redirect', '/settings']


@pytest.mark.asyncio
async def test_catalog_includes_referenced_request_schema_and_preserves_sensitive_field_definitions(tmp_path):
    schema = {'paths': {'/settings': {'put': {'summary': 'Update model', 'requestBody': {
        'content': {'application/json': {'schema': {'$ref': '#/components/schemas/Config'}}}}}},
        '/status': {'get': {'summary': 'Health'}}},
        'components': {'schemas': {'Config': {'type': 'object', 'properties': {
            'api_key': {'type': 'string'}, 'nested': {'$ref': '#/components/schemas/Nested'}}},
            'Nested': {'type': 'object', 'properties': {'again': {'$ref': '#/components/schemas/Config'}}}}}}
    async def handler(request):
        return web.json_response(schema)
    async with api(tmp_path, handler) as (admin, manifest):
        listed = await admin.catalog(manifest, 'astrbot', 'model')
        assert listed['total'] == 1 and listed['endpoints'][0]['path'] == '/settings'
        detail = await admin.catalog(manifest, 'astrbot', path='/settings')
        assert set(detail['schemas']) == {'Config', 'Nested'}
        assert detail['schemas']['Config']['properties']['api_key'] == {'type': 'string'}


@pytest.mark.asyncio
async def test_rotated_credentials_and_revoked_local_grant_take_effect(tmp_path):
    seen = []
    async def handler(request):
        seen.append(request.headers['Authorization'])
        return web.json_response({})
    async with api(tmp_path, handler) as (admin, manifest):
        assert admin.manifest('12345', '67890') == manifest
        with pytest.raises(BridgeError, match='admin_not_authorized'):
            admin.manifest('12345', '11111')
        await admin.request(manifest, 'astrbot', 'GET', '/status')
        (tmp_path / 'credential').write_text('ApiKey rotated')
        await admin.request(manifest, 'astrbot', 'GET', '/status')
        assert seen == ['Bearer fixture-admin-credential', 'ApiKey rotated']
        manifest['enabled'] = False
        admin.manifest_file.write_text(json.dumps(manifest))
        with pytest.raises(BridgeError, match='admin_not_authorized'):
            admin.manifest('12345', '67890')


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE'])
async def test_write_connection_loss_is_never_retried(tmp_path, method):
    calls = []
    async def handler(request):
        calls.append(request.path)
        request.transport.close()
        return web.Response()
    async with api(tmp_path, handler) as (admin, manifest):
        with pytest.raises(BridgeError, match='admin_outcome_unknown_read_back_before_retry'):
            await admin.request(manifest, 'astrbot', method, '/restart', body={})
        assert calls == ['/restart']
        assert json.loads(admin.audit_file.read_text().splitlines()[-1])['state'] == 'outcome_unknown'


@pytest.mark.asyncio
async def test_stale_or_cross_target_secret_handle_does_not_dispatch(tmp_path):
    calls = []
    async def handler(request):
        calls.append(request.path)
        return web.json_response({'api_key': 'private'})
    async with api(tmp_path, handler) as (admin, manifest):
        result = await admin.request(manifest, 'astrbot', 'GET', '/settings')
        with pytest.raises(BridgeError, match='admin_secret_handle_expired'):
            await admin.request(manifest, 'youmi', 'PUT', '/settings', body=result['result'])
        assert calls == ['/settings']

