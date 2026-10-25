import unittest

from aiohttp import web
from aiohttp.test_utils import TestServer
from tempfile import TemporaryDirectory
from pathlib import Path

from integrations.astrbot_plugin_youmi.client import BridgeClient


class PersonaTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_transports_complete_persona_larger_than_old_response_limit(self):
        prompt = '# 人设设定\n' + '你是图书管理员，使用文言回答。\n' * 5000
        async def prepare(request):
            self.assertEqual(request.headers['Authorization'], 'Bearer synthetic-token')
            return web.json_response({'system_prompt': prompt})
        app = web.Application()
        app.router.add_post('/internal/astrbot/prepare', prepare)
        server = TestServer(app)
        await server.start_server()
        with TemporaryDirectory() as temp:
            token = Path(temp) / 'token'
            token.write_text('synthetic-token')
            client = BridgeClient({'youmi_base_url': str(server.make_url('')).rstrip('/'),
                'allow_internal_http': True, 'service_credential_file': str(token)})
            try:
                response = await client.call('prepare', {})
                self.assertEqual(response['system_prompt'], prompt)
            finally:
                await client.close()
                await server.close()
