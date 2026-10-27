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
