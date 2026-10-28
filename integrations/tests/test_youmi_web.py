import json
import unittest
from integrations.astrbot_plugin_youmi_web.web import checked_url, extract, summarize_json, validate_options


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

    def test_structured_json_summary_and_order(self):
        data = {'message': {'total-results': 1000, 'items': [
            {'title': 'T', 'DOI': str(i), 'author': [{'given': 'A', 'family': 'B'}],
             'abstract': 'a' * 1000, 'unwanted': 'z' * 5000} for i in range(5)]}}
        text, truncated, shape = summarize_json(json.dumps(data), ('title', 'DOI', 'author', 'abstract', 'given', 'family'), 3, 12000)
        result = json.loads(text)
        self.assertTrue(truncated)
        self.assertEqual(shape['total_results'], 1000)
        self.assertEqual(len(result['message']['items']), 1)
        self.assertEqual(result['message']['items'][0]['author'][0]['family'], 'B')
        self.assertEqual(len(result['message']['items'][0]['abstract']), 400)
        self.assertNotIn('unwanted', result['message']['items'][0])
        self.assertEqual(shape['arrays'][0]['total'], 5)
        self.assertEqual(shape['arrays'][0]['removed_fields_per_item'], [1])

    def test_invalid_options_are_rejected(self):
        for args in [(None, 0, 10), (None, 21, 10), (None, 3, 12001),
                     (None, True, 1), ('title', 3, 10), ([1], 3, 10), (['x'] * 21, 3, 10)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                validate_options(*args)

    def test_final_prefix_and_invalid_json_are_marked(self):
        text, truncated, shape = summarize_json('{"very_long_key":123456}', [], 3, 3)
        self.assertTrue(truncated)
        self.assertEqual(len(text), 3)
        self.assertNotEqual(shape, 'unknown')
        text, truncated, shape = summarize_json('{bad json', [], 3, 3)
        self.assertEqual((text, truncated, shape), ('{ba', True, 'unknown'))


class WebFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_is_returned_as_bounded_text(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from aiohttp import web
        from integrations.astrbot_plugin_youmi_web import web as module

        body = '{"value":"' + '中' * 20000 + '<script>kept</script>"}'
        async def handle(request):
            value = {'small': '{ "text": "中<script>" }', 'broken': '{' + 'x' * 15000,
                     'huge': '{"value":"' + '中' * 400000 + '"}'}.get(request.query.get('case'), body)
            return web.Response(text=value, content_type=request.match_info['content_type'].replace('_', '/'))

        app = web.Application()
        app.router.add_get('/{content_type}', handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            with tempfile.TemporaryDirectory() as directory:
                credentials = Path(directory) / 'proxy.json'
                credentials.write_text('{"username":"test","password":"test"}')
                with patch.object(module, 'PROXY', None), patch.object(module, 'PROXY_CREDENTIALS', credentials):
                    for content_type in ('application_json', 'application_problem+json'):
                        result = await module.Web(Path(directory)).fetch(f'http://127.0.0.1:{port}/{content_type}')
                        self.assertTrue(result['truncated'])
                        self.assertLessEqual(len(result['text']), 12000)
                        self.assertIsInstance(json.loads(result['text']), dict)
                        self.assertEqual(result['title'], '')
                        self.assertEqual(result['links'], [])
                    result = await module.Web(Path(directory)).fetch(f'http://127.0.0.1:{port}/application_json?case=small')
                    self.assertEqual(result['text'], '{ "text": "中<script>" }')
                    self.assertFalse(result['truncated'])
                    for case in ('broken', 'huge'):
                        result = await module.Web(Path(directory)).fetch(f'http://127.0.0.1:{port}/application_json?case={case}')
                        self.assertTrue(result['truncated'])
                        self.assertEqual(result['shape'], 'unknown')
                        self.assertEqual(len(result['text']), 12000)
                    result = await module.Web(Path(directory)).fetch(f'http://127.0.0.1:{port}/text_plain')
                    self.assertEqual(result['error'], 'unsupported_content_type')
        finally:
            await runner.cleanup()


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
