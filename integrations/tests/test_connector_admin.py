"""Exercise administration through the actual Connector and native event classes."""

import json
from unittest.mock import AsyncMock

import pytest

from integrations.astrbot_plugin_youmi.client import BridgeError
from integrations.tests.test_astrbot_compatibility import setup, close_native_database_pool


def enable(connector, tmp_path):
    manifest = tmp_path / 'admin.json'
    manifest.write_text(json.dumps({'enabled': True, 'bot_uin': '12345',
        'conversation_key': 'qq:private:67890', 'targets': {'youmi': {'bridge_session': True}}}))
    connector.config['admin_manifest_file'] = str(manifest)
    return connector._admin_client()


@pytest.mark.asyncio
@pytest.mark.parametrize('scheduled', [False, True])
async def test_owner_and_scheduled_agent_get_native_session_before_admin_write(setup, tmp_path, scheduled):
    connector, meme, event, bot, sent = setup
    admin = enable(connector, tmp_path)
    if scheduled:
        event.get_platform_name = lambda: 'cron'
    connector.client.call = AsyncMock(return_value={'token': 'native-session', 'expires_in': 43200})
    admin.request = AsyncMock(return_value={'http_status': 200, 'result': {'code': 0}})
    result = await connector.admin_request(event, 'youmi', 'PUT', '/settings', '{}', '{"volume":20}')
    assert json.loads(result)['http_status'] == 200
    assert connector.client.call.call_args.args == ('admin-session', {
        'bot_uin': '12345', 'sender_uin': '67890', 'conversation_key': 'qq:private:67890'})
    assert admin.request.call_args.args[0]['targets']['youmi']['_authorization'] == 'Bearer native-session'
    assert '自主判断' in connector._admin_instruction(event)
    assert not sent


@pytest.mark.asyncio
async def test_revoked_manager_scope_cannot_execute_admin_tool(setup, tmp_path):
    connector, meme, event, bot, sent = setup
    admin = enable(connector, tmp_path)
    connector.client.call = AsyncMock(side_effect=BridgeError('authorization_required', 403))
    admin.request = AsyncMock()
    result = await connector.admin_request(event, 'youmi', 'PUT', '/settings')
    assert json.loads(result)['error'] == 'authorization_required'
    admin.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_qq_identity_cannot_use_global_administration(setup, tmp_path):
    connector, meme, event, bot, sent = setup
    admin = enable(connector, tmp_path)
    event.get_sender_id = lambda: '11111'
    connector.client.call = AsyncMock()
    admin.request = AsyncMock()
    result = await connector.admin_request(event, 'youmi', 'GET', '/settings')
    assert json.loads(result)['error'] == 'admin_not_authorized'
    connector.client.call.assert_not_awaited()
    assert connector._admin_instruction(event) == ''


@pytest.mark.asyncio
async def test_explicit_astrbot_delegation_issues_native_token_without_interactive_login(setup, tmp_path):
    import jwt
    connector, meme, event, bot, sent = setup
    admin = enable(connector, tmp_path)
    config = json.loads(admin.manifest_file.read_text())
    config['targets']['astrbot'] = {'native_delegation': True}
    admin.manifest_file.write_text(json.dumps(config))
    connector.context.get_config = lambda: {'dashboard': {'username': 'fixture-admin', 'jwt_secret': 'x' * 64}}
    connector.client.call = AsyncMock(return_value={'token': 'native-manager-session', 'expires_in': 43200})
    client, manifest = await connector._admin_access(event)
    token = manifest['targets']['astrbot']['_authorization'].removeprefix('Bearer ')
    payload = jwt.decode(token, 'x' * 64, algorithms=['HS256'])
    assert payload['username'] == 'fixture-admin'
    assert payload['auth_source'] == 'youmi_delegation'
    assert payload['exp'] > 0
    connector.client.call.assert_awaited_once()
