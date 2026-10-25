import asyncio
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from server.companion.extraction import source_supported
from server.companion import jobs as actual_jobs, memory as actual_memory


spec = importlib.util.spec_from_file_location('youmi_worker_competition',
    Path(__file__).resolve().parents[1] / 'youmi_worker_competition.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ExtractionBindingTests(unittest.TestCase):
    def test_failed_original_validation_is_bound_recorded_and_not_repaired(self):
        source, owner = '我常用青瓷杯喝茶', 'user:1:agent:a'
        value = {'memories': [{'category': 'fact', 'content': source, 'source_quote': source,
            'source_index': 0, 'kind': 'explicit', 'subject': 'user', 'value': 'durable',
            'sensitive': False, 'keywords': ['喝茶喝茶']}]}
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE mia_jobs(id TEXT,owner TEXT,state TEXT,source_payload TEXT,attempts INTEGER)')
        db.execute('INSERT INTO mia_jobs VALUES (?,?,?,?,1)', ('job', owner, 'extracting', json.dumps({'text': source})))
        worker = SimpleNamespace(jobs=SimpleNamespace(store=SimpleNamespace(db=db, lock=threading.RLock())))
        before = json.dumps(value, ensure_ascii=False)
        report = {'native_extractions': [{'owner': owner, 'job_id': 'job', 'attempt': 1,
            'original_output_sha256': hashlib.sha256(before.encode()).hexdigest()}]}
        with patch.dict(sys.modules, {'youmi_companion': sys.modules['server.companion'],
                'youmi_companion.jobs': actual_jobs, 'youmi_companion.memory': actual_memory}), \
                patch.object(actual_jobs, 'validate_output', wraps=actual_jobs.validate_output) as original:
            probe.observe_validation_failures(worker, report, threading.RLock(), lambda value: None)
            with self.assertRaises(actual_memory.MemoryError):
                actual_jobs.validate_output(value, [source], [])
            self.assertEqual(original.call_count, 1)
        self.assertEqual(json.dumps(value, ensure_ascii=False), before)
        failure = report['validation_failures'][0]
        self.assertEqual((failure['job_id'], failure['owner'], failure['attempt']), ('job', owner, 1))
        self.assertEqual(failure['original_output'], value)
        self.assertEqual(failure['rejected_clues'], {'keywords': ['喝茶喝茶']})
        self.assertEqual(db.execute('SELECT state FROM mia_jobs').fetchone()[0], 'extracting')

    def test_pending_foreign_and_ambiguous_rows_cannot_prove_sdk_job_binding(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE mia_jobs(id TEXT,owner TEXT,state TEXT,source_payload TEXT,attempts INTEGER)')
        worker = SimpleNamespace(jobs=SimpleNamespace(store=SimpleNamespace(db=db, lock=threading.RLock())))
        owner, source = 'user:1:agent:a', probe.SOURCE

        def insert(key, scope, state, text):
            db.execute('INSERT INTO mia_jobs VALUES (?,?,?,?,1)',
                (key, scope, state, json.dumps({'text': text})))

        insert('pending', owner, 'pending', source)
        insert('foreign', 'user:2:agent:a', 'extracting', source)
        insert('other-source', owner, 'extracting', '我在周日整理其他物品')
        with self.assertRaises(AssertionError):
            probe.active_job(worker, owner, [source])
        insert('actual', owner, 'extracting', source)
        self.assertEqual(probe.active_job(worker, owner, [source])['job_id'], 'actual')
        insert('ambiguous', owner, 'extracting', source)
        with self.assertRaises(AssertionError):
            probe.active_job(worker, owner, [source])

    def test_new_probe_source_cannot_take_question_empty_shortcut(self):
        self.assertTrue(source_supported(probe.SOURCE, None))
        self.assertTrue(source_supported(probe.SOURCE, probe.SOURCE))
        self.assertFalse(source_supported('周三的钢琴练习安排是什么？', None))


class FailedWorkloadShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_workload_failure_still_sends_sigterm_and_reaps_real_child_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {'MIA_P23_REPORT_DIR': directory, 'MIA_P23_MODE': 'worker-extraction',
                'MIA_SCOPE_UPSTREAM': directory}
            with patch.dict(os.environ, environment):
                spec = importlib.util.spec_from_file_location('worker_lifecycle_test',
                    Path(__file__).resolve().parents[1] / 'verify_youmi_app_lifecycle.py')
                lifecycle = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(lifecycle)
                (root / 'app-child').mkdir()
                # A real child owns SIGTERM and exits zero. No app/model claim.
                child = '''import json,os,pathlib,signal,time
root=pathlib.Path(os.environ['MIA_P23_REPORT_DIR'])/'app-child'
def stop(*args):
 (root/'app-results.json').write_text(json.dumps({'passed':True,'code_sha':'unit',
  'provider_requests':0,'accepted_extraction_jobs':0}))
 raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
(root/'ready.json').write_text(json.dumps({'pid':os.getpid(),'ports':[]}))
while True:time.sleep(.01)
'''
                spawn = asyncio.create_subprocess_exec

                async def actual_child(*args, **kwargs):
                    return await spawn(sys.executable, '-c', child, **kwargs)

                report = {'code_sha': 'unit'}
                previous_path = list(sys.path)
                try:
                    with patch.object(asyncio, 'create_subprocess_exec', actual_child), \
                            patch.object(lifecycle, 'members', return_value={}), \
                            patch.object(lifecycle, 'group_state', return_value={}), \
                            patch.dict(sys.modules, {'youmi_worker_competition': SimpleNamespace(
                                drive=AsyncMock(side_effect=AssertionError('Injected workload failure')))}):
                        await asyncio.wait_for(lifecycle.control(report), 10)
                finally:
                    sys.path[:] = previous_path
                self.assertFalse(report['worker_contract_passed'])
                self.assertEqual(report['workload_assertion'], 'Injected workload failure')
                self.assertTrue(report['app_lifecycle_preflight_passed'])
                self.assertEqual(report['app_returncode'], 0)


if __name__ == '__main__':
    unittest.main()
