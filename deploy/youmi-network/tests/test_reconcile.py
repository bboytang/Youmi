import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('youmi_network_reconcile',
    Path(__file__).resolve().parents[1] / 'reconcile.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReconcileTests(unittest.TestCase):
    def test_compose_changes_stop_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose, config = Path(tmp) / 'compose.yaml', Path(tmp) / 'config.json'
            compose.write_text('services: {}', encoding='utf-8')
            data = {'project': 'youmi-test', 'project_directory': tmp,
                    'files': [{'path': str(compose), 'sha256': hashlib.sha256(compose.read_bytes()).hexdigest()}],
                    'environment': {key: 'value' for key in module.ENV_KEYS}}
            config.write_text(json.dumps(data), encoding='utf-8')
            self.assertEqual(module.load_config(config), data)
            compose.write_text('services: {changed: {}}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'compose_file_changed'):
                module.load_config(config)

    def test_only_stale_services_are_recreated_in_dependency_order(self):
        class Fake(module.Reconciler):
            def __init__(self):
                self.items = {name: {'State': {'Running': True, 'Pid': pid}}
                              for name, pid in zip(module.ORDER, [1, 2, 3, 4])}
                self.namespaces = {1: 10, 2: 10, 3: 9, 4: 9}
                self.changed = []

            def inspect(self, service):
                return self.items.get(service)

            def wait(self, service, healthy=False):
                return self.items[service]

            def namespace(self, item):
                return self.namespaces[item['State']['Pid']]

            def up(self, service, *, recreate):
                self.changed.append((service, recreate))
                self.namespaces[self.items[service]['State']['Pid']] = 10

        control = Fake()
        control.run()
        self.assertEqual(control.changed, [('youmi-browserless', True), ('astrbot', True)])


if __name__ == '__main__':
    unittest.main()
