import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location('youmi_network_install_rules', root / 'install_rules.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class InstallRulesTests(unittest.TestCase):
    def test_wrong_namespace_changes_nothing(self):
        with patch('os.stat') as stat:
            stat.return_value.st_ino = 12
            with patch.object(module, '_run') as run, self.assertRaisesRegex(RuntimeError, 'wrong_network_namespace'):
                module.install(13)
            run.assert_not_called()

    def test_failure_rolls_back_only_successful_commands(self):
        planned = [['iptables', '-t', 'nat', '-N', 'TEST'],
                   ['iptables', '-t', 'nat', '-A', 'TEST', '-j', 'RETURN'],
                   ['ip', '-4', 'rule', 'add', 'fwmark', '1', 'lookup', '219']]
        seen = []

        def run(command, *, check=True):
            seen.append(command)
            if command == planned[-1]:
                raise subprocess.CalledProcessError(1, command)

        with patch.object(module, 'preflight'), patch.object(module, 'commands', return_value=planned), \
                patch.object(module, '_run', side_effect=run), patch('os.stat') as stat:
            stat.return_value.st_ino = 12
            with self.assertRaises(subprocess.CalledProcessError):
                module.install(12)
        self.assertEqual(seen[-2:], [['iptables', '-t', 'nat', '-D', 'TEST', '-j', 'RETURN'],
                                     ['iptables', '-t', 'nat', '-X', 'TEST']])


if __name__ == '__main__':
    unittest.main()
