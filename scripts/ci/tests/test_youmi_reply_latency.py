from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import asyncio

from server.companion.authorization import AuthenticatedScope
from server.companion.context import ContextBuilder, ContextLimits


spec = importlib.util.spec_from_file_location('youmi_reply_latency',
    Path(__file__).resolve().parents[1] / 'youmi_reply_latency.py')
latency = importlib.util.module_from_spec(spec)
spec.loader.exec_module(latency)


class EmptyMemoryBaselineTests(unittest.TestCase):
    def test_stage_diagnostic_has_fixed_small_real_request_cap(self):
        self.assertEqual(latency.REQUEST_CAPS['reply-stages'], 4)
        self.assertEqual(latency.REQUEST_CAPS['reply-stages-replay'], 0)
        root = Path(__file__).resolve().parents[3]
        self.assertIn('reply-stages', (root / '.github/workflows/youmi-memory-shared-performance.yml').read_text())
        self.assertIn('reply-stages-replay', (root / 'scripts/ci/run_youmi_shared_envelope.sh').read_text())

    def test_frozen_report_corrected_stages_preserve_all_six_total_p95_values(self):
        directory = Path(__file__).resolve().parents[1]
        timing_spec = importlib.util.spec_from_file_location('youmi_tts_timing', directory / 'youmi_tts_timing.py')
        timing = importlib.util.module_from_spec(timing_spec)
        timing_spec.loader.exec_module(timing)
        with patch.dict(sys.modules, {'youmi_tts_timing': timing}):
            spec = importlib.util.spec_from_file_location('stage_review', directory / 'review_youmi_reply_stages.py')
            review = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(review)
        root = directory.parents[1] / 'docs/verification/mia-persona-memory'
        result = review.review(root / 'performance-auth-once-latency-child.json.gz')
        original = json.loads((root / 'performance-auth-once-latency-review.json').read_text())
        self.assertEqual(len(result['pairs']), 36)
        for corrected, frozen in zip(result['groups'], original['query_verdicts']):
            self.assertAlmostEqual(corrected['total_delta_p95_ms'], frozen['delta_p95_ms'], places=6)
        worst = result['groups'][3]['worst_pair']
        for key, expected in zip(worst['delta'], (322.904883, 437.91558, 174.669288)):
            self.assertAlmostEqual(worst['delta'][key], expected, places=5)

    def test_sdk_observer_first_content_is_relative_to_case_not_sdk(self):
        spec = importlib.util.spec_from_file_location('youmi_tts_timing',
            Path(__file__).resolve().parents[1] / 'youmi_tts_timing.py')
        timing = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(timing)
        response = SimpleNamespace(__iter__=None, close=Mock())
        class Response:
            def __iter__(self):
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))])
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='中文'))])
            close = response.close
        create = Mock(return_value=Response())
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        old = Mock()
        old.with_options.return_value = client
        provider = SimpleNamespace(client=old)
        configuration = {'model_name': latency.MODEL, 'api_key': 'synthetic',
            'base_url': 'https://synthetic.invalid', 'type': 'openai'}
        modules = {'httpx': SimpleNamespace(Client=Mock()), 'youmi_tts_timing': timing,
            'youmi_model_comparison': SimpleNamespace(safe_provider_error=Mock()),
            'youmi_companion.model_requests': SimpleNamespace(model_requests=SimpleNamespace(snapshot=lambda: {}))}
        environment = {'MIA_TEST_LLM_API_KEY': 'synthetic', 'MIA_TEST_LLM_BASE_URL': configuration['base_url']}
        with patch.dict(sys.modules, modules), patch.dict(os.environ, environment):
            latency.observe_provider(configuration, 'reply', {'provider_requests': 0, 'provider_request_cap': 1},
                threading.RLock(), lambda report: None, lambda *args: provider)
            sample = provider._p23_sample = {'started': 100.0}
            messages = [{'role': 'system', 'content': 'core'},
                {'role': 'user', 'content': '{"youmi_context":{"facts":[]}}'}]
            with patch.object(latency.time, 'perf_counter', side_effect=[100.25, 100.9]):
                stream = provider.client.chat.completions.create(model=latency.MODEL,
                    messages=messages, max_tokens=4096, stream=True)
                self.assertEqual(len(list(stream)), 2)
                stream.close()
        self.assertAlmostEqual(sample['sdk_start_ms'], 250)
        self.assertAlmostEqual(sample['sdk_first_content_ms'], 900)
        create.assert_called_once()
        response.close.assert_called_once()

    def test_shutdown_mode_has_only_two_original_extraction_attempts(self):
        self.assertEqual(latency.REQUEST_CAPS['worker-shutdown'], 2)

    def test_original_prompt_budget_and_question_survive_without_index_access(self):
        scope = AuthenticatedScope('user:1:agent:a', 1, 1, 'a', 'mia', 'device', 'client', 0, 1,
            datetime.now(timezone.utc) + timedelta(days=1), frozenset({'memory:read'}))
        builder = ContextBuilder(object())  # No store/retrieve/authorization methods available.
        limits = ContextLimits(latency.MODEL, 16384, 4096, 'Synthetic unit envelope')
        query, role = '我去湖边散步应该准备什么？', '测试角色数据'
        baseline = latency.empty_memory_request(builder, scope, limits, query, role, 'baseline')
        inputs = builder._prepare(query, [], [], role, [], None)
        inputs.update(facts=[{'id': 'one', 'category': 'fact', 'content': '散步带水壶',
            'content_version': 1, 'evidence_version': 1}], traits=[], memory_status='available',
            events=[], emotion_trend={'status': 'not_relevant'}, session_id='memory')
        reference = builder._assemble(scope, 'unit-memory', limits, None, inputs).request()
        self.assertEqual(baseline['messages'][0], reference['messages'][0])
        self.assertEqual(baseline['messages'][-1], {'role': 'user', 'content': query})
        self.assertEqual(baseline['max_tokens'], reference['max_tokens'])
        data = json.loads(baseline['messages'][1]['content'])['youmi_context']
        self.assertEqual(data['facts'], [])
        self.assertEqual(data['events'], [])
        self.assertEqual(data['role_data'], role)
        self.assertEqual(len(json.loads(reference['messages'][1]['content'])['youmi_context']['facts']), 1)


class DriverTerminalReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_controlled_driver_ignores_tts_and_llm_until_actual_terminal(self):
        for terminal in ('p23_case', 'p23_error'):
            socket = SimpleNamespace(recv=AsyncMock(side_effect=[json.dumps({'type': name})
                for name in ('tts', 'llm', terminal)]))
            self.assertEqual(await latency.read_case(socket), {'type': terminal})
            self.assertEqual(socket.recv.await_count, 3)

    async def test_nonterminal_frames_do_not_restart_the_absolute_deadline(self):
        socket = SimpleNamespace(recv=AsyncMock(return_value='{"type":"tts"}'))
        with patch.object(latency, 'time', SimpleNamespace(monotonic=Mock(side_effect=[100, 100.1, 101.1]))):
            with self.assertRaises(asyncio.TimeoutError):
                await latency.read_case(socket, timeout=1)
        self.assertEqual(socket.recv.await_count, 1)


if __name__ == '__main__':
    unittest.main()
