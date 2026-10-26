# Youmi 记忆接管工作交接

最后更新：2026-10-26（步骤 5A–5B 管理接口与页面验收完成）。目标和完整验收见 `plan.md`；每步实测结果见 `worklog.md`。

## 当前进度

- 步骤 0 文档固化完成。
- 步骤 1 兼容验收完成：运行时代码 `37f5680c`，最终验证提交 `1f781da9a990433a3dbf1b719f14043133523860`。GitHub Python 3.10/3.12 各 135 项、完整后端 491 项与人设传输、实际安装后的向量/上下文/回复/提取、绑定 Java 192 项及 Manager 基线均通过。准确链接见 verification.md。
- 步骤 2A 验收完成：`e92439086a61810fc5c9a0e065b45e696a8b8e07` 实现 canonical 只读仓储与移植 CJK/BM25/RRF 影子检索；相关 GitHub 检查全部通过，详见 verification.md、shadow-retrieval.md。
- 步骤 2B 验收完成：产品代码 `39fb95b8`，最终验证提交 `e2d6627528dde37e3d704cc72604068b9946c73a`。GitHub 双版本各 189 项、后端 533 项、实际安装的真实向量链路、绑定授权及 Manager 基线全部通过。新增 25 项机制验证；契约见 state-persistence.md。当前默认仍是旧引擎，认知与合并没有新增公开路由。步骤 3–7 未完成，生产未升级或切换。
- 步骤 3A 验收完成：`04a440ea6583a694a307019e86cadaf807fd82ee`。GitHub 双版本各 207 项、后端 551 项、实际安装后的真实向量链路、绑定授权和 Manager 基线全部通过。具体结果见 verification.md。整个步骤 3 尚未完成，接下来是 3B。
- 步骤 3B 后端机制验收完成：`033cfd05efc5a1c4cba2ae3c532af4f031558564`。新增 24 项；GitHub 双版本各 231 项、后端 575 项、人设传输、真实 BGE 安装链路、绑定授权和 Manager 基线通过。V12 仅在合成库/CI 运行，生产未升级。自动生成/调度尚未接入，不代表整个步骤 3 完成。
- 步骤 3C 机制验收完成：产品 `299fc994`，最终验证 `c0a4a288af38dfe7c1774f41052040f12faa633b`。GitHub 双 Python 各 268 项、后端 595 项及人设传输、实际安装/真实 BGE/固定 Provider+SDK MockTransport、绑定授权和 Manager 基线全部通过。新增 20 项；3A–3C 的步骤 3 后端机制已完成，真实模型质量及线上启用仍待后续验收，生产未升级或开启反思。
- 步骤 4 后端机制验收完成：`43c15bde7db0ca94ea02bdb64d1021a51b1e8dc0`。GitHub 双 Python 各 292 项、后端 619 项及人设传输、实际 BGE/安装/联动和排序、绑定授权及 Manager 基线全部通过。新增 24 项；V14 已冻结。默认联动关闭、排序仅影子检索，生产和旧默认召回未切换；真实模型质量/终端尚未验收。
- 步骤 5A 管理接口验收完成：最终 `8d0e7209215a128d888c142aebca612c125f5c4b`。新增 15 项 Python 和 2 项 Java 测试，GitHub 双 Python 各 307 项、完整后端 634 项及人设传输、Java 194 项及 JAR、Manager 基线、实际 BGE/安装链路全部通过。首轮冲突响应错误及修正如实记录。没有新增 schema 或部署，5B 页面仍未实现，整个步骤 5 未完成。
- 步骤 5B 验收完成：最终 `088033fe827b102baac61076da713911752f503c`。独立页面/导航/卡片、版本化事实编辑确认删除、来源/整理/配置/能量/手动合并、情感审计跳转已实现；新增 4 项后端测试和 13 个四浏览器交互场景。GitHub 双 Python 各 311 项、后端 638 项、Java 194 项及 JAR、Manager 基线、实际安装链路，以及前端原有 9/53/13 项和四浏览器 164 项全部通过。先前浏览器失败和修正完整保留记录，不作为通过证据。V14 未改，未部署或切换。

## 实施约束

保留已有用户修改，尤其 context.py、astrbot_api.py；尽量从未修改的工厂与新增模块接入，不覆盖或提交用户工作。每完成一步，记录准确测试、限制、提交及 CI 链接，再更新本交接。

5B 期间另出现用户并行新文件 `.github/workflows/youmi-qq-reply.yml`，与原有 10 个修改文件和 YOUMI_ASTRBOT/ 一起保持未提交；后续必须重新检查 worktree，不将并行工作误纳入记忆迁移提交。

运行时保留 `store`、`retrieve`、`_require`、`_check`，新增显式 `shadow_retrieve`；默认回复不调用影子接口，不需要安装可选 Tantivy。向量维护仍由原有 SemanticMemoryIndex/MemoryWorker 执行，完成对话写入路径未改。不同进程分别创建实例，但复用同一权威数据库和 owner。

## 下一步具体入口

步骤 3A 已实现 remember/revise 反思动作接入既有 worker 校验/提交链路，本地新增 18 项及相关 207 项通过，对应准确提交的 CI 已全部通过。契约见 reflection-actions.md；输入反思版本 1，持久化结果仍用既有 `_schema: 1`。关闭反思时仍用旧提案；3C 启用后可自动生成新格式，不另开模型请求。

3B 后端机制及准确提交 CI 全部通过。契约见 profile-notes-maintenance.md：画像/笔记/维护候选在 V12 保存来源 ID/版本，完整原句在读取时从 canonical 事实生成，不保存无据摘要。维护始终 preview_only；现有引用事实的变更/删除/待确认/合并使整个条目及标题失效。每 owner 最多 128 条、每条最多 8 个来源，全部动作共享单次 16 条上限；同 key 更新保留 ID，CAS 版本来自服务端 context_for，不由模型填写。

3C 已实现反思生成适配与完成回合驱动的到期维护，契约见 reflection-generation.md，新增 20 项及相关 285 项本地通过，最终准确提交 CI 全通过。开关默认关闭；启用只绑定 V2 成功回合任务，不增加模型请求或聊天历史，整理参考仍最多八条。配置版本/名额/时间/签名在 V13 持久化。没有新对话时不调用模型，时钟到期后由下次成功对话触发；来源不变跳过维护，明确更正快速路径不消耗维护成功时间。

步骤 4 后端机制及准确提交 CI 已通过，契约见 emotion-linkage.md。新增 24 项、相关 294 项本地通过；共用 builder_for 使用 YoumiEmotionContextBuilder，QQ/App/设备后续入口须复用此工厂。原始 user_text 与记忆完整原句、ID/内容/证据版本分开；两组最多八个不同维度，合并原始 delta 后一次执行原有 transition，成功完成后才消费内容版本冷却（新增佐证不重置）。联动新表故障保存点回退本轮变化，不得阻断原情感；授权及上下文变更错误仍传播。

下一任务为步骤 6，先写分阶段计划再实施。优先检查 memory_shadow.py/memory_search.py/memory_repository.py 与既有 SemanticMemoryIndex 的恢复/增量同步路径，补可重建持久化索引、删除/修正后的失效与跨进程一致性、崩溃/重试恢复；SQLite canonical 仍为唯一权威，旧引擎继续唯一写入/默认回复，新引擎只做显式影子评估。认知自动能量/策略映射和永久固定条目也还未实现，需另立具体契约及验收，再更新管理页能力提示。随后准备可重复迁移、冻结中文独立对照集与模型/预算/性能对照，不能直接用生产数据备份或切换来试。

5A 已公开 `/agent/{agentId}/memory-system`，经原管理代理的 `memory_system` 动作，只使用服务端 owner 证明；契约见 management.md。status/list/detail 提供版本、来源分页和最多 50 条合并关系；reflection/emotion_linkage/cognition 复用完整配置 CAS；artifacts/delete_artifact/merge 复用证据、版本及幂等。5B edit/delete 强制传 expected（revision/content_version/evidence_version），底层在 BEGIN IMMEDIATE 内校验；旧方法不传 expected 仍保持原行为，不能用事务外读版本冒充 CAS。内部运行时设备接口的 read/manage 权限保持不变，console 所有者证明拥有该智能体完整管理权，不应构造虚假设备 scope。Java 冲突使用 Result.error(409,msg)，不要用查询翻译词条的 RenException(int,String...)。

5B 已完成，入口为 server/admin_web/YoumiMemorySystem.vue、emotional_system.py 源锚点安装及 apply.py；保留旧记忆弹窗。使用同源 HTTPS + RequestService.sensitive，分组保存草稿，旧版本失败后不自动重试，忙时阻止切换，销毁使读取失效。草稿放弃用页面自有 el-dialog/Promise，避免通用 MessageBox 的 hashchange 监听与复用实例自动关闭；取消/关闭/销毁均结束等待。不要退回仅用通用确认框的中间态。浏览器夹具先代理静态资源后安装 mockBackend，afterEach 等待路由清理；测试保留真实开关、地址变化取消及在途读取断言，不用增加重试掩盖问题。

最终前端工作流 https://github.com/bboytang/Youmi/actions/runs/40263498051 提供完整源码/静态/基线/报告，artifact 名及大小见 verification.md（约135MB，14天）。本机磁盘余量较少，没有整体下载前端产物或安装大型依赖；后续应在有空间的环境检查候选包。CI 使用合成 API/模拟手机，不能作为真实用户登录、真机或生产验收。准确最终提交的其余全部 CI 链接也在 verification.md。

情感历史的 memory_link 记录关联 interaction_history_id、session_id、turn_id、来源 ID/版本及原因，无引用正文。5B 已显示原因并链接当前智能体详情，已删除来源只显示不可用。总开关默认关闭、方向开关默认开启且继承现代情感/记忆主开关，候选不在生产提前启用。排序只接显式影子检索且保护否定/纠错/时间查询，默认召回不变；默认回复不额外注入整理内容。自动合并/退役仍仅预览，管理端只允许所有者明确提交带版本的手动合并；索引恢复和质量评估仍在后续。

后续不得直接使用上游原生独立数据库/public scope 或按 judgment 跨 owner 去重。persona_id 的历史值 `mia` 不当作人物名称修改。V1–V14 已提交冻结，后续扩展从 V15 开始，并同步历史迁移夹具。新检索结果仍不可进入默认回复，需完成剩余验收及步骤 6–7。

2B 未扩容 TurnCompleted/source_payload。完成钩子按 owner/session/turn 捕获状态，worker 仅为新来源关联既有快照；重复历史来源、旧回合和失败捕获保持未知。快照存储失败用保存点隔离，不能回滚已有情感更新；不得由延迟 worker 读取“现在”的状态补历史。快照只表示当时状态，不证明某条事实导致该情绪。

后台合并使用 merge_in_transaction，并传当前 job_id，不能嵌套管理入口的独立事务；必须先验证提案来源、归属、两种版本和 owner revision。cognition 管理接口需要 memory:manage，不应把模型反思当作管理员配置命令。四维认知自动能量更新和行为参数映射仍未接入，不能把双向情感联动当作认知策略完成。

现有情感系统必须持续生效；天使之魂四维状态不替代 51 维情感。人设原文保持不动。默认仍为现有记忆引擎，不允许未经验证启用新引擎。

## 研究材料与来源

上游固定提交及注意事项见方案。详细实验保存在 `/tmp/angel-memory-port-study/`，为临时材料，可能回收；不能作为产品运行依赖。165 文件清单、55 项上游用例、14 项研究用例和 Python 3.10 复跑不等于完成产品验收。

云运行环境技能已读；当前未提供 environment_status 工具，且网络策略快照不存在，不能据此宣称凭据或网络已就绪。正常使用现有工具并以实际返回结果为准，不读取或输出密钥。

## 切换前未完成项

持久化索引恢复、认知自动策略、永久固定条目契约、真实模型反思/联动/召回质量影子对照、真实数据迁移、线上故障回退演练、真实手机和真实 QQ/App/设备验收。管理页虽已通过模拟测试，尚未发布生产。维护为完成回合驱动，画像/笔记是有证据原句的整理视图，不是上游全部自由文件笔记或空闲夜间唤醒；兼容旧算法测试也不是未来切换回退的完整演练。不得提前切换。
