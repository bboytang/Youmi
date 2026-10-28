import unittest
from integrations.astrbot_plugin_youmi_web.web import checked_url, extract


class WebTests(unittest.TestCase):
    def test_public_site_urls_are_not_filtered_by_domain(self):
        for url in ['https://github.com/example', 'https://arbitrary.example:8443/', 'http://example.org/']:
            with self.subTest(url=url):
                self.assertEqual(checked_url(url), url)
        self.assertEqual(checked_url('https://github.com/example#fragment'), 'https://github.com/example')
        for url in ['ftp://example.org/', 'https://name:password@example.org/', 'https://example.org:bad/']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                checked_url(url)

    def test_extract_ignores_script_and_limits_output(self):
        title, text, links = extract('<title>Page</title><script>injected</script><main>' + 'x' * 13000 + '</main>')
        self.assertEqual(title, 'Page')
        self.assertNotIn('injected', text)
        self.assertLessEqual(len(text), 12000)


if __name__ == '__main__':
    unittest.main()

class BrowserRenderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tempfile
        from pathlib import Path
        from aiohttp import web
        from unittest.mock import patch
        from integrations.astrbot_plugin_youmi_web import render as module
        self.seen = {}
        async def handle(request):
            self.seen['token'] = request.query.get('token')
            self.seen['launch'] = request.query.get('launch')
            self.seen['body'] = await request.json()
            return web.Response(text='<title>Rendered</title><main>Dynamic result</main>', content_type='text/html')
        app = web.Application()
        app.router.add_post('/chromium/content', handle)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.temp = tempfile.TemporaryDirectory()
        credentials = Path(self.temp.name) / 'browserless.json'
        credentials.write_text('{"token":"test-token"}')
        self.endpoint = patch.object(module, 'BROWSER_ENDPOINT', f'http://127.0.0.1:{port}/chromium/content')
        self.creds = patch.object(module, 'CREDENTIALS', credentials)
        self.endpoint.start(); self.creds.start()
        self.module = module

    async def asyncTearDown(self):
        self.endpoint.stop(); self.creds.stop()
        await self.runner.cleanup()
        self.temp.cleanup()

    async def test_render_uses_browser_service_and_proxy(self):
        import json
        result = await self.module.render('https://example.org/page')
        self.assertEqual(result['text'], 'Rendered Dynamic result')
        self.assertEqual(self.seen['token'], 'test-token')
        self.assertEqual(self.seen['body']['url'], 'https://example.org/page')
        self.assertIn('--proxy-server=http://127.0.0.1:31987', json.loads(self.seen['launch'])['args'])
