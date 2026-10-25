# Youmi × AstrBot × SnowLuma 当前实施方案与计划

更新时间：2026-10-25 04:39 UTC。当前为**同一次完整交付的实施中记录**，不是上线/验收声明。开发分支 `feature/astrbot-fusion`，基线 `739c509eb3f4bc231d879367b33a15a8a94c38f5`；新增工作尚未提交。

任务依据：用户附件 `YOUMI_ASTRBOT/YOUMI_ASTRBOT_SNOWLUMA_CODEX_PLAN.md` 与后续明确指令。入口索引：[审计](astrbot-audit.md)、[HTTP 契约](astrbot-contract.md)、[工作记录](astrbot-worklog.md)、[交接记录](astrbot-handoff.md)、[部署](../../deploy/astrbot-fusion/setup.md)、[回滚](../../deploy/astrbot-fusion/rollback.md)。

## 已确定的架构与所有权

- SnowLuma + OneBot v11 反向 WebSocket 接 QQ；AstrBot **内置 Agent** 生成唯一最终回复，保留工具、原生插件和 MCP；Meme Manager 管理全部表情资源、控制标签和选择过程。
- Youmi 是人格、角色、长期记忆、持续情绪、成长的唯一权威。QQ 与原 App/设备使用同一现有 owner/agent/persona（内部 persona_id 仍为 `mia`）。不新建另一套事实数据库、Embedding/RAG 或账号系统。
- 不增加 QQ 好友白名单。内部服务凭据、manager 当前 Scope、授权绑定版本/过期/撤销校验必须保留。QQ sender 不能当可信 owner。
- 不重写第三方源码；通过固定版本插件钩子和既有后端扩展接入。不冒称 QQ 原生实时电话；Record 是语音消息。
- 不切换生产模型/音色，不覆盖角色，不删库、不 down -v，不复制线上密钥、不进行未经授权的生产部署。
- 用户 2026-10-25 最新要求：后续 Actions **使用本地 nektos/act**；不再触发新的 GitHub 托管运行。另授权清理不会影响 Youmi 和 CodeRove 的其他缓存/临时文件。

## 已核实的重要事实

1. 指定分支 fetch 后 HEAD 仍为上述基线。本地原 origin 指向更名前的 Mia，未擅自改 remote。原任务书是未跟踪附件，保留。
2. 已通过现有 SSH 做生产只读核查。当前语音镜像为 `mia-xiaozhi-server:dialogue-b51be7e-capacity-93b7172`，SQLite V7/quick_check=ok，当前 memories=0；不能沿用旧文档的 47 条事实。
3. 当前生产 agent 的实际模型是 `deepseek-flash`，voice 为 `zh_female_xiaohe_uranus_bigtts`，角色 prompt 长度 0。没有恢复旧 GLM、补造记忆或改角色。
4. 生产是向外部 XiaoZhi 服务安装 companion 扩展；HTTP app 在原服务，worker 在 `start_worker` 创建。线上旧入口仍叫 `xiaozhi_adapter.py`，仓库入口叫 `youmi_adapter.py`，需离线增量更新器分别处理。
5. 固定 AstrBot v4.29.3 / `f61a7f4`、Meme v5.2.5 / `cf18e81`、SnowLuma v1.10.0 / `7f391e0`；完整 SHA 见 versions.json 和审计。
6. AstrBot 大 priority 先执行；Meme 的 request/response/decorate=99999。正常工具循环复用 runner.req，不会每一步自动重发 request hook。原生 cron 绕过普通 request hook，需 on_agent_begin。
7. 原生 RespondStage 可能捕获发送错误后仍触发 after_message_sent，不能单靠该钩子认定投递成功。

## 当前实际实现

| 部分 | 当前代码 | 实现要点 | 验证状态 |
|---|---|---|---|
| 审计/契约 | docs/integrations/astrbot-audit.md、astrbot-contract.md | 按真实源码写新契约，示例接口不当现有 API | 已写，需随最终实现复核 |
| 虚拟身份 | manager_identity 两个既有 Java 类；astrbot_identity.py | 独立 `astrbot-...` 虚拟设备，复用 code/exchange/renew/revoke；bridge-config 再验 Scope | Python否定用例已过；Java act完整190项/JAR已通过，最终SHA待复跑 |
| 同库回合账本 | schema-v8.sql、astrbot_turns.py | prepared→sending→completed/failed/expired；发送前持久 claim；情绪/事件与步骤账本同事务，原 JobStore 幂等 admission | SQLite/故障/重启/重复测试已过 |
| HTTP 适配 | astrbot_api.py | 复用 ContextBuilder、角色、RAG、PersonaAgent、同一 service-owned worker；只取系统核心及临时动态上下文 | 已有后端专项通过，仍需最终审查 |
| QQ 记忆管理 | backend manage 与插件命令 | 复用 MemoryStore；pause 沿用 owner 级开关；“这段别记”只抑制当前 QQ 会话，明确 resume 才恢复 | 机制测试通过；须补充最终契约字段说明 |
| Connector | integrations/astrbot_plugin_youmi | 逐请求去重注入临时 TextPart，保留 history/func_tool/tool_call_id/非 Plain；Meme 后校验，原 event.send 最终发送一次 | 固定源码集成15项通过 |
| 完成恢复 | turn_state.py / client.py | 有界重试和受保护短期 JSON complete outbox；不重放 QQ、模型或副作用工具 | 后端和 Connector 故障用例通过 |
| 音频/图像 | astrbot_media.py、插件 media.py | 复用当前 Huoshan provider 的配置与协议 helpers 收集 PCM→有界 WAV；图像/STT 用原 AstrBot | WAV/Record 序列化通过；供应商/手机未测 |
| 主动聊天 | 插件 on_agent_begin / on_using_llm_tool | 原生 cron 注入 scheduled；不创建用户事实 job；发送工具文本校验 | 真实CronMessageEvent/发送工具/Meme联动及拒绝路径通过；实际定时未验 |
| 部署/回滚 | deploy/astrbot-fusion | 两个 sidecar、固定镜像 digest、回环端口、持久卷、占位配置、离线 runtime 更新器 | Compose config 已通过；未启动生产 sidecar |
| 本地 Actions | youmi-astrbot.yml、run_astrbot_act.sh | act 本机 runner 执行同一验证脚本；隔离 MySQL/Redis，不接生产 | 三类job前轮均成功；最终新增用例待精确提交act复跑 |

## 发送与幂等取舍

Connector 在 Meme 已处理后的最终 decoration 中，通过 **AstrBot 原 event.send** 发送同一最终链；成功后保留公共 after hooks，再清空 result，防止原 RespondStage 重发。这是对已证实发送错误契约的适配，没有另建 OneBot 发送客户端。

发送前 claim 是持久且至多一次的。claim/投递之间崩溃无法判断手机是否收到时，不自动重放，可能留下 delivery uncertain、丢失该回合记忆 admission；不能承诺分布式 exactly-once 手机投递。after hook 本身不是成功证明。完成重试只恢复内部账本/既有 job。

情绪/事件 checkpoint 与 SQLite 事务一致；worker admission 是另一原生事务，依靠原 JobStore 唯一键恢复。accepted 只表示入队，不表示事实已保存。

“这段别记”在 QQ 没有原硬件连接结束事件，因此采用保守隐私语义：跨重连/重启持续抑制当前私聊提炼，用户显式 resume 恢复；不静默恢复、不改变其他 App 自动保存开关。这一行为必须在契约/插件 README 中明确。

## 剩余工作计划及成功条件

这些是同一次开发任务内的工程顺序，不拆产品阶段。

1. **完成实现审查和缺口修正**：核对 complete/claim 竞争、过期终态、角色变更、scope 撤销、model/tool 预算、scheduled 失败处理、独立插件输出范围、临时数据清理；补足必要可查询回合状态和 updater/迁移回滚测试。成功条件：契约与实际路由/字段/错误一致，无仅文档承诺。
2. **完成固定版本兼容验证**：补 scheduled/ASR 失败/发送工具路径测试，运行 Meme 原生完整测试；保留首次失败原因与最终结果，不以 fixture 当真机。成功条件：最终链文字、GIF/Image/Record 不丢失，控制标签不外泄，工具 ID/参数/结果完整，回合只提交一次。
3. **完成本地 act 回归与构建**：backend 重新跑最终代码，connector 与 Meme 原生，Java 权限完整回归及 JAR 构建；修复实际失败。成功条件：记录准确代码 SHA、命令、原始结果及本地 runner 边界。没有 GitHub run link 就不写远端通过。
4. **补齐统一验收材料**：astrbot-compatibility.md、astrbot-acceptance.md（T01–T30），完善运行/回滚/健康/配置/凭据生命周期说明。成功条件：每项区分机制 fixture、固定源码集成、真实生产/QQ/iOS，未测项有实际原因。
5. **最终 diff/提交**：确认只改本任务相关文件，保留附件和其他 worktree；提交到 feature/astrbot-fusion，记录 SHA。若推送，避免触发用户已改为本地执行的远端 Actions。成功条件：最终提交与测试源码对应，剩余真机/生产阻塞明确。

## 当前证据与未验证边界

- 已成功：act0.2.89 / Docker29.1.3；较早act backend438server+98scripts、manager190/JAR成功；Meme原生1045passed+1skip、85subtests；最新工作区专项39项后端（含真实V7迁移与固定Huoshan协议）、15项Connector、39项CI辅助脚本测试通过。
- 新增status路由、30秒后台过期擦除、原始OneBot文字与图片caption分离、scheduled真实工具测试、V7→V8失败回滚、真实provider构帧/解析测试已实现；不是待实现项。
- 统一兼容和T01–T30报告已写。下一步只剩最终diff、提交及本地act完整复跑、回填证据和交接，不重新从审计开始。
- 未执行：生产候选切换/备份/迁移、线上虚拟授权注册、QQ扫码/真机、实际模型工具/视觉/STT、生产TTS/手机播放、原App双端、生产回滚。已有只读SSH权限不等于修改授权。Linux act不具备macOS/Xcode。
- 原生实时电话：NOT SUPPORTED / NOT VERIFIED。

后续实际进度追加到worklog，并同步handoff和验收表。
