import unittest
from integrations.astrbot_plugin_youmi_web.web import checked_url, extract


class WebTests(unittest.TestCase):
    def test_allowed_and_blocked_urls(self):
        self.assertEqual(checked_url('https://github.com/example#fragment'), 'https://github.com/example')
        for url in ['http://github.com/', 'https://github.com@127.0.0.1/', 'https://github.com.evil.test/', 'https://127.0.0.1/', 'https://github.com:8443/', 'https://evil.test/']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                checked_url(url)

    def test_extract_ignores_script_and_limits_output(self):
        title, text, links = extract('<title>Page</title><script>injected</script><main>' + 'x' * 13000 + '</main>')
        self.assertEqual(title, 'Page')
        self.assertNotIn('injected', text)
        self.assertLessEqual(len(text), 12000)


if __name__ == '__main__':
    unittest.main()
