import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from server.companion import MemoryStore
from server.companion import youmi_adapter as youmi
from server.companion.authorization import AuthenticatedScope, PrivateAuthorizationRequired
from server.companion.install_youmi import install, replace_once


def message(content, role="user", temporary=False):
    return SimpleNamespace(role=role, content=content, uniq_id=uuid.uuid4().hex,
                           is_temporary=temporary, tool_calls=None, tool_call_id=None)


class Model:
    def __init__(self):
        self.inputs = []

    def response_no_stream(self, system, user):
        data = json.loads(user)
        self.inputs.append(data)
        return json.dumps({"memories": [{"category": "preference", "content": data["user_excerpts"][0],
                                         "source_index": 0}]})


class WebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(json.loads(message))


class AuthorizationFixture:
    """Synthetic policy fixture; real manager proof runs in identity Actions."""
    def __init__(self):
        self.active = True

    async def require(self, scope, permission):
        if not self.active or not isinstance(scope, AuthenticatedScope) or permission not in scope.permissions:
            raise PrivateAuthorizationRequired()
        return scope


class CompanionAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp.name) / "memory.db")
        self.model = Model()
        self.scope = AuthenticatedScope("user:1:agent:one", 1, 1, "one", "mia", "verified-device", "client",
                                        1, 1, datetime.now(timezone.utc) + timedelta(days=1),
                                        frozenset({"memory:read", "memory:write", "memory:manage"}))
        self.authorization = AuthorizationFixture()
        self.provider = youmi.YoumiMemoryProvider(self.store, "user:1:agent:one", self.model, "session",
                                             self.scope, self.authorization)
        self.conn = SimpleNamespace(_youmi_memory=self.provider, websocket=WebSocket(),
                                    _youmi_scope=self.scope, _youmi_authorization=self.authorization,
                                    dialogue=SimpleNamespace(dialogue=[]), read_config_from_api=True,
                                    device_id="verified-device", headers={"authorization": "Bearer good", "client-id": "client"},
                                    server=SimpleNamespace(auth_enable=True,
                                        auth=SimpleNamespace(verify_token=lambda token, **kwargs: token == "good")))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def test_checkpoints_after_ten_user_turns_and_disconnect_saves_remaining(self):
        messages = [message(f"我喜欢第{i}种蓝色") for i in range(9)]
        await self.provider.checkpoint(youmi._user_excerpts(messages))
        self.assertEqual(self.model.inputs, [])
        messages.extend([message("第十次明确的偏好"), message("assistant guess", "assistant"), message("example", temporary=True)])
        await self.provider.checkpoint(youmi._user_excerpts(messages))
        self.assertEqual(len(self.model.inputs), 1)
        self.assertEqual(len(self.model.inputs[0]["user_excerpts"]), 10)
        self.assertNotIn("assistant guess", self.model.inputs[0]["user_excerpts"])
        await self.provider.checkpoint(youmi._user_excerpts(messages))
        self.assertEqual(len(self.model.inputs), 1)
        messages.append(message("这次喜欢绿色"))
        await self.provider.save_memory(messages)
        self.assertEqual(len(self.model.inputs), 2)
        self.assertEqual(self.model.inputs[1]["user_excerpts"], ["这次喜欢绿色"])

    async def test_pending_context_is_bounded_and_credentials_are_never_sent(self):
        messages = [message("我喜欢蓝色" * 200) for _ in range(30)]
        messages.extend([message("password: value"), message("ｐａｓｓｗｏｒｄ：value"),
                         message(json.dumps({"speaker": "friend", "content": "验证码是123456"})),
                         message(json.dumps({"content": "我喜欢猫"}))])
        await self.provider.save_memory(messages)
        sources = self.model.inputs[0]["user_excerpts"]
        self.assertEqual(len(sources), 20)
        self.assertEqual(sources[-1], "我喜欢猫")
        self.assertTrue(all(len(source) <= 512 for source in sources))
        self.assertTrue(all("password" not in source and "验证码" not in source for source in sources))

    async def test_legacy_bearer_and_owner_string_cannot_replace_private_scope(self):
        youmi.attach_private_config(self.conn, {"mia_owner": "user:1:agent:one"})
        self.assertEqual(self.conn._youmi_owner, "user:1:agent:one")
        for config in [{}, {"mia_owner": "client-chosen"}, {"mia_owner": "user:null:agent:one"}]:
            youmi.attach_private_config(self.conn, config)
            self.assertIsNone(self.conn._youmi_owner)
        self.conn._youmi_scope = None
        youmi.attach_private_config(self.conn, {"mia_owner": "user:1:agent:one"})
        self.assertIsNone(self.conn._youmi_owner)

    async def test_revocation_stops_lookup_management_and_background_commit(self):
        def revoke_during_model(system, user):
            self.authorization.active = False
            return Model.response_no_stream(self.model, system, user)
        self.model.response_no_stream = revoke_during_model
        await self.provider.save_memory([message("我喜欢海边")])
        self.assertEqual(self.store.list_memories(self.provider.owner), [])
        with self.assertRaises(PrivateAuthorizationRequired):
            await self.provider.query_memory("喜欢哪里")
        await youmi.handle_message(self.conn, json.dumps({"type": "mia_memory", "request_id": str(uuid.uuid4()), "action": "list"}))
        self.assertEqual(self.conn.websocket.sent[-1]["error"]["code"], "authorization_required")
        self.assertNotIn("result", self.conn.websocket.sent[-1])

    async def test_private_memory_never_falls_back_to_legacy_provider(self):
        self.conn._youmi_owner = None
        self.conn.memory = object()
        self.assertTrue(youmi.initialize_memory(self.conn))
        self.assertIsNone(self.conn.memory)
        self.conn._youmi_owner = self.scope.owner
        self.conn.llm = self.model
        self.conn.session_id = "fixture"
        with patch("server.companion.youmi_adapter._store", side_effect=OSError("fixture")):
            self.assertTrue(youmi.initialize_memory(self.conn))
        self.assertIsNone(self.conn.memory)

    async def test_latest_job_status_without_id_is_owner_scoped_and_empty_is_not_saved(self):
        request_id = str(uuid.uuid4())
        await youmi.handle_message(self.conn, json.dumps({'type': 'mia_memory', 'request_id': request_id, 'action': 'job_status'}))
        self.assertEqual(self.conn.websocket.sent[-1]['request_id'], request_id)
        self.assertEqual(self.conn.websocket.sent[-1]['result'], {'state': 'none', 'confirmation_required': False})

    async def test_management_uses_server_owner_and_clears_extractable_context(self):
        original = message("我喜欢蓝色")
        self.conn.dialogue.dialogue = [message("system", "system"), original]
        await self.provider.save_memory([original])
        memory_id = self.store.list_memories(self.provider.owner)[0]["id"]
        request_id = str(uuid.uuid4())
        request = {"type": "mia_memory", "request_id": request_id, "action": "delete",
                   "id": memory_id, "owner": "user:2:agent:other"}
        self.assertTrue(await youmi.handle_message(self.conn, json.dumps(request)))
        self.assertEqual(self.conn.websocket.sent[-1]["error"]["code"], "not_authorized")
        self.assertEqual(len(self.store.list_memories(self.provider.owner)), 1)
        request["owner"] = self.scope.owner
        self.assertTrue(await youmi.handle_message(self.conn, json.dumps(request)))
        response = self.conn.websocket.sent[-1]
        self.assertEqual(response["request_id"], request_id)
        self.assertEqual(response["result"]["memories"], [])
        self.assertEqual([m.role for m in self.conn.dialogue.dialogue], ["system"])
        await self.provider.save_memory([original])
        self.assertEqual(self.store.list_memories(self.provider.owner), [])

    async def test_management_waits_for_background_memory_initialization(self):
        self.conn._youmi_memory = None
        self.conn._youmi_owner = "user:1:agent:one"

        async def initialize():
            await asyncio.sleep(0.02)
            self.conn._youmi_memory = self.provider

        task = asyncio.create_task(initialize())
        await youmi.handle_message(self.conn, json.dumps({"type": "mia_memory", "request_id": str(uuid.uuid4()), "action": "list"}))
        await task
        self.assertIn("result", self.conn.websocket.sent[-1])

    async def test_inflight_extraction_cannot_resurrect_deleted_memory(self):
        started, release = threading.Event(), threading.Event()
        model = self.model
        def delayed(system, user):
            started.set()
            if not release.wait(3):
                raise TimeoutError("test model did not release")
            return Model.response_no_stream(model, system, user)
        model.response_no_stream = delayed
        source = message("我喜欢蓝色")
        task = asyncio.create_task(self.provider.save_memory([source]))
        self.assertTrue(await asyncio.to_thread(started.wait, 2))
        self.provider.manage({"action": "clear"}, [source])
        release.set()
        await task
        self.assertEqual(self.store.list_memories(self.provider.owner), [])
        await self.provider.save_memory([source])
        self.assertEqual(len(model.inputs), 1)

    async def test_old_connection_started_after_deletion_cannot_reextract_history(self):
        source = message("我喜欢海边")
        self.store.clear(self.provider.owner)
        await self.provider.save_memory([source])
        self.assertEqual(self.model.inputs, [])
        self.conn.dialogue.dialogue = [message("system", "system"), source]
        self.store.clear(self.provider.owner)
        youmi.begin_turn(self.conn, "这是新的话题")
        self.assertEqual([m.role for m in self.conn.dialogue.dialogue], ["system"])
        await self.provider.save_memory([message("我喜欢新的话题")])
        self.assertEqual(self.store.list_memories(self.provider.owner)[0]["content"], "我喜欢新的话题")

    async def test_failure_and_paused_memory_leave_conversation_available(self):
        self.store.set_paused(self.provider.owner, True)
        await self.provider.save_memory([message("我喜欢蓝色")])
        self.assertEqual(self.model.inputs, [])
        self.store.set_paused(self.provider.owner, False)
        self.model.response_no_stream = lambda system, user: "not json"
        await self.provider.save_memory([message("我喜欢绿色")])
        self.assertEqual(self.store.list_memories(self.provider.owner), [])
        self.assertIn("youmi_context", json.loads(await self.provider.query_memory("喜欢")))
        self.conn._youmi_memory = None
        self.conn._youmi_owner = None
        await youmi.handle_message(self.conn, json.dumps({"type": "mia_memory", "request_id": str(uuid.uuid4()), "action": "list"}))
        self.assertEqual(self.conn.websocket.sent[-1]["error"]["code"], "unavailable")

    async def test_failed_extraction_waits_for_next_checkpoint_and_does_not_loop(self):
        self.model.response_no_stream = lambda system, user: "not json"
        source = message("我喜欢海边")
        await self.provider.save_memory([source])
        self.assertEqual(list(self.provider.pending), ["我喜欢海边"])
        self.model.response_no_stream = Model.response_no_stream.__get__(self.model)
        await self.provider.save_memory([source])
        self.assertEqual(self.store.list_memories(self.provider.owner)[0]["content"], "我喜欢海边")

    async def test_unknown_wire_requests_and_invalid_ids_do_not_mutate_store(self):
        self.assertFalse(await youmi.handle_message(self.conn, '{"type":"hello"}'))
        self.assertFalse(await youmi.handle_message(self.conn, 'not-json'))
        for request_id in [None, True, "other-user", "x" * 100]:
            await youmi.handle_message(self.conn, json.dumps({"type": "mia_memory", "action": "clear", "request_id": request_id}))
            self.assertEqual(self.conn.websocket.sent[-1]["error"]["code"], "invalid_input")

    async def test_session_suppression_and_ambiguous_forgetting_are_honest(self):
        youmi.begin_turn(self.conn, "讨论“这段别记”是什么意思")
        self.assertFalse(self.provider.suppressed)
        youmi.begin_turn(self.conn, "这段别记。")
        await self.provider.save_memory([message("我喜欢蓝色")])
        self.assertEqual(self.model.inputs, [])
        youmi.begin_turn(self.conn, "忘掉这件事")
        self.assertIn("尚未删除", self.provider.notice)

    async def test_explicit_remember_checkpoints_without_waiting_ten_turns(self):
        youmi.begin_turn(self.conn, "记住：我喜欢海边")
        self.assertIn("尚未保存", self.provider.notice)
        await self.provider.checkpoint(youmi._user_excerpts([message("我喜欢海边")]))
        self.assertEqual(len(self.model.inputs), 1)

    async def test_natural_remember_request_saves_and_is_retrieved_in_next_session(self):
        for command in ["记住我喜欢海边", "请记住我喜欢海边", "米娅，请记住我喜欢海边",
                        "你好，米娅，帮我记住我喜欢海边", "记住这个"]:
            with self.subTest(command=command):
                youmi.begin_turn(self.conn, command)
                self.assertTrue(self.provider.force_checkpoint)
                self.assertIn("尚未保存", self.provider.notice)
        await self.provider.checkpoint(youmi._user_excerpts([message("我喜欢海边")]))
        next_session = youmi.YoumiMemoryProvider(self.store, self.provider.owner, self.model, "next",
                                             self.scope, self.authorization)
        context = json.loads(await next_session.query_memory("我喜欢哪里"))
        self.assertEqual(context["youmi_context"]["memories"][0]["content"], "我喜欢海边")

    async def test_discussing_or_negating_remember_does_not_force_save(self):
        for command in ["不要记住我的信息", "请不要记住", "如果我说记住这个会怎样",
                        "他说‘记住我喜欢海边’", "记住了吗", "你记住我喜欢什么了吗"]:
            with self.subTest(command=command):
                youmi.begin_turn(self.conn, command)
                self.assertFalse(self.provider.force_checkpoint)

    async def test_explicit_topic_forgetting_is_unique_and_removes_short_context(self):
        source = message("我喜欢海边")
        await self.provider.save_memory([source])
        self.conn.dialogue.dialogue = [message("system", "system"), source]
        youmi.begin_turn(self.conn, "忘掉关于海边的记忆")
        self.assertEqual(self.store.list_memories(self.provider.owner), [])
        self.assertEqual([m.role for m in self.conn.dialogue.dialogue], ["system"])
        self.assertIn("已删除", self.provider.notice)

    async def test_same_generated_emoji_drives_sentence_emotion_and_intensity(self):
        for emoji, name in [("😌", "calm"), ("🙂", "happy"), ("🤔", "curious"), ("😲", "surprised"),
                            ("🙄", "confused"), ("😍", "caring"), ("😔", "sad"), ("😜", "playful")]:
            youmi.remember_emotion(self.conn, emoji * 2)
            self.assertEqual(youmi.tts_emotion(self.conn, "普通回答"), {"emotion": name, "intensity": 0.65})
            self.assertEqual(youmi.tts_emotion(self.conn, emoji * 3 + "回答")["intensity"], 0.9)
        youmi.begin_turn(self.conn, "下一轮")
        self.assertEqual(youmi.tts_emotion(self.conn, "回答"), {"emotion": "calm", "intensity": 0.5})
        self.conn._youmi_memory = None
        self.assertEqual(youmi.tts_emotion(self.conn, "🙂回答"), {"emotion": "happy", "intensity": 0.35})
        self.conn._youmi_scope = None
        self.assertEqual(youmi.tts_emotion(self.conn, "🙂回答"), {})

    def test_fixed_persona_loads_when_memory_database_initialization_fails(self):
        from server.companion.persona import CORE
        self.conn._youmi_owner = self.scope.owner
        self.conn.llm = self.model
        self.conn.session_id = 'db-failure'
        self.conn.prompt = '当前角色偏好简短回答'
        self.conn.config = {}
        self.conn.change_system_prompt = lambda prompt: setattr(self.conn, 'prompt', youmi.compose_persona(self.conn, prompt))
        with patch('server.companion.youmi_adapter._store', side_effect=OSError('synthetic unavailable')):
            self.assertTrue(youmi.initialize_memory(self.conn))
        self.assertIsNone(self.conn.memory)
        youmi.apply_persona_prompt(self.conn)
        self.assertTrue(self.conn.prompt.startswith(CORE))
        self.assertIn('当前角色偏好简短回答', self.conn.prompt)

    def test_short_history_keeps_whole_user_and_tool_rounds(self):
        history = []
        for i in range(25):
            history.extend([message(str(i)), message("tool invocation", "assistant"), message("tool result", "tool")])
        result = youmi.bounded_history(history)
        self.assertEqual(len(result), 60)
        self.assertEqual(result[0].content, "5")
        self.assertEqual(result[-1].role, "tool")

    def test_patch_helper_requires_one_unambiguous_anchor(self):
        self.assertEqual(replace_once("before", "before", "after"), "after")
        for source in ["nothing", "before before"]:
            with self.assertRaises(ValueError):
                replace_once(source, "before", "after")

    def test_install_preserves_original_and_refuses_repeated_install(self):
        root = Path(self.temp.name) / "checkout"
        relative = "main/xiaozhi-server/core/test.py"
        source = root / relative
        source.parent.mkdir(parents=True)
        source.write_text("original\n")
        changes = {relative: ("original\n", "changed\n")}
        install(root, changes)
        self.assertEqual(source.read_text(), "changed\n")
        self.assertEqual(Path(str(source) + ".mia-original").read_text(), "original\n")
        self.assertTrue((root / "main/xiaozhi-server/youmi_companion/memory.py").exists())
        self.assertTrue((root / "main/xiaozhi-server/youmi_companion/proposal.py").exists())
        with self.assertRaises(ValueError):
            install(root, changes)

    def test_install_copy_failure_restores_original_and_keeps_backup(self):
        import shutil
        root = Path(self.temp.name) / "checkout"
        relative = "main/xiaozhi-server/core/test.py"
        source = root / relative
        source.parent.mkdir(parents=True)
        source.write_text("original\n")
        original_copy = shutil.copy2
        def fail_package(src, dest):
            if Path(src).name == "memory.py":
                raise OSError("test copy failure")
            return original_copy(src, dest)
        with patch("server.companion.install_youmi.shutil.copy2", side_effect=fail_package):
            with self.assertRaises(OSError):
                install(root, {relative: ("original\n", "changed\n")})
        self.assertEqual(source.read_text(), "original\n")
        self.assertTrue(Path(str(source) + ".mia-original").exists())
        self.assertFalse((root / "main/xiaozhi-server/youmi_companion").exists())


if __name__ == "__main__":
    unittest.main()
