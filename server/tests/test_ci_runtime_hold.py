"""Controlled fixture checks; not real app/model lifecycle acceptance."""

import asyncio
import json
from pathlib import Path
import sys
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.ci.youmi_behavior_runtime import observe_hold
from server.companion import extraction
from server.companion.memory import MemoryError


class RuntimeHoldTests(unittest.IsolatedAsyncioTestCase):
    async def test_hold_preserves_validated_original_output_and_marks_only_actual_source(self):
        source = '我常用松纹刻刀做木雕'
        value = {'memories': [{'category': 'fact', 'content': source, 'source_quote': source,
            'source_index': 0, 'kind': 'explicit', 'subject': 'user', 'value': 'durable', 'sensitive': False}]}
        calls = []

        async def resolve(scope):
            def extract(sources, context):
                calls.append(sources)
                return value
            return extract

        db = sqlite3.connect(':memory:', check_same_thread=False)
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE mia_jobs (id TEXT, source_payload TEXT, owner TEXT, state TEXT)')
        db.execute('INSERT INTO mia_jobs VALUES (?,?,?,?)', ('synthetic-unit-job', json.dumps({'text': source}), 'synthetic-owner', 'extracting'))
        worker = SimpleNamespace(resolve=resolve, jobs=SimpleNamespace(store=SimpleNamespace(db=db, lock=threading.RLock())),
                                 index=SimpleNamespace(encoder=object()))
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {'youmi_companion.extraction': extraction}):
            root = Path(directory)
            (root / 'shutdown-release.json').write_text('{}')
            report = {}
            observe_hold(worker, {'source': source}, root, report, threading.Lock(), lambda result: None)
            extract = await worker.resolve(SimpleNamespace(owner='synthetic-owner'))
            returned = await asyncio.to_thread(extract, [source], [])
            self.assertIs(returned, value)
            self.assertEqual(calls, [[source]])
            self.assertTrue(json.loads((root / 'shutdown-held.json').read_text())['validated_actual_output'])
            self.assertEqual(len(report['shutdown_held_outputs']), 1)
            self.assertEqual(report['shutdown_held_outputs'][0]['job_id'], 'synthetic-unit-job')

    async def test_invalid_output_never_gets_held_or_repaired(self):
        value = {'memories': [{'content': 'invented'}]}
        async def resolve(scope):
            return lambda sources, context: value

        worker = SimpleNamespace(resolve=resolve, index=SimpleNamespace(encoder=object()))
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {'youmi_companion.extraction': extraction}):
            root = Path(directory)
            report = {}
            observe_hold(worker, {'source': '我喜欢蓝色'}, root, report, threading.Lock(), lambda result: None)
            extract = await worker.resolve(None)
            returned = await asyncio.to_thread(extract, ['我喜欢蓝色'], [])
            self.assertIs(returned, value)
            with self.assertRaises(MemoryError):
                extraction.validate_output(returned, ['我喜欢蓝色'], [])
            self.assertFalse((root / 'shutdown-held.json').exists())
            self.assertEqual(report, {})

    async def test_vector_hold_requires_original_persisted_result_and_keeps_attempts(self):
        import numpy as np
        source = '我常用帆布袋装木雕工具'
        vector = np.zeros(768, dtype=np.float32)
        vector[0] = 1
        encoder_calls = []
        def encode(text, query=False):
            encoder_calls.append((text, query))
            return vector
        db = sqlite3.connect(':memory:', check_same_thread=False)
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE mia_jobs (id TEXT,source_payload TEXT,owner TEXT,state TEXT,validated_result TEXT,attempts INTEGER)')
        db.execute('INSERT INTO mia_jobs VALUES (?,?,?,?,?,?)', ('synthetic-durable-job', json.dumps({'text': source}),
            'synthetic-owner', 'validated', json.dumps({'_schema': 1, 'memories': [
                {'content': source, 'kind': 'explicit', 'sensitive': False}]}), 2))
        async def resolve(scope):
            self.fail('Durable vector resume must not resolve a model')
        worker = SimpleNamespace(resolve=resolve, index=SimpleNamespace(encoder=SimpleNamespace(encode=encode)),
            jobs=SimpleNamespace(store=SimpleNamespace(db=db, lock=threading.RLock())))
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {'youmi_companion.extraction': extraction}):
            root = Path(directory)
            (root / 'shutdown-release.json').write_text('{}')
            report = {}
            observe_hold(worker, {'source': source, 'owner': 'synthetic-owner', 'stage': 'validated_vector'},
                         root, report, threading.Lock(), lambda result: None)
            returned = await asyncio.to_thread(worker.index.encoder.encode, source)
            self.assertIs(returned, vector)
            self.assertEqual(encoder_calls, [(source, False)])
            self.assertEqual(report['shutdown_held_outputs'][0]['persisted_attempts'], 2)
            self.assertEqual(db.execute('SELECT attempts FROM mia_jobs').fetchone()[0], 2)
