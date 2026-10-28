from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

from server.companion import shared_history
from server.companion.memory import MemoryStore
from server.companion.schema import migrate


class SharedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1800000000
        self.store = MemoryStore(Path(self.temp.name) / 'history.sqlite3', clock=lambda: self.now)
        self.owner = 'user:1:agent:a'

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def record(self, turn, channel, user='用户原话', assistant='悠米回答', owner=None):
        with self.store.lock, self.store.db:
            return shared_history.record_locked(self.store, owner or self.owner, turn, channel, user, assistant)

    def test_cross_channel_order_isolation_and_id_only_snapshot(self):
        self.record('q1', 'qq', 'QQ问话', 'QQ回答')
        self.record('p1', 'passport', '设备问话', '设备回答')
        self.record('q2', 'qq', '新的问话', '新的回答')
        self.record('other', 'qq', owner='user:2:agent:b')
        snapshot = shared_history.capture(self.store, self.owner, 'passport')
        self.assertNotIn('QQ问话', str(snapshot))
        rows = shared_history.render(self.store, self.owner, snapshot)
        self.assertEqual([row['user_text'] for row in rows], ['QQ问话', '新的问话'])
        other = shared_history.render(self.store, self.owner, shared_history.capture(self.store, self.owner, 'qq'))
        self.assertEqual([row['user_text'] for row in other], ['设备问话'])

    def test_exact_expiry_invalidates_frozen_snapshot(self):
        self.record('q1', 'qq')
        snapshot = shared_history.capture(self.store, self.owner, 'passport')
        self.now += shared_history.RETENTION - 1
        self.assertEqual(len(shared_history.render(self.store, self.owner, snapshot)), 1)
        self.now += 1
        with self.assertRaisesRegex(ValueError, 'shared_history_changed'):
            shared_history.render(self.store, self.owner, snapshot)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_shared_history_turns').fetchone()[0], 0)

    def test_pause_clear_and_resume_invalidate_previous_history(self):
        self.record('q1', 'qq')
        snapshot = shared_history.capture(self.store, self.owner, 'passport')
        shared_history.configure(self.store, self.owner, paused=True)
        with self.assertRaisesRegex(ValueError, 'shared_history_changed'):
            shared_history.render(self.store, self.owner, snapshot)
        self.assertFalse(self.record('q2', 'qq'))
        paused = shared_history.capture(self.store, self.owner, 'passport')
        self.assertEqual(shared_history.render(self.store, self.owner, paused), [])
        shared_history.configure(self.store, self.owner, paused=False, clear=True)
        with self.assertRaisesRegex(ValueError, 'shared_history_changed'):
            shared_history.render(self.store, self.owner, paused)
        self.assertEqual(shared_history.render(self.store, self.owner,
            shared_history.capture(self.store, self.owner, 'passport')), [])

    def test_deduplication_and_secrets(self):
        self.assertTrue(self.record('q1', 'qq'))
        self.assertFalse(self.record('q1', 'qq'))
        with self.assertRaisesRegex(ValueError, 'shared_history_turn_conflict'):
            self.record('q1', 'qq', '篡改')
        self.assertFalse(self.record('secret', 'qq', 'password=hidden'))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_shared_history_turns').fetchone()[0], 1)

    def test_context_limit_keeps_recent_complete_turns(self):
        for number in range(20):
            self.record('q' + str(number), 'qq', '一' * 700, str(number))
        snapshot = shared_history.capture(self.store, self.owner, 'passport')
        rows = shared_history.render(self.store, self.owner, snapshot)
        self.assertLess(len(rows), 20)
        self.assertEqual(rows[-1]['assistant_text'], '19')
        self.assertTrue(all(row['user_text'] == '一' * 700 for row in rows))

    def test_concurrent_append_does_not_rewrite_frozen_snapshot(self):
        self.record('q1', 'qq', '第一轮', '第一个回复')
        snapshot = shared_history.capture(self.store, self.owner, 'passport')
        self.record('q2', 'qq', '同时发生', '第二个回复')
        self.assertEqual([row['user_text'] for row in shared_history.render(self.store, self.owner, snapshot)], ['第一轮'])
        latest = shared_history.capture(self.store, self.owner, 'passport')
        self.assertEqual([row['user_text'] for row in shared_history.render(self.store, self.owner, latest)],
                         ['第一轮', '同时发生'])

    def test_summary_only_for_older_turns_and_invalidated_with_sources(self):
        for number in range(15):
            self.record('q' + str(number), 'qq', '用户' + str(number), '回复' + str(number))
        snapshot = shared_history.capture(self.store, self.owner, 'passport')
        self.assertEqual(len(snapshot['ids']), 12)
        self.assertEqual(len(snapshot['summary_ids']), 3)
        sources = shared_history.summary_sources(self.store, self.owner, snapshot)
        self.assertEqual([row['user_text'] for row in sources], ['用户0', '用户1', '用户2'])
        self.assertIsNone(shared_history.cached_summary(self.store, self.owner, 'passport', snapshot))
        self.assertTrue(shared_history.save_summary(self.store, self.owner, 'passport', snapshot, '之前讨论了三个话题。'))
        self.assertEqual(shared_history.cached_summary(self.store, self.owner, 'passport', snapshot), '之前讨论了三个话题。')
        with self.assertRaisesRegex(ValueError, 'invalid_shared_history_summary'):
            shared_history.save_summary(self.store, self.owner, 'passport', snapshot, 'password=secret')
        shared_history.configure(self.store, self.owner, clear=True)
        with self.assertRaisesRegex(ValueError, 'shared_history_changed'):
            shared_history.cached_summary(self.store, self.owner, 'passport', snapshot)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_shared_history_summaries').fetchone()[0], 0)

    def test_summary_expires_with_earliest_source(self):
        self.record('q0', 'qq')
        self.now += 5
        for number in range(1, 14):
            self.record('q' + str(number), 'qq')
        snapshot = shared_history.capture(self.store, self.owner, 'passport')
        shared_history.save_summary(self.store, self.owner, 'passport', snapshot, '先前对话摘要。')
        self.now += shared_history.RETENTION - 5
        with self.assertRaisesRegex(ValueError, 'shared_history_changed'):
            shared_history.cached_summary(self.store, self.owner, 'passport', snapshot)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM youmi_shared_history_summaries').fetchone()[0], 0)

    def test_v18_to_v19_failure_rolls_back_and_old_loader_rejects(self):
        with self.store.lock, self.store.db:
            self.store.db.execute('DROP TABLE youmi_shared_history_summaries')
            self.store.db.execute('DROP TABLE youmi_shared_history_turns')
            self.store.db.execute('DROP TABLE youmi_shared_history_settings')
            self.store.db.execute('DELETE FROM mia_schema_migrations WHERE version=19')
        before = list(self.store.db.iterdump())
        self.store.db.set_authorizer(lambda action, name, *args: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TABLE and name == 'youmi_shared_history_turns' else sqlite3.SQLITE_OK)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                migrate(self.store.db)
        finally:
            self.store.db.set_authorizer(None)
        self.assertEqual(list(self.store.db.iterdump()), before)
        migrate(self.store.db)
        source = subprocess.check_output(['git', 'show', '12d42ad72b8d6980eba6c63d4735224b760cbac5:server/companion/schema.py'], text=True)
        old = {'__file__': str(Path('server/companion/schema.py').resolve())}
        exec(compile(source, 'frozen-v18.py', 'exec'), old)
        with self.assertRaisesRegex(ValueError, 'version or checksum'):
            old['migrate'](self.store.db)
        self.assertEqual(self.store.db.execute('PRAGMA quick_check').fetchone()[0], 'ok')


if __name__ == '__main__':
    unittest.main()
