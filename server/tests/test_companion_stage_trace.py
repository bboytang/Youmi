"""CI wrappers must preserve real local validation/deletion/revocation paths."""

import importlib
import importlib.util
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

from server.tests import test_companion_reply as fixtures

spec = importlib.util.spec_from_file_location('p23_stage_trace',
    Path(__file__).resolve().parents[2] / 'scripts/ci/youmi_stage_trace.py')
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


class InstalledMethodTraceTests(unittest.IsolatedAsyncioTestCase):
    async def test_observation_preserves_delivery_rechecks_delete_and_revoke(self):
        modules = {'youmi_companion': importlib.import_module('server.companion')}
        for name in ('authorization', 'context', 'embedding', 'persona', 'reply', 'semantic'):
            modules['youmi_companion.' + name] = importlib.import_module('server.companion.' + name)
        for name in ('test_each_validation_checks_current_permission_once_without_reusing_proof',
                'test_stream_stops_and_closes_on_deletion_before_next_delivery',
                'test_stream_stops_on_revocation_before_next_delivery_and_releases_slot'):
            with self.subTest(case=name), patch.dict(sys.modules, modules):
                fixture = fixtures.ReplyTests(name)
                fixture.setUp()
                store = fixture.store
                db, lock = store.db, store.lock
                trace = observer.StageTrace({'started': time.perf_counter()})
                try:
                    trace.install(store)
                    await getattr(fixture, name)()
                    self.assertTrue(trace.events)
                    self.assertEqual(trace.dropped, 0)
                    self.assertIn('reply.validate', trace.summary())
                    self.assertIn('sqlite.python_lock_wait', trace.summary())
                finally:
                    trace.close()
                    self.assertIs(store.db, db)
                    self.assertIs(store.lock, lock)
                    fixture.tearDown()
