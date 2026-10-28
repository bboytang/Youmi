import importlib.util
from pathlib import Path
import sys
import unittest


root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location('youmi_network_firewall_plan', root / 'firewall_plan.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FirewallPlanTests(unittest.TestCase):
    def test_local_ranges_and_proxy_mark_are_excluded_before_redirection(self):
        commands = module.commands()
        for table, chain in [('nat', module.TCP_CHAIN), ('mangle', module.UDP_CHAIN)]:
            entries = [x for x in commands if x[:5] == ['iptables', '-t', table, '-A', chain]]
            self.assertIn(['-m', 'mark', '--mark', '255', '-j', 'RETURN'], [x[5:] for x in entries])
            for network in ['127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16']:
                self.assertIn(['-d', network, '-j', 'RETURN'], [x[5:] for x in entries])
            self.assertNotEqual(entries[-1][-1], 'RETURN')
        self.assertEqual(commands[-2][-1], module.TCP_CHAIN)
        self.assertEqual(commands[-1][-1], module.UDP_CHAIN)

    def test_tcp_and_udp_use_separate_listeners(self):
        commands = module.commands()
        tcp = next(x for x in commands if '--to-ports' in x)
        udp = next(x for x in commands if 'TPROXY' in x)
        self.assertEqual(tcp[-1], '31990')
        self.assertEqual(udp[udp.index('--on-port') + 1], '31991')
        self.assertEqual(udp[udp.index('--on-ip') + 1], '127.0.0.1')

    def test_rejects_invalid_or_ipv6_ranges(self):
        for internal in [[], ['::1/128'], ['10.0.0.1/8'], ['not-a-network']]:
            with self.subTest(internal=internal), self.assertRaises(ValueError):
                module.commands(internal)


if __name__ == '__main__':
    unittest.main()
