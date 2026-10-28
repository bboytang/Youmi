import importlib.util
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch


spec = importlib.util.spec_from_file_location('youmi_network_wait',
    Path(__file__).resolve().parents[1] / 'wait_for_proxy.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class WaitForProxyTests(unittest.TestCase):
    def test_requires_both_firewall_marker_and_proxy_port(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'youmi-network-ready'
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(module.urllib.request, 'build_opener', return_value=opener), \
                patch.object(module.socket, 'create_connection') as connect:
            self.assertTrue(module.ready())
            connect.side_effect = OSError('proxy unavailable')
            self.assertFalse(module.ready())
            response.__enter__.return_value.read.return_value = b'not-ready'
            connect.reset_mock()
            self.assertFalse(module.ready())
            connect.assert_not_called()

    def test_waits_until_ready_and_replaces_process(self):
        with patch.object(module, 'ready', side_effect=[False, True]), \
                patch.object(module.time, 'sleep') as sleep, patch.object(module.os, 'execvp') as execute:
            module.main(['python', 'main.py'])
        sleep.assert_called_once_with(1)
        execute.assert_called_once_with('python', ['python', 'main.py'])


if __name__ == '__main__':
    unittest.main()
