"""Review/apply a small, preimage-checked patch to an exact v0.9.7 checkout."""

import argparse
import ast
import difflib
import hashlib
from pathlib import Path
import shutil


PYTHON = "main/xiaozhi-server/"
JAVA = "main/manager-api/src/main/java/xiaozhi/modules/config/service/impl/ConfigServiceImpl.java"
EXPECTED = {
    PYTHON + "core/connection.py": "a1d6694197a2422d777a85cb55becb63b66278c1d23588782f39dc98a8954386",
    PYTHON + "core/handle/sendAudioHandle.py": "9b9dba36e4f110017596f975503d893d1a134071bf953ce9021020b8d0436c6e",
    PYTHON + "core/utils/dialogue.py": "a4f7af45edd5f1e60a119eb698daba56047fb6c57dfe5c4bfcadbf28234a6602",
    JAVA: "9f6a782d2c25fc015d4ffc6a799085b0439884bdadc4d0a7f47eeebd9256aac1",
    PYTHON + "core/websocket_server.py": "e46fc6a0d1b268953cd6fdcf9cb10aaf89b17f8612d611c33201a6303eb31505",
    PYTHON + "core/providers/tools/unified_tool_manager.py": "e6ce2d08b1d4932735170e6a6440506b2461311f9f2141cf65913447a57fc41b",
    PYTHON + "core/handle/intentHandler.py": "eca53dfb2dd3cfa9b02190a9257b381246efd23bdbcca24933adbcbc9d78a7a4",
    PYTHON + "core/handle/reportHandle.py": "d1598fba8b7d4e86ce6302e8f932d4a5e1c82cf7aadb177fab91f70dcb01cdf6",
}


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("Upstream hook preimage is missing or ambiguous")
    return text.replace(before, after, 1)


def patch_connection(text):
    text = replace_once(text, '        """消息路由"""\n',
                        '        """消息路由"""\n'
                        '        if await youmi_auth.route_server_control(self, message):\n            return\n')
    cleanup = '            # 确保停止事件被设置\n            if self.stop_event:\n                self.stop_event.set()\n'
    text = replace_once(text, cleanup, cleanup + '            await youmi_auth.close_connection(self)\n')
    text = replace_once(text, "from core.utils.dialogue import Message, Dialogue\n",
                        "from core.utils.dialogue import Message, Dialogue\nfrom youmi_companion import youmi_adapter as youmi\n"
                        "from youmi_companion import authorization as youmi_auth\n")
    text = replace_once(text, 'f"{self.client_ip} conn - Headers: {self.headers}"',
                        'f"{self.client_ip} conn - Header names: {sorted(self.headers)}"')
    text = replace_once(text, 'from config.config_loader import get_private_config_from_api\n', '')
    text = replace_once(text, '            private_config = await get_private_config_from_api(\n'
                        '                self.config,\n                self.headers.get("device-id"),\n'
                        '                self.headers.get("client-id", self.headers.get("device-id")),\n            )',
                        '            private_config = await youmi_auth.load_private_config(self)')
    text = replace_once(text, '            private_config["delete_audio"] = bool(self.config.get("delete_audio", True))\n',
                        '            youmi.attach_private_config(self, private_config)\n'
                        '            private_config["delete_audio"] = bool(self.config.get("delete_audio", True))\n')
    text = replace_once(text, '        except DeviceNotFoundException as e:\n',
                        '        except youmi_auth.PrivateAuthorizationRequired:\n'
                        '            self.need_bind = False\n'
                        '            self._youmi_authorization_required = True\n'
                        '            self.bind_completed_event.set()\n'
                        '            self.report_asr_enable = False\n'
                        '            self.report_tts_enable = False\n'
                        '            await youmi_auth.prompt_authorization(self)\n'
                        '            return\n'
                        '        except DeviceNotFoundException as e:\n')
    text = replace_once(text, '            # 在线程池中初始化组件\n',
                        '            if getattr(self, "_youmi_authorization_required", False):\n                return\n'
                        '            # 在线程池中初始化组件\n')
    text = replace_once(text, '        # 已经获取到真实状态，检查是否需要绑定\n',
                        '        if getattr(self, "_youmi_authorization_required", False):\n'
                        '            await youmi_auth.route_required(self, message)\n            return\n\n'
                        '        # 已经获取到真实状态，检查是否需要绑定\n')
    text = replace_once(text, '                # 超时仍未获取到真实状态，丢弃消息\n'
                        '                await self._discard_message_with_bind_prompt()\n                return',
                        '                # 身份结果未就绪时不误报为需要重新绑定。\n                return')
    text = replace_once(text, 'await asyncio.wait_for(self.bind_completed_event.wait(), timeout=1)',
                        'await asyncio.wait_for(self.bind_completed_event.wait(), timeout=4)')
    text = replace_once(text, "    def _initialize_memory(self):\n        if self.memory is None:\n",
                        "    def _initialize_memory(self):\n        if youmi.initialize_memory(self):\n            return\n        if self.memory is None:\n")
    text = replace_once(text, "            self._init_prompt_enhancement()\n",
                        "            self._init_prompt_enhancement()\n            youmi.apply_persona_prompt(self)\n")
    text = replace_once(text, '            # 打开语音合成通道\n',
                        '            youmi.attach_output_guard(self)\n            # 打开语音合成通道\n')
    text = replace_once(text, '    def change_system_prompt(self, prompt):\n        self.prompt = prompt\n',
                        '    def change_system_prompt(self, prompt):\n        self.prompt = youmi.compose_persona(self, prompt)\n')
    text = replace_once(text, '        # Define intent functions\n',
                        '        if youmi.blocked_input_reply(self, depth):\n            return\n\n'
                        '        # Define intent functions\n')
    for arguments in ('current_sentence_id, da_response', 'current_sentence_id, streamed_text',
                      'current_sentence_id, text_buff', 'self.sentence_id, text'):
        text = replace_once(text, f'self.tts.store_tts_text({arguments})', f'youmi.store_tts_text(self, {arguments})')
    text = replace_once(text, "        if isinstance(message, str):\n            await handleTextMessage(self, message)\n",
                        "        if not await youmi_auth.allow_output(self):\n            return\n"
                        "        if isinstance(message, str):\n            if await youmi.handle_message(self, message):\n                return\n            await handleTextMessage(self, message)\n")
    text = replace_once(text, '            self.dialogue.put(Message(role="user", content=query))\n',
                        '            youmi.begin_turn(self, query)\n            self.dialogue.put(Message(role="user", content=query))\n')
    text = replace_once(text, "                if emotion_flag and content is not None and content.strip():\n",
                        "                if emotion_flag and content is not None and content.strip():\n                    youmi.remember_emotion(self, content)\n")
    text = replace_once(text, "        if depth == 0:\n            self.tts.tts_text_queue.put(\n",
                        "        if depth == 0:\n            youmi.checkpoint(self)\n            self.tts.tts_text_queue.put(\n")
    text = replace_once(text, '    def chat(self, query, depth=0):\n',
                        '    def chat(self, query, depth=0):\n'
                        '        if depth > 0 and youmi.output_was_blocked(self):\n            return\n'
                        '        if not youmi_auth.allow_generation(self):\n            return\n')
    text = replace_once(text, '                if self.client_abort:\n                    break\n',
                        '                if self.client_abort or youmi.output_was_blocked(self):\n                    break\n')
    text = replace_once(text, '        # 处理function call\n',
                        '        if youmi.finish_blocked_output(self):\n'
                        '            if hasattr(llm_responses, "close"):\n                llm_responses.close()\n'
                        '            return\n\n        # 处理function call\n')
    text = replace_once(text, 'from config.manage_api_client import DeviceNotFoundException, DeviceBindException, generate_and_save_chat_title',
                        'from config.manage_api_client import DeviceNotFoundException, DeviceBindException')
    text = replace_once(text, '                            generate_and_save_chat_title(self.session_id)',
                        '                            youmi_auth.save_chat_title(self)')
    text = replace_once(text, '                        self.func_handler.handle_llm_function_call(\n'
                        '                            self, tool_call_data\n                        ),',
                        '                        youmi_auth.execute_tool(self, tool_call_data),')
    start = text.index('            # 使用带记忆的对话\n')
    end = text.index('        except Exception as e:\n', start)
    original = text[start:end]
    text = replace_once(text, original,
        '            if youmi.uses_context(self):\n'
        '                llm_responses = youmi.context_response(self, functions, depth)\n'
        '            else:\n' + ''.join('    ' + line if line.strip() else line for line in original.splitlines(True)))
    for anchor in ('            self.logger.bind(tag=TAG).error(f"LLM 处理出错 {query}: {e}")\n',
                   '            self.logger.bind(tag=TAG).error(f"LLM stream processing error: {e}")\n'):
        text = replace_once(text, '        except Exception as e:\n' + anchor,
            '        except (youmi.ContextError, youmi_auth.PrivateAuthorizationRequired) as e:\n'
            '            youmi.context_failure(self, e)\n            return\n'
            '        except Exception as e:\n' + anchor)
    forced = ('            self.dialogue.put(\n                Message(\n                    role="user",\n'
              '                    content="[系统提示] 已达到最大工具调用次数限制，请你基于目前已经获取的所有信息，直接给出最终答案。不要再尝试调用任何工具。",\n'
              '                )\n            )\n')
    text = replace_once(text, forced, '            if not youmi.uses_context(self):\n'
        + ''.join('    ' + line for line in forced.splitlines(True)))
    text = replace_once(text, '        # 如果使用 nointent，直接返回\n',
        '        if youmi.uses_context(self) and intent_type == "intent_llm":\n'
        '            intent_type = self.intent_type = "function_call"\n\n'
        '        # 如果使用 nointent，直接返回\n')
    text = replace_once(text, '                if text is not None and len(text) > 0:\n',
        '                if text is not None:\n')
    text = replace_once(text, '    def chat(self, query, depth=0):\n',
                        '    @youmi.completed_chat\n    def chat(self, query, depth=0):\n')
    text = replace_once(text, '            if self.memory:\n',
                        '            if self.memory and not isinstance(self.memory, youmi.YoumiMemoryProvider):\n')
    for anchor in ('            self.logger.bind(tag=TAG).error(f"LLM 处理出错 {query}: {e}")\n',
                   '            self.logger.bind(tag=TAG).error(f"LLM stream processing error: {e}")\n'):
        text = replace_once(text, anchor, '            youmi.incomplete_turn()\n' + anchor)
    return text


def patch_tts(text):
    text = replace_once(text, "from core.utils import textUtils\n",
                        "from core.utils import textUtils\nfrom youmi_companion import youmi_adapter as youmi\n"
                        "from youmi_companion import authorization as youmi_auth\n")
    text = replace_once(text, '    packet_index = flow_control.get("packet_count", 0)\n',
                        '    if not await youmi_auth.allow_output(conn):\n        return\n'
                        '    packet_index = flow_control.get("packet_count", 0)\n')
    text = replace_once(text, '    message = {"type": "tts", "state": state, "session_id": conn.session_id}\n',
                        '    if not await youmi_auth.allow_output(conn):\n        return\n'
                        '    message = {"type": "tts", "state": state, "session_id": conn.session_id}\n')
    return replace_once(text, '    if text is not None:\n        message["text"] = textUtils.check_emoji(text)\n',
                        '    if text is not None:\n        message["text"] = textUtils.check_emoji(text)\n'
                        '    if state == "sentence_start":\n        message.update(youmi.tts_emotion(conn, text))\n')


def patch_dialogue(text):
    text = replace_once(text, "from datetime import datetime\n",
                        "from datetime import datetime\nfrom youmi_companion.youmi_adapter import bounded_history, guard_history\n")
    text = replace_once(text, '    def put(self, message: Message):\n        self.dialogue.append(message)\n',
                        '    def put(self, message: Message):\n        guard_history(self, message)\n        self.dialogue.append(message)\n')
    text = replace_once(text, '                    f"<memory>\\n{memory_str}\\n</memory>",\n',
                        '                    lambda _: f"<memory>\\n{memory_str}\\n</memory>",\n')
    return replace_once(text, "        actual_messages = [m for m in non_system_messages if not m.is_temporary]\n",
                        "        actual_messages = [m for m in non_system_messages if not m.is_temporary]\n"
                        "        if memory_str and memory_str.startswith('{\"youmi_context\":'):\n"
                        "            actual_messages = bounded_history(actual_messages)\n")


def patch_manager(text):
    return replace_once(text, '        result.put("chat_history_conf", chatHistoryConf);\n',
                        '        result.put("chat_history_conf", chatHistoryConf);\n'
                        '        result.put("mia_owner", "user:" + agent.getUserId() + ":agent:" + agent.getId());\n')


def patch_websocket(text):
    text = replace_once(text, 'import websockets\n', 'import websockets\nfrom youmi_companion import authorization as youmi_auth\n')
    return replace_once(text, '            ping_interval=None,\n',
                        '            ping_interval=None,\n            ssl=youmi_auth.server_tls_context(),\n')


def patch_tools(text):
    text = replace_once(text, 'from typing import Dict, List, Optional, Any\n',
                        'from typing import Dict, List, Optional, Any\nfrom youmi_companion import authorization as youmi_auth\n')
    return replace_once(text, '        """执行工具调用"""\n',
                        '        """执行工具调用"""\n        await youmi_auth.require_connection(self.conn)\n')


def patch_report(text):
    text = replace_once(text, 'from config.manage_api_client import report as manage_report',
                        'from youmi_companion import authorization as youmi_auth')
    text = replace_once(text, '        if audio_data:\n',
                        '        await youmi_auth.require_connection(conn, "memory:write")\n        if audio_data:\n')
    return replace_once(text, '        await manage_report(\n', '        await youmi_auth.report_chat(\n            conn,\n')


def patch_intent(text):
    text = replace_once(text, 'import asyncio\n', 'import asyncio\nfrom youmi_companion import authorization as youmi_auth\n')
    text = replace_once(text, '    """使用LLM分析用户意图"""\n',
                        '    """使用LLM分析用户意图"""\n    await youmi_auth.require_connection(conn)\n')
    text = replace_once(text, '    if conn.intent_type == "function_call":\n',
        '    if isinstance(getattr(conn, "_youmi_scope", None), youmi_auth.AuthenticatedScope):\n'
        '        return False  # All personalized generation uses ConnectionHandler.chat.\n'
        '    if conn.intent_type == "function_call":\n')
    text = replace_once(text, 'conn.intent.replyResult(context_prompt, original_text)',
                        'youmi_auth.intent_reply(conn, context_prompt, original_text)')
    return replace_once(text, 'conn.intent.replyResult(text, original_text)',
                        'youmi_auth.intent_reply(conn, text, original_text)')


def patch_app(text):
    text = replace_once(text, 'from core.websocket_server import WebSocketServer\n',
                        'from core.websocket_server import WebSocketServer\nfrom youmi_companion import youmi_adapter as youmi\n')
    text = replace_once(text, '    ws_server = WebSocketServer(config)\n',
                        '    ws_server = WebSocketServer(config)\n    await youmi.start_worker(ws_server)\n')
    cleanup = '''        # 停止全局GC管理器
        await gc_manager.stop()

        # 取消所有任务（关键修复点）
        stdin_task.cancel()
        ws_task.cancel()
        if ota_task:
            ota_task.cancel()

        # 等待任务终止（必须加超时）
        await asyncio.wait(
            [stdin_task, ws_task, ota_task] if ota_task else [stdin_task, ws_task],
            timeout=3.0,
            return_when=asyncio.ALL_COMPLETED,
        )
'''
    return replace_once(text, cleanup,
        '        await youmi.stop_service(ws_server, gc_manager, [stdin_task, ws_task, ota_task], timeout=20)\n')


def patch_http(text):
    text = replace_once(text, 'from aiohttp import web\n',
                        'from aiohttp import web\nfrom youmi_companion.management import ManagementHandler\n'
                        'from youmi_companion.astrbot_api import register_routes as register_astrbot_routes\n')
    return replace_once(text, '                app = web.Application()\n',
                        '                app = web.Application()\n'
                        '                register_astrbot_routes(app, self.config)\n'
                        '                app.router.add_post("/youmi/management", ManagementHandler(self.config).handle)\n'
                        '                app.router.add_post("/mia/management", ManagementHandler(self.config).handle)\n')


PATCHERS = dict(zip(EXPECTED, [patch_connection, patch_tts, patch_dialogue, patch_manager,
                              patch_websocket, patch_tools, patch_intent, patch_report]))
EXPECTED[PYTHON + "app.py"] = "65c37299c510128006b658de6858492a4c8df044b26ed6a83bc3eba1b9d1a77b"
PATCHERS[PYTHON + "app.py"] = patch_app
EXPECTED[PYTHON + "core/http_server.py"] = "d136a04d70562ddcf64be3fc97d2575f312e1af1f6831947b3066d39869ff1d4"
PATCHERS[PYTHON + "core/http_server.py"] = patch_http


def build_changes(root):
    changes = {}
    for relative, digest in EXPECTED.items():
        path = root / relative
        original = path.read_text(encoding="utf-8")
        normalized = original.rstrip("\n") + "\n"
        if hashlib.sha256(normalized.encode()).hexdigest() != digest:
            raise ValueError(f"Refusing unreviewed source: {relative}; requires clean youmi v0.9.7")
        changed = PATCHERS[relative](original)
        if relative.endswith(".py"):
            ast.parse(changed, filename=relative)
        changes[relative] = (original, changed)
    return changes


def install(root, changes):
    destination = root / PYTHON / "youmi_companion"
    if destination.exists():
        raise ValueError("youmi_companion already exists; review and restore the previous installation first")
    backups = [root / (relative + ".mia-original") for relative in changes]
    if any(path.exists() for path in backups):
        raise ValueError("Original backups already exist; refusing to overwrite them")
    for relative in changes:
        shutil.copy2(root / relative, root / (relative + ".mia-original"))
    destination.mkdir(mode=0o700)
    try:
        for name in ["__init__.py", "memory.py", "youmi_adapter.py", "authorization.py", "schema.py", "schema.sql", "schema-v2.sql", "persona.py",
                     "embedding.py", "semantic.py", "retrieval.py", "memory_runtime.py", "context.py", "reply.py", "jobs.py", "extraction.py", "proposal.py", "model_requests.py",
                     "events.py", "emotions.py", "schema-v3.sql", "schema-v4.sql", "schema-v5.sql", "schema-v6.sql", "affect.py", "features.py", "management.py", "character.py", "schema-v7.sql",
                     "request_accounting.py", "requirements-vector.txt", "schema-v8.sql", "schema-v9.sql", "schema-v10.sql", "emotional_system.py", "astrbot_api.py",
                     "astrbot_identity.py", "astrbot_turns.py", "astrbot_media.py",
                     "memory_repository.py", "memory_shadow.py", "memory_search.py", "memory_state.py", "memory_reflection.py", "schema-v11.sql", "schema-v12.sql", "memory_artifacts.py",
                     "schema-v13.sql", "memory_reflection_policy.py", "reflection_prompt.py",
                     "schema-v14.sql", "memory_emotion.py", "memory_emotion_context.py", "memory_management.py", "memory_index_cache.py", "memory_cache_invalidations.py", "schema-v15.sql",
                     "requirements-memory-shadow.txt", "YOUMI_MEMORY_NOTICE.md", "YOUMI_MEMORY_LICENSE"]:
            shutil.copy2(Path(__file__).parent / name, destination / name)
        for relative, (_, changed) in changes.items():
            (root / relative).write_text(changed, encoding="utf-8")
    except OSError:
        for relative in changes:
            shutil.copy2(root / (relative + ".mia-original"), root / relative)
        shutil.rmtree(destination)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream", type=Path, help="Full xiaozhi-esp32-server v0.9.7 repository")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Validate and print the proposed diff; write nothing")
    mode.add_argument("--apply", action="store_true", help="Copy adapter, back up originals and apply the checked patch")
    args = parser.parse_args()
    try:
        changes = build_changes(args.upstream)
        if args.apply:
            install(args.upstream, changes)
            print("Installed Youmi companion hooks. Rebuild/restart manager-api and xiaozhi-server; existing databases were not changed.")
        else:
            for relative, (original, changed) in changes.items():
                print("".join(difflib.unified_diff(original.splitlines(True), changed.splitlines(True),
                                               fromfile="a/" + relative, tofile="b/" + relative)), end="")
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
