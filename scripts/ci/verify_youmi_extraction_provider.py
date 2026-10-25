"""Ten bounded extraction provider samples; synthetic data and vector fixtures, no production."""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
from urllib.parse import urlsplit

import httpx
import numpy as np

sys.path.insert(0, str(Path(os.environ['MIA_EXTRACTION_UPSTREAM']) / 'main/xiaozhi-server'))
logger = SimpleNamespace()
logger.bind = lambda *args, **kwargs: logger
for level in ('debug', 'info', 'warning', 'error'):
    setattr(logger, level, lambda *args, **kwargs: None)
logging = ModuleType('config.logger')
logging.setup_logging = lambda: logger
sys.modules['config.logger'] = logging
util = ModuleType('core.utils.util')
util.check_model_key = lambda *args, **kwargs: None
sys.modules['core.utils.util'] = util

from core.providers.llm.openai.openai import LLMProvider
from youmi_companion.authorization import AuthenticatedScope
from youmi_companion.embedding import DIMENSION, MODEL_KEY, REVISION
from youmi_companion.extraction import LLMMemoryExtractor
from youmi_companion.jobs import JobStore, MemoryWorker, TurnCompleted
from youmi_companion.memory import MemoryStore


class UnitEncoder:
    model_id, revision, dimension = MODEL_KEY, REVISION, DIMENSION

    def encode(self, text, query=False):
        vector = np.zeros(DIMENSION, dtype=np.float32)
        vector[0] = 1
        return vector


class Policy:
    async def restore(self, snapshot):
        assert snapshot == self.scope.snapshot()
        return self.scope

    async def require(self, scope, permission):
        assert scope == self.scope and permission in scope.permissions


def deadline(*unused):
    raise TimeoutError('Bounded P17 provider verification expired')


async def evaluate(report, secret):
    from youmi_model_comparison import selected_model, output_tokens, configure_provider, safe_provider_error
    base, model = os.environ['MIA_TEST_LLM_BASE_URL'], os.environ['MIA_TEST_LLM_MODEL']
    baseline = model
    model = selected_model(baseline)
    url = urlsplit(base)
    if (not secret or url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment):
        raise ValueError('Dedicated HTTPS test configuration is missing or invalid')
    report.update(provider_host=url.hostname, configured_model=model, baseline_model=baseline)
    report['limits']['max_output_tokens'] = output_tokens()
    outputs = []
    def factory():
        if report['requests'] >= 10:
            raise ValueError('P17 request budget exhausted')
        report['requests'] += 1
        provider = LLMProvider({'api_key': secret, 'base_url': base, 'model_name': model, 'max_tokens': output_tokens(),
                                'temperature': 0.1, 'timeout': 30})
        report['model_comparison'] = configure_provider(provider)
        original = provider.client
        provider.client = original.with_options(max_retries=0,
            http_client=httpx.Client(timeout=30, follow_redirects=False))
        original.close()
        create = provider.client.chat.completions.create
        def observe_request(*args, **kwargs):
            report.setdefault('sdk_request_parameters', []).append({key: kwargs[key] for key in
                ('model', 'max_tokens', 'temperature', 'top_p', 'extra_body', 'stream') if key in kwargs})
            request_number = len(report['sdk_request_parameters'])
            try:
                response = create(*args, **kwargs)
            except Exception as error:
                report.setdefault('provider_errors', []).append(dict(safe_provider_error(error), stage='sdk_request'))
                raise
            if not kwargs.get('stream'):
                return response
            class ObservedStream:
                def __iter__(self):
                    for chunk in response:
                        finished = [{'index': choice.index, 'finish_reason': choice.finish_reason}
                                    for choice in (getattr(chunk, 'choices', None) or []) if choice.finish_reason]
                        usage = getattr(chunk, 'usage', None)
                        if finished or usage:
                            report.setdefault('sdk_response_metadata', []).append({'request': request_number,
                                'response_model': getattr(chunk, 'model', None), 'finished': finished,
                                'usage': usage.model_dump(exclude_none=True) if usage else None})
                        yield chunk
                def close(self):
                    response.close()
            return ObservedStream()
        provider.client.chat.completions.create = observe_request
        original_response = provider.response_no_stream
        def capture(system, user):
            report.setdefault('extraction_inputs', []).append({
                'system': system.replace(secret, '[REDACTED]'),
                'user': user.replace(secret, '[REDACTED]'),
                'system_sha256': hashlib.sha256(system.encode()).hexdigest(),
                'user_sha256': hashlib.sha256(user.encode()).hexdigest(),
            })
            output = original_response(system, user)
            outputs.append(output.replace(secret, '[REDACTED]') if isinstance(output, str) else 'non_text_output')
            return output
        provider.response_no_stream = capture
        function_response = provider.response_with_functions
        def capture_function(session, dialogue, functions=None, **kwargs):
            system, user = dialogue[0]['content'], dialogue[1]['content']
            report.setdefault('extraction_inputs', []).append({'system': system.replace(secret, '[REDACTED]'),
                'user': user.replace(secret, '[REDACTED]'), 'tools': functions,
                'system_sha256': hashlib.sha256(system.encode()).hexdigest(),
                'user_sha256': hashlib.sha256(user.encode()).hexdigest()})
            events, text, arguments = [], '', {}
            complete = False
            try:
                for content, calls in function_response(session, dialogue, functions=functions, **kwargs):
                    events.append({'content': content,
                        'tool_calls': [call.model_dump(exclude_none=True) for call in calls or []]})
                    text += content or ''
                    for call in calls or []:
                        if call.function:
                            arguments[call.index] = arguments.get(call.index, '') + (call.function.arguments or '')
                    yield content, calls
                complete = True
            except Exception as error:
                report.setdefault('provider_errors', []).append(dict(safe_provider_error(error), stage='function_stream'))
                raise
            finally:
                report.setdefault('extraction_tool_outputs', []).append({'complete': complete, 'events': events})
                if len(arguments) == 1:
                    outputs.append(next(iter(arguments.values())).replace(secret, '[REDACTED]'))
                elif not arguments and (complete or events):
                    outputs.append(text.replace(secret, '[REDACTED]'))
        provider.response_with_functions = capture_function
        return provider
    policy = Policy()
    policy.scope = AuthenticatedScope('user:1:agent:p17-live-fixture', 1, 1, 'p17-live-fixture', 'mia', 'device', 'client', 0, 1,
        datetime.now(timezone.utc) + timedelta(days=1), frozenset({'memory:read', 'memory:write', 'memory:manage'}))
    cases = [('neutral_durable', '我每周三都会在公司食堂吃素', 'active'),
             ('medical_confirmation', '我被确诊为糖尿病', 'pending'),
             ('quoted_fiction', '这只是小说台词：“我住在月球”；不要把它记成我的真实经历', 'empty'),
             ('ongoing_goal', '我计划接下来半年每周练习游泳', 'active'),
             ('temperature_preference', '我看温度时更习惯用摄氏度。', 'active'),
             ('lookup_not_existing_fact', '查一下temperature里的温度，用我平时习惯的单位告诉我。', 'empty'),
             ('parallel_lookup_not_fact', '请同时查红盒和蓝盒，分别告诉我各有几颗棋子。', 'empty'),
             ('independent_activity_clues', '配音练习时我总用橡木耳机', 'active'),
             ('independent_positive_importance', '修好祖父的收音机对我有特别的意义', 'active'),
             ('independent_negative_importance', '我一直觉得整理旧纸箱并不重要', 'active')]
    with tempfile.TemporaryDirectory() as folder:
        store = MemoryStore(Path(folder) / 'facts.sqlite3')
        async def resolve(scope):
            return LLMMemoryExtractor(factory)
        jobs = JobStore(store)
        worker = MemoryWorker(jobs, policy, resolve, encoder=UnitEncoder())
        for name, text, expected in cases:
            admitted = await worker.submit(TurnCompleted(policy.scope, 'live-synthetic', name, name, text, 0))
            started = time.perf_counter()
            captured_before = len(outputs)
            await worker.run_once()  # No evaluation retry or parameter tuning.
            task = jobs.current(admitted)
            found = store.db.execute('SELECT m.content,m.confirmed,x.entities,x.keywords,x.trigger_terms,x.emotional_weight FROM memories m '
                'JOIN mia_memory_meta x ON x.memory_id=m.id JOIN mia_source_meta s ON s.memory_id=m.id '
                'WHERE m.owner=? AND s.message_id=?', (policy.scope.owner, name)).fetchall()
            passed = (task['state'] == 'empty' and not found if expected == 'empty' else
                task['state'] == 'committed' and bool(found) and all(bool(row['confirmed']) == (expected == 'active') for row in found))
            if expected == 'pending' and passed:
                passed = json.loads(task['validated_result']) == {'confirmation_required': True}
            if passed and name == 'independent_activity_clues':
                clues = json.loads(found[0]['entities']) + json.loads(found[0]['keywords'])
                triggers = json.loads(found[0]['trigger_terms'])
                passed = (any(len(term) >= 2 and term in '橡木耳机' for term in clues)
                    and any(len(term) >= 2 and term in '配音练习时我总用哪个物品？' for term in triggers))
            if passed and name == 'independent_positive_importance':
                passed = found[0]['emotional_weight'] is not None and found[0]['emotional_weight'] > 0
            if passed and name == 'independent_negative_importance':
                passed = found[0]['emotional_weight'] in (None, 0)
            report['cases'].append({'name': name, 'source': text, 'expected': expected, 'state': task['state'],
                'error_category': task['error_category'], 'pipeline_semantic_pass': passed,
                'model_requests': len(outputs) - captured_before,
                'model_output': outputs[-1] if len(outputs) > captured_before else None,
                'stored': [dict(row) for row in found], 'elapsed_ms': (time.perf_counter() - started) * 1000})
            if not passed and task['state'] not in {'committed', 'empty', 'failed', 'cancelled'}:
                raise AssertionError('Actual task remains unfinished; do not rerun it as a new case')
        report['passed'] = all(row['pipeline_semantic_pass'] for row in report['cases'])
        store.close()


def main():
    report = {'code_sha': os.environ.get('GITHUB_SHA'), 'upstream_sha': '87c6df77b0220ddbd8fc804f984f83f69f3225e5',
        'kind': 'P17_real_installed_provider_structured_extraction', 'passed': False, 'requests': 0, 'cases': [],
        'limits': {'maximum_requests': 10, 'max_output_tokens': 768, 'http_timeout_seconds': 30,
                   'total_deadline_seconds': 330, 'retries': 0},
        'fixtures': 'Synthetic user excerpts, policy and unit vectors; actual BGE and manager tested separately',
        'full_B03': 'not_accepted', 'full_B08': 'not_accepted', 'production_deployment': 'not_performed'}
    secret = os.environ.get('MIA_TEST_LLM_API_KEY', '')
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(330)
    try:
        asyncio.run(evaluate(report, secret))
    except Exception as error:
        report['error_class'] = type(error).__name__
    finally:
        signal.alarm(0)
        Path(os.environ['MIA_EXTRACTION_REPORT']).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'passed': report['passed'], 'requests': report['requests'], 'cases': len(report['cases'])}))
    if not report['passed']:
        raise SystemExit('P17 bounded provider samples failed; inspect sanitized artifact')


if __name__ == '__main__':
    main()
