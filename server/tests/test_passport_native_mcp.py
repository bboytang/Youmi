"""Check Passport MCP against the pinned upstream implementation when supplied by CI."""

import os
from pathlib import Path
import subprocess
import sys
import unittest


class PassportNativeMCPTests(unittest.TestCase):
    def test_pinned_device_mcp_executor_and_protocol(self):
        source = os.environ.get('YOUMI_ASR_TEST_SOURCE')
        if not source:
            self.skipTest('Pinned upstream source is not configured')
        path = Path(source).resolve()
        if len(path.parents) < 4:
            self.skipTest('Full pinned upstream source is not configured')
        upstream = path.parents[3]
        if not (upstream / 'core/providers/tools/device_mcp/mcp_executor.py').is_file():
            self.skipTest('Full pinned upstream source is not configured')
        root = Path(__file__).resolve().parents[2]
        env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(upstream), str(root))))
        subprocess.run([sys.executable, str(Path(__file__).with_name('passport_native_mcp_probe.py'))],
                       cwd=root, env=env, check=True, timeout=15)


if __name__ == '__main__':
    unittest.main()
