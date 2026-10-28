import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from server.companion.authorization import PrivateAuthorizationRequired
from server.companion.management import ManagementHandler
from server.companion.notebook_management import read_notebook


class NotebookManagementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        credential = root / 'credential'
        credential.write_text('a' * 43)
        config = root / 'bridge.json'
        config.write_text(json.dumps({'bindings': [{'bot_uin': '12345', 'conversation_key': 'qq:private:67890',
            'device_id': 'astrbot-test', 'client_id': 'astrbot:12345', 'credential_file': str(credential)}]}))
        db = root / 'notebook.sqlite3'
        with sqlite3.connect(db) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('CREATE TABLE entries (id INTEGER, scope TEXT, kind TEXT, title TEXT, content TEXT, created_at TEXT, updated_at TEXT)')
            conn.executemany('INSERT INTO entries VALUES (?,?,?,?,?,?,?)', [
                (1, 'qq:12345:67890', 'important', '标题', '正文', '2026-01-01', '2026-01-01'),
                (2, 'qq:12345:67890', 'secret', '心事', '秘密', '2026-01-01', '2026-01-01'),
                (3, 'qq:12345:11111', 'important', '其他人', '其他', '2026-01-01', '2026-01-01')])
        conn.close()
        self.env = patch.dict(os.environ, {'YOUMI_ASTRBOT_CONFIG_FILE': str(config), 'YOUMI_NOTEBOOK_DB': str(db)})
        self.env.start(); self.addCleanup(self.env.stop)
        self.scope = SimpleNamespace(owner='user:1:agent:a')
        self.calls = 0

    async def verify(self, device, client, secret):
        self.calls += 1
        return self.scope

    async def require(self, scope, permission):
        self.assertEqual(permission, 'memory:read')

    async def test_owner_category_and_read_only_detail(self):
        result = await read_notebook(self, self.scope.owner, {})
        self.assertEqual([x['title'] for x in result['entries']], ['标题'])
        self.assertNotIn('content', result['entries'][0])
        self.assertEqual((await read_notebook(self, self.scope.owner, {'entry_id': 1}))['entry']['content'], '正文')
        self.assertIsNone((await read_notebook(self, self.scope.owner, {'entry_id': 2}))['entry'])
        self.assertEqual((await read_notebook(self, self.scope.owner, {'kind': 'secret', 'entry_id': 2}))['entry']['content'], '秘密')
        self.assertEqual(self.calls, 8)

    async def test_foreign_owner_and_invalid_input_denied(self):
        with self.assertRaises(PrivateAuthorizationRequired):
            await read_notebook(self, 'user:2:agent:b', {})
        with self.assertRaises(ValueError):
            await read_notebook(self, self.scope.owner, {'scope': 'qq:12345:11111'})

    async def test_revoked_after_read_denied(self):
        async def revoke(device, client, secret):
            self.calls += 1
            if self.calls > 1:
                raise PrivateAuthorizationRequired()
            return self.scope
        self.verify = revoke
        with self.assertRaises(PrivateAuthorizationRequired):
            await read_notebook(self, self.scope.owner, {})

    async def test_wal_reopens_when_coordination_files_are_missing(self):
        db = Path(os.environ['YOUMI_NOTEBOOK_DB'])
        self.assertFalse(Path(str(db) + '-shm').exists())
        for _ in range(2):
            result = await read_notebook(self, self.scope.owner, {'kind': 'important'})
            self.assertEqual([item['title'] for item in result['entries']], ['标题'])
        with sqlite3.connect(db) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM entries').fetchone()[0], 3)

    async def test_manager_endpoint_rechecks_owner_and_never_accepts_browser_scope(self):
        async def verify_manager(endpoint, proof):
            return {'owner': self.scope.owner}
        self._request = verify_manager
        handler = ManagementHandler({'manager-api': {'secret': 'synthetic-secret'}}, authorization=self)
        app = web.Application(); app.router.add_post('/youmi/management', handler.handle)
        client = TestClient(TestServer(app)); await client.start_server()
        try:
            body = {'operator_id': 1, 'agent_id': 'a', 'request': {'action': 'notebook', 'operation': {'kind': 'secret'}}}
            response = await client.post('/youmi/management', json=body, headers={'Authorization': 'Bearer synthetic-secret'})
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual((await response.json())['data']['entries'][0]['title'], '心事')
            body['request']['operation']['scope'] = 'qq:12345:11111'
            response = await client.post('/youmi/management', json=body, headers={'Authorization': 'Bearer synthetic-secret'})
            self.assertEqual(response.status, 400)
            response = await client.post('/youmi/management', json=body)
            self.assertEqual(response.status, 401)
        finally:
            await client.close()
