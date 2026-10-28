from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from server.companion import shared_history, shared_history_summary
from server.companion.authorization import AuthenticatedScope, PrivateAuthorizationRequired
from server.companion.context import ContextError
from server.companion.memory import MemoryStore


class SharedSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp.name) / 'memory.sqlite3')
        self.scope = AuthenticatedScope('user:1:agent:a', 1, 1, 'a', 'mia', 'device', 'client', 1, 1,
            datetime.now(timezone.utc) + timedelta(days=1), frozenset({'memory:read'}))
        self.auth = SimpleNamespace(require=AsyncMock())
        self.models = {'selected_module': {'LLM': 'ci'}, 'LLM': {'ci': {'type': 'ci'}}}
        for index in range(15):
            with self.store.lock, self.store.db:
                shared_history.record_locked(self.store, self.scope.owner, 'q' + str(index), 'qq',
                                             '用户说' + str(index), '悠米回复' + str(index))
        self.snapshot = shared_history.capture(self.store, self.scope.owner, 'passport')
        self.create = Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='之前讨论了三个话题。'))]))
        self.client = SimpleNamespace(with_options=Mock(return_value=SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=self.create)))), close=Mock())
        self.provider = SimpleNamespace(client=self.client, model_name='ci', _apply_thinking_disabled=Mock())
        self.factory = Mock(return_value=self.provider)
        self.modules = patch.dict(sys.modules, {'core': SimpleNamespace(), 'core.utils': SimpleNamespace(),
                                               'core.utils.llm': SimpleNamespace(create_instance=self.factory)})
        self.modules.start()

    async def asyncTearDown(self):
        self.modules.stop()
        self.store.close()
        self.temp.cleanup()

    async def test_only_overflow_calls_model_and_cache_prevents_repeat(self):
        short = dict(self.snapshot, summary_ids=[])
        self.assertIsNone(await shared_history_summary.ensure(self.store, self.scope, self.auth, self.models, 'passport', short))
        self.factory.assert_not_called()
        text = await shared_history_summary.ensure(self.store, self.scope, self.auth, self.models, 'passport', self.snapshot)
        self.assertEqual(text, '之前讨论了三个话题。')
        self.assertEqual(await shared_history_summary.ensure(self.store, self.scope, self.auth, self.models, 'passport', self.snapshot), text)
        self.assertEqual(self.create.call_count, 1)
        self.client.with_options.assert_called_once_with(max_retries=0, timeout=2)
        self.assertEqual(self.create.call_args.kwargs['max_tokens'], 400)
        self.client.close.assert_called_once()

    async def test_clear_during_model_request_prevents_resurrection(self):
        def clear(**_):
            shared_history.configure(self.store, self.scope.owner, clear=True)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='旧对话摘要'))])
        self.create.side_effect = clear
        with self.assertRaises(ContextError):
            await shared_history_summary.ensure(self.store, self.scope, self.auth, self.models, 'passport', self.snapshot)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_shared_history_summaries').fetchone()[0], 0)

    async def test_revocation_and_invalid_summary_do_not_write(self):
        self.auth.require.side_effect = [None, PrivateAuthorizationRequired()]
        with self.assertRaises(PrivateAuthorizationRequired):
            await shared_history_summary.ensure(self.store, self.scope, self.auth, self.models, 'passport', self.snapshot)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_shared_history_summaries').fetchone()[0], 0)
        self.auth.require.side_effect = None
        self.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='password=secret'))])
        self.assertIsNone(await shared_history_summary.ensure(self.store, self.scope, self.auth, self.models, 'passport', self.snapshot))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_shared_history_summaries').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
