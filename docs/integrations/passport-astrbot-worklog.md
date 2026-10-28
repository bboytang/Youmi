# Passport 与 AstrBot 工作记录

## 2026-10-28：基线与范围

- 用户要求实施设备语音接入 AstrBot、QQ 与设备跨端续聊，并保存方案、工作记录和交接；共享近期内容保留 7 天，既有 QQ 历史只导入可确认授权及隐私状态的部分。
- 基线分支 `feature/emotional-system`，HEAD `efb2b378`；工作区已有大量修改和未跟踪文件，必须保留。当前源码迁移列至 V18。
- 历史记录和先前只读生产日志显示 Passport 曾报告 1.0.6；不将其当作本次语音、工具或新链路验收。
- 原 AstrBot 连接器与桥接身份限制 QQ；现有“这段别记”不等于清除 AstrBot 原有历史。新增共享记录及摘要尚未实现。
- 状态：仅开始基线审查及文档记录；代码、部署、GitHub Actions 和实机验收均未完成。

## 实现边界核查

- 只读核查生产容器：语音镜像 `youmi-voice:autoadmin-5ad2a240-active`，AstrBot `v4.29.3` 显示 healthy；这是容器状态，不是设备端到端验收。
- `PrivateAuthorization` 使用 manager 对真实设备凭据校验及 Scope 复核；AstrBot `BridgeIdentity` 固定要求 QQ 映射和 `astrbot-` 虚拟设备，现有 manager 的 bridge-config 也限定虚拟身份。硬件必须有独立身份契约，不能伪造 QQ 来源。
- 原硬件 `completed_chat` 与 AstrBot `TurnLedger.complete` 都可能提交情绪、事件和记忆；直接串接会重复产生效果，须在接入时明确唯一所有者和交付边界。
- AstrBot 插件中的原生事件和发送路径是 QQ 特定的，Web 工具插件同样要求 QQ 平台；设备 MCP 与这些插件的适配尚未实现。既有完成账本在终态清除上下文载荷，不提供七天共享历史。
- 数据库版本迁移带源码与 schema 校验；共享历史必须设计增量迁移和兼容回退，不能直接连接不认识新版本的旧运行镜像。

## 一次性设备凭证基础

- 新增 `server/companion/passport_tickets.py`：进程内短期、限容量、单次兑换的回合凭证；绑定已认证 Scope、连接、会话和句子，签发及兑换时向现有授权对象复核 `memory:read`，可在后续工具和输出前再次复核。兑换先原子移除凭证，再做异步授权检查，失败不恢复，避免并发重复消费。
- 本地 `python3 -m unittest discover -s server/tests -p test_passport_tickets.py -v`：6 项通过，覆盖并发兑换、过期、撤销、断开/中断状态、回合或 Scope 变化、验证期间变化、容量上限和非私有连接。为单元 fixture，未连接生产设备。
- 既有 `test_companion_authorization.py` 回归尝试在 `test_connection_session_is_not_shared_with_another_event_loop` 等待超过 30 秒，被外层 `timeout` 终止（退出码 124）；此前两项通过，整个文件未完成，原因待查。不得计为通过。
- 该模块尚未注册任何 HTTP 接口、接入设备处理或 AstrBot，也未加入运行时制品；没有改变生产。下一步设计经过认证的设备事件端到端交换及实际会话关闭生命周期，再将模块接到入口并补真实管线测试。

## 服务端句柄入口（尚未上线）

- 增加受现有服务 Bearer 和 TLS/受控内网规则约束的 `passport-open`、`passport-check` 和 `passport-close`；一次性票据兑换成最长 300 秒的进程内回合句柄。每次检查均通过原连接的授权对象复核，绑定 Scope、授权对象、session、sentence，并检查 stop_event/中断。认证令牌和句柄不进入响应日志。
- `install_youmi.py` 与离线旧版安装器已包含模块；该基础尚无设备端签发调用、AstrBot 事件入口、设备 MCP、共享历史或生产部署。
- 本地回环 HTTP 和凭证测试 11 项通过；原 QQ 桥接的服务认证、身份映射和单次完成 3 项通过；离线安装器 2 项通过。沙箱内首次 HTTP 测试因禁止 socket 返回 `Operation not permitted`，在授权的本地回环环境重跑通过。尚无精确提交 GitHub Actions 或实机证据。

## 设备事件交接队列（尚未上线）

- 增加 `passport_dispatch.py`：受认证 Scope 及当前连接/回合约束的内存队列，容量默认 32，排队语音文本最长驻留 30 秒，回合去重保留 300 秒并受容量约束；一条事件只交付一个消费者，不自动重放。`passport-next` 复用既有服务 Bearer 和 TLS/受控内网策略，读取前复核当前设备授权，响应禁止缓存，不写用户文本日志。
- 模块已加入两种离线安装路径。本地隔离回环及凭证/队列测试 15 项通过，包含设备授权、服务令牌、重复提交、容量、过期和中断；离线安装器 2 项再次通过，`git diff --check` 与 Python 编译通过。实际设备尚未向队列提交，AstrBot 尚无消费者或回复回送；未部署、未运行 GitHub Actions，也未实机验证。

## 设备语音入口与回复回传（默认关闭）

- 设备语音识别后的顶层 chat 在 `begin_turn` 和旧记忆提取前检查服务端 `YOUMI_PASSPORT_ASTRBOT_DEVICES` JSON 数组；仅经授权且设备 ID 精确匹配时进入新路径。没有配置时保持旧路径，未配置到生产。
- 新路径先执行原 PersonaGuardrail 输入检查，再通过队列等待 Agent 回复；恢复时重新核验同一连接、Scope、授权对象和回合，并通过原输出检查和 TTS 队列播放。终止或更换回合会取消等待，不重复提交旧路径的记忆或情绪。回传接口在成功排队给设备工作线程时确认接收，不代表音频已经播放或已提交记忆。
- 当前仅 fixture 接入回复，AstrBot 事件消费者、实体设备上下文、完整记忆/情绪完成、共享历史、真实 MCP、GitHub CI 和生产实机均未完成。不能开启开关冒充完整链路。
- 22 项 Passport 定向测试、24 项既有语音适配器测试、13 项既有授权测试和 2 项离线安装器测试本地通过；真实 upstream 固定源码补丁通过语法及入口顺序检查。先前受沙箱限制卡住的授权测试在授权环境重跑 13 项通过；仍非 GitHub Actions 或实体设备验证。

## 实体设备上下文与 AstrBot 事件（候选，未上线）

- 管理端新增实体设备配置路径：必须持有服务端身份、当前真实设备 Scope 和物理 MAC 设备绑定，虚拟 AstrBot 设备被拒；QQ 原有虚拟配置路径保持独立。隔离本地 act manager 终态成功，195 项 Java 测试通过并完成 JAR 打包；日志 `/tmp/youmi-passport-ci/manager.log`。这是固定候选的本地证据，不是同 SHA GitHub Actions。
- `passport-prepare` 不接受调用方传入 owner 或语音原文；从已兑换的活跃设备回合读取原文、复核真实 Scope，沿现有 ContextBuilder、角色、情绪和回合账本构建上下文。`passport-reply` 要求匹配已认领的 turn token、completion ID 和文本，在回复入设备等待队列前复核权限、角色及上下文版本；重复回复拒绝，未收到队列接收凭证不能提交成功的记忆/情绪效果。发送给 TTS 队列不等于实体设备已播放。
- 新增默认关闭的 AstrBot Passport 事件消费者，使用原生事件和工具循环；设备只能单次发送已认领最终文本，图片等不能展示时用文字说明，拒绝提前发送工具造成重复回答。固定 AstrBot 原生 Agent 的两次工具续答 fixture 和 QQ 兼容测试共 45 项通过（有 audioop 弃用警告）；设备服务、身份、上下文及 HTTP 定向测试 29 项通过。首次设备上下文测试错误地把 role_data 当 system 字段，修正断言后通过。
- 仍缺实体设备 MCP、跨设备 7 天历史/摘要、真实 Agent 端到端与实机、精确 SHA GitHub Actions、部署及回退验收；上述测试不代表线上可用，保持两个设备开关关闭。

## 设备 MCP 候选（未上线）

- AstrBot 事件只接收当前已授权 Passport 连接的设备端 MCP 工具定义；工具名采用有界映射，不能调用服务端其他类型的工具。执行前后复核设备连接、Scope、角色和回合版本；按回合与调用 ID 拒绝重复执行，设备能力变化或授权撤销时停止。请求超时后的设备动作结果可能不确定，不自动重试。
- 固定 AstrBot 的原生工具执行器 fixture 已验证设备工具注入；本地 32 项 Passport 服务/HTTP 与 46 项固定 AstrBot/QQ 插件测试通过（audioop 弃用警告保留）。这些是隔离模拟，不是实体设备控制或完整 Agent 端到端验证。
- 下一步：实现 7 天共享历史、摘要与隐私策略；随后完成真实服务/Agent 组合测试、GitHub Actions、设备实机和部署。两个新入口默认关闭，生产未变。

## 跨端近期历史与摘要候选（未上线）

- 新增 V19 共享历史及摘要表：只在已有完成账本成功提交时记录同一 owner 的已交付、非禁记文本；QQ 和 Passport 准备回合时只加载另一端已完成回合，按提交顺序最多取最近 12 轮、12 KB。快照仅保留记录 ID 和版本，暂停、清除、到期或授权变化会使正在处理的旧快照失效。
- 保存期限为 7 天；运行中的清理任务按最近到期时间清理原文和关联摘要。服务停止期间不会运行清理，重启后会先清理。超出近期预算时最多选取紧接其前的 8 轮、24 KB 生成有界摘要，按来源和到期时间缓存；生成超时、不可用或结果不合格时仅使用近期原文，更早内容不保证进入上下文。摘要视为不可信参考，不是指令。
- 支持“暂停跨设备对话共享”“恢复跨设备对话共享”“清除跨设备对话记录”；现有会话禁记仍阻止该会话写入共享记录。清除只影响共享记录和摘要，不删除 QQ 原始历史、长期记忆或笔记。
- 历史导入核查：固定 AstrBot 的 `platform_message_history` 记录平台、用户、发送者、内容及 checkpoint，但未提供可与 Youmi 完成账本可靠逐条关联的消息 ID；Youmi 已完成账本清除临时原文，保留哈希和状态。现有证据不能同时证明原始对话的交付与禁记状态，不能用时间或文本猜测关联，因此没有导入不可确认的旧片段；新的成功回合开始积累共享历史。
- 本地 Passport 服务和 HTTP 测试 37 项、跨端历史、摘要及迁移测试 12 项通过；涵盖双向隔离、重复、七天到期、清除/暂停失效、来源绑定和摘要生成期间清除。Passport 与 QQ 组合 fixture 已验证双向近期衔接和模型摘要缓存，仍非完整线上 Agent/设备测试。首次使用系统 Python 的测试缺少依赖且线程环境受限，已中止；使用项目测试虚拟环境和允许回环/线程的环境重跑，结果以重跑为准。
- 受限网络中 GitHub CLI 曾报告凭据无效；在允许联网环境复核后授权有效。目前没有精确提交的 GitHub Actions 结果。尚未部署 V19 或启用设备开关，不能宣称实机链路已打通。

## 服务与原生 Agent 组合验证（本地模拟）

- 新增 `integrations/tests/test_passport_end_to_end.py`，通过真实本地 HTTP 客户端、服务端认证/队列/设备回合、固定 AstrBot 原生 `ToolLoopAgentRunner` 和 `FunctionToolExecutor`，验证 Passport 事件被消费、Agent 调用绑定设备 MCP、回复送达设备等待方，并恰好生成一次完成任务和共享记录。
- 测试中的设备连接、设备 MCP 执行器、模型及 manager 仍为 fixture，不涵盖真实 ESP32 音频采集、TTS 播放、网络或实机动作。与 Passport 插件和固定 AstrBot/QQ 回归一起运行，本地 47 项通过，存在 upstream `audioop` 弃用警告；不是 GitHub Actions。
- 远端 `feature/emotional-system` 经只读查询与本地基线 `efb2b378` 一致；当前未创建验证提交。Passport 涉及的插件及服务文件还包含本任务之前的管理端等未提交修改，须在隔离提交中厘清依赖，不能直接把整个工作区当作本次变更提交或覆盖原有修改。
- 首次 CI 同款本地后端全量运行：809 项中 3 项失败、57 项跳过，三个失败均为旧迁移测试仍断言当前迁移数为 18；检查堆栈后将最新版本断言更新为 19，保留旧版本迁移和回滚断言，已开始完整重跑。首次失败记录保留在 `/tmp/youmi-passport-backend.log`。
- 修正版本断言后重跑，后端服务 809 项通过（57 项跳过）；后续脚本测试因本地直接调用虚拟环境解释器却未将其 `bin` 加入 PATH，6 个子进程返回 127，整条脚本当次失败。按 CI 环境补全 PATH 后独立重跑：固件脚本 98 项、CI 辅助脚本 39 项通过，Compose 配置检查通过。记录 `/tmp/youmi-passport-backend-rerun.log`、`/tmp/youmi-passport-scripts.log` 和 `/tmp/youmi-passport-ci-tests.log`；这些仍为本地检查，非精确提交的 GitHub Actions。

## 隔离验证候选

- 发现原工作区基线之后的管理功能已提交并推送于 `codex/autonomous-admin`，最新基线 `12d42ad72b8d6980eba6c63d4735224b760cbac5`。在 `/tmp/youmi-passport-candidate-20261028` 从该基线构造隔离候选，只叠加 Passport、共享历史及对应测试和文档；原工作区中尚未提交的图片压缩等无关修改不包含在候选中，原工作区未被重置。
- 隔离候选本地回归：固定 AstrBot/Passport 插件 45 项通过；Passport 服务 37 项及共享历史/摘要 12 项通过。较原工作区插件的 47 项少两项无关图片压缩测试。尚需候选精确提交的 GitHub Actions 和真实设备验收。
- 隔离提交 `7d4fd5bcf28675c74123fefb6b2b6c6e001572a4` 已在候选目录创建并检查差异；推送到公开 GitHub 新分支的请求被自动审批拒绝，理由是虽然用户要求精确提交的 Actions 验证，但未明确授权将这些具体代码与文档公开发布。未采用绕过方式；GitHub Actions 因此尚未启动。继续在隔离提交上执行本地 CI 同款检查。
- 对隔离提交全量检查发现旧版加载器测试使用 `git show HEAD`，提交后实际读取新 V19 加载器，809 项中该项失败；已固定到历史 V18 基线 `12d42ad7`，定向 12 项重跑通过，并追加修复提交 `d54ab0acebcf34464547165340148232c4a1cd3a`。修复后 CI 同款后端全量重跑中。此前被拒绝的推送未执行，新候选仍未发布。
- 隔离提交 `d54ab0ac` 的 CI 同款本地检查终态：后端 809 项通过、57 项跳过；脚本 98 项、CI 辅助 39 项及 Compose 配置通过；连接器 69 项通过，上游 Meme 测试 1045 项通过、1 项因未安装 `botpy` 跳过，另有 85 个子测试通过。日志 `/tmp/youmi-passport-candidate-backend-final.log`、`/tmp/youmi-passport-candidate-connector-final.log`。首次连接器上游测试因本机磁盘可用空间不足失败；仅清理本任务可再生 Maven 缓存后重跑通过，原日志和候选保留。这些不是 GitHub Actions，也不覆盖实机。

## 发布与回退准备

- 新增 `passport-astrbot-rollout.md`：列明生产版本/授权/迁移核对、两级开关启用、双端及实机验收、暂停共享和 V19-aware 故障降级。特别明确旧加载器不能直接接回 V19 数据库，不推断降库安全；尊重此前取消新增生产备份的要求。
- 这是待执行方案，不是生产变更或实机证据。精确提交的 GitHub Actions 仍受公开推送自动审批拒绝限制。

## 完成边界修复

- 审查发现，记忆任务队列满或入队后异常时，原实现已经写入共享历史但账本仍为 `sending`，可能让另一端读到尚未完成的回合。先用本地 HTTP 测试复现，再将共享记录与账本 `completed` 状态放在同一事务中，任务入队成功后才写入；完成前复核共享快照，清除期间等待的旧请求不能恢复历史。
- 本地桥接回归 46 项、Passport 服务 37 项、共享历史 12 项通过；包含队列满、入队后异常重试及等待期间清除。第一次复现失败保留为发现依据。此次改变需要重新对最终精确提交运行全部要求的 CI，不能沿用旧 SHA 结果。
- 修复后的隔离提交 `3ce1dfcc60ef5f16858db8716ad515d024740a4d` 运行 CI 同款本地后端检查：810 项通过、57 项跳过；固件脚本 98 项、CI 辅助 39 项和 Compose 检查通过。Passport 插件及模拟端到端 6 项通过。上游插件完整检查继续运行；这些不是 GitHub Actions 或实机证据。
- 同一隔离提交的 CI 同款本地连接器检查终态：主测试 69 项通过，上游 Meme 1045 项通过、1 项因未安装 `botpy` 跳过，85 个子测试通过；日志 `/tmp/youmi-passport-completion-backend.log` 和 `/tmp/youmi-passport-completion-connector.log`。公开推送与 GitHub Actions 仍未执行。

## 设备长回复边界修复

- 通过真实本地 HTTP 复现多句、5000 字符回复：现有输出检查和认领允许该长度，但设备队列原本只接受 4096 字符，回传失败且无法完成。已将设备回复队列上限与现有已检查输出上限统一为 16000 字符，设备输入上限仍为 4096；不截断原回复。新增回传完成和 16000/16001 边界测试，Passport 本地服务测试 39 项通过。设备实际长语音播放未验证。
- 本机未发现可执行的 ESP-IDF 环境或直连 ESP32 串口；这不代表联网设备离线，实机仍须在生产部署并通过设备网络验收。公开推送仍受自动审批拒绝限制。
- 进一步复现 UTF-8/JSON 编码边界：含表情的 16000 字符合规回复在默认 ASCII 转义下超过桥接请求体限制。客户端改用 UTF-8 JSON，并将检查/认领的响应读取上限设为有界 256 KiB；真实本地 `BridgeClient`→HTTP→设备队列→完成账本已验证该长度，设备服务 40 项和 AstrBot/Passport 回归 45 项通过。首次编码测试出现 `payload_too_large`，修复后通过；仍需真实设备播放验收。
- 最新隔离提交 `5e8dde69fc0c03c162bd7cc59fc1d6f0da906c8f` 的 CI 同款本地后端检查终态：813 项通过、57 项跳过；固件脚本 98 项、CI 辅助 39 项及 Compose 检查通过。连接器主测试 69 项、上游 Meme 1045 项及 85 个子测试通过，上游 1 项因缺 `botpy` 跳过。日志 `/tmp/youmi-passport-unicode-backend.log` 与 `/tmp/youmi-passport-unicode-connector.log`；仍非 GitHub Actions 或实机结果。

## 固定上游设备 MCP 协议验证

- 在 CI 同款固定 xiaozhi 源码上，直接使用上游 `MCPClient` 和 `DeviceMCPExecutor`，通过模拟设备 WebSocket 验证 `self.audio_speaker.set_volume` 的名称转换、`tools/call` 消息、参数、正常返回和设备错误映射。只替换测试中的音频模块、日志和设备链路；不声称实体硬件动作通过。
- 新增上游协议回归：CI 未提供固定源码时明确跳过；标准 backend 验证脚本提供固定上游源码，因此会执行该测试。本地定向测试通过。
- 隔离提交 `7f107268231c1d2e61b217261314e02c3f9a9837` 的 CI 同款本地检查终态：后端 814 项通过、57 项跳过，固定上游设备 MCP 测试实际执行并通过；脚本 98 项、CI 辅助 39 项及 Compose 检查通过；连接器 69 项、上游 Meme 1045 项和 85 个子测试通过（1 项缺 `botpy` 跳过）。日志 `/tmp/youmi-passport-native-mcp-backend.log`、`/tmp/youmi-passport-native-mcp-connector.log`。尚无 GitHub Actions 或实机结果。

## 已授权推送与 GitHub 验证

- 用户明确允许推送后，已将隔离提交 `7f107268231c1d2e61b217261314e02c3f9a9837` 推送到公开新分支 `codex/passport-astrbot-integration`，原工作区未重置。手动启动 [集成工作流](https://github.com/bboytang/Youmi/actions/runs/40617699170) 和 [固件构建](https://github.com/bboytang/Youmi/actions/runs/40617701238)；推送还触发授权、协议及记忆相关检查。以上运行初查均为进行中，尚无通过结论。
- 本地临时探针 `/tmp/youmi-passport-tts-probe.py` 使用固定上游 `TTSProviderBase` 的真实文本消费者、真实 DTO 和现有 `GuardedTTSQueue`，模拟合成器接收设备回传的文本，确认结束标记与字幕文本；首次受沙箱线程限制中止，在允许线程的环境重跑通过。该探针不验证真实 TTS 模型、音频传输或设备播放，也不属于已推送提交的自动测试。
- GitHub [协议工作流](https://github.com/bboytang/Youmi/actions/runs/40617685205) 在 `7f107268` 失败：其只下载单个 ASR 文件，新增上游 MCP 测试无法导入完整 `core`；集成工作流则实际提供完整源码。已修改测试在源码不完整时明确跳过，完整源码时仍执行；两种路径本地均验证。`7f107268` 的集成 workflow 中 backend、connector、snowluma 已通过，两个 manager 任务及固件矩阵仍在运行；修复需新提交及新 SHA 检查。
- 部分源码测试修复已提交并推送 `55b235f0d9be2e007e1a79849d9b27347dbce91b`。已启动该 SHA 的 [协议服务](https://github.com/bboytang/Youmi/actions/runs/40618011509)、[集成](https://github.com/bboytang/Youmi/actions/runs/40618018917)、[固件构建](https://github.com/bboytang/Youmi/actions/runs/40618020943)、[授权](https://github.com/bboytang/Youmi/actions/runs/40618069747)、[管理端](https://github.com/bboytang/Youmi/actions/runs/40618071374)、[记忆兼容](https://github.com/bboytang/Youmi/actions/runs/40618075755) 和 [语义向量](https://github.com/bboytang/Youmi/actions/runs/40618070992)；初查均排队或运行中。被新提交替代的旧固件矩阵已请求取消，不把其结果当新 SHA 证据。
- GitHub 集成工作流 `40617699170` 对旧提交 `7f107268` 的 backend、connector、snowluma、manager、manager-legacy 全部成功；这是旧 SHA 证据，不能冒充最新 `55b235f0`。最新 SHA 的对应任务仍在运行。
- 最新 SHA `55b235f0` 的 [协议服务工作流](https://github.com/bboytang/Youmi/actions/runs/40618011509) 已成功；集成 workflow 的 SnowLuma 成功，其余任务仍运行。固件矩阵展开 185 个任务，AI Passport 当前排队；不得将排队或运行视为通过。
- 最新提交 `55b235f0` 的 GitHub [集成工作流](https://github.com/bboytang/Youmi/actions/runs/40618018917) 全部成功（backend、connector、snowluma、manager、manager-legacy）；[协议](https://github.com/bboytang/Youmi/actions/runs/40618011509)、[授权](https://github.com/bboytang/Youmi/actions/runs/40618069747)、[管理端基线](https://github.com/bboytang/Youmi/actions/runs/40618071374) 和 [记忆兼容](https://github.com/bboytang/Youmi/actions/runs/40618075755) 成功。固件与向量检查仍运行；生产和实机尚未验证。
- `55b235f0` 的 [语义向量检查](https://github.com/bboytang/Youmi/actions/runs/40618070992) 也已成功；集成、协议、授权、Manager 基线、记忆兼容及语义向量检查均为 GitHub 成功。完整固件构建尚在运行，AI Passport 尚无终态，不可提前部署或标为通过。
- 当前环境未发现生产容器或已配置 SSH 主机别名，已向用户请求现有运维入口（不请求密码或密钥）；等待回复期间未更改生产。`55b235f0` 固件矩阵已有 41 项完成、无失败，AI Passport 任务正在运行，完整矩阵及实机仍待验收。
- 固定上游 TTS 本地临时探针扩展验证：实际文本消费者在 `client_abort` 时丢弃待处理文本，回合 ID 改变后现有 GuardedTTSQueue 拒绝旧文本；模拟合成器未收到旧内容。该证据不覆盖设备真实录音、网络或播放。

## Manager 发布制品修正

- 核查 `55b235f0` 的成功集成工作流发现，当前类名 Manager 制品仅含 Controller；新实体设备接口还依赖同一提交的 `YoumiDeviceAuthorizationService.requirePhysical`。仅替换 Controller 会造成方法缺失，不能用于上线。
- 已调整工作流，先逐一检查当前类名 Controller、Service 及 Service 的 Binding 内部类存在，再将三者一起上传。旧类名补丁保持原有严格预像核对。需新 SHA GitHub 运行和实际制品内容检查，不能沿用旧制品或覆盖整份定制 JAR。
