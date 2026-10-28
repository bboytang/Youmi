import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('youmi_network_xray_config',
    Path(__file__).resolve().parents[1] / 'xray_config.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class XrayConfigTests(unittest.TestCase):
    def setUp(self):
        self.original = {
            'inbounds': [{'listen': '127.0.0.1', 'port': 31987, 'protocol': 'http'}],
            'outbounds': [
                {'tag': 'vpn', 'protocol': 'vless', 'streamSettings': {'security': 'reality'}},
                {'tag': 'domestic', 'protocol': 'freedom'},
            ],
            'routing': {'domainStrategy': 'IPIfNonMatch', 'rules': [
                {'domain': ['geosite:cn'], 'outboundTag': 'domestic'},
                {'ip': ['geoip:cn'], 'outboundTag': 'domestic'},
            ]},
        }

    def test_preserves_routes_and_existing_inbound_without_mutating_input(self):
        before = json.loads(json.dumps(self.original))
        config = module.make_config(self.original)
        self.assertEqual(self.original, before)
        self.assertEqual(config['routing'], before['routing'])
        self.assertEqual(config['inbounds'][0], before['inbounds'][0])
        self.assertEqual(config['outbounds'][0]['streamSettings']['security'], 'reality')
        self.assertTrue(all(x['streamSettings']['sockopt']['mark'] == 255 for x in config['outbounds']))
        tcp, udp = config['inbounds'][-2:]
        self.assertEqual(tcp['listen'], '127.0.0.1')
        self.assertEqual(udp['listen'], '127.0.0.1')
        self.assertEqual(tcp['settings'], {'network': 'tcp', 'followRedirect': True})
        self.assertEqual(udp['settings'], {'network': 'udp', 'followRedirect': True})
        self.assertEqual(tcp['streamSettings']['sockopt']['tproxy'], 'redirect')
        self.assertEqual(udp['streamSettings']['sockopt']['tproxy'], 'tproxy')
        self.assertEqual(tcp['sniffing']['destOverride'], ['http', 'tls', 'quic'])

    def test_rejects_collisions(self):
        self.original['inbounds'].append({'port': 31990})
        with self.assertRaisesRegex(ValueError, 'transparent_port_in_use'):
            module.make_config(self.original)
        self.original['inbounds'].pop()
        self.original['outbounds'][0]['streamSettings']['sockopt'] = {'mark': 12}
        with self.assertRaisesRegex(ValueError, 'outbound_mark_conflict'):
            module.make_config(self.original)

    def test_output_permissions_and_source_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / 'source.json', Path(tmp) / 'output.json'
            source.write_text(json.dumps(self.original), encoding='utf-8')
            module.write_config(source, output)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(json.loads(source.read_text()), self.original)
            with self.assertRaisesRegex(ValueError, 'source_equals_destination'):
                module.write_config(source, source)


if __name__ == '__main__':
    unittest.main()
