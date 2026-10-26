# Youmi 记忆接管工作交接

最后更新：2026-10-26（6A空闲重试与页面清理反馈均已验收，下一步6B；生产未启用）。目标和完整验收见 `plan.md`；每步实测结果见 `worklog.md`。

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
- 步骤 6A 首段已验收：产品 `514c19ee13615b80459407a3528f6edeb8225a98`，最终准确验证提交 `1a4e7277ad812c2467e4d057f2257744f55a36a6`。新增 16 项，本地相关 105 项；GitHub 双 Python 各 327 项、后端 654 项、Java 194 项/JAR、Manager 基线及实际安装全部通过。合成评估磁盘重开/追加/变更重建/dirty恢复/跨进程锁已实现；默认仍 RAM 影子和旧回复，生产缓存生命周期仍缺，整个步骤 6 未完成。

- 6A 第二段的 V15 事务失效账本基础已验收：产品 `6459fe6d`，最终验证 `879e62e2db089dcb75b2783c642da0d157c2a5f6`。GitHub双Python各339项、完整后端666项、人设传输、实际BGE安装链路20项影子检查、Java194项/JAR及Manager150+4全部通过。尚无目录注册或清理执行器，不能用于生产私有磁盘缓存。

- 6A V16受控清理后端机制已验收：产品/验证 `56aefbce140b3fd0c48080487101ac00c833c9f8`。新增25项，GitHub双Python各364项、后端691项、人设传输、实际安装23项影子检查、Java194项/JAR和Manager150+4全部通过。固定根授权注册、提交后物理清理、查询前后版本栅栏与启动恢复已实现。默认未开启；pending页面/时限/空闲重试与生产验收未完成。

- 6A空闲自动重试与页面反馈验收完成：产品/验证 `e3b512fb436e22d7ca0f1e40c3fe042ff4e7366e`，双Python各371项、后端698项、四浏览器184项、实际安装24项影子检查、Java194项/JAR、Manager150+4六组CI全通过。此前空闲重试与pending页面缺口已补，无schema变化；生产仍未注册/开启。

## 实施约束

保留已有用户修改，尤其 context.py、astrbot_api.py；尽量从未修改的工厂与新增模块接入，不覆盖或提交用户工作。每完成一步，记录准确测试、限制、提交及 CI 链接，再更新本交接。

5B 期间另出现用户并行新文件 `.github/workflows/youmi-qq-reply.yml`，与原有 10 个修改文件和 YOUMI_ASTRBOT/ 一起保持未提交；后续必须重新检查 worktree，不将并行工作误纳入记忆迁移提交。

运行时保留 `store`、`retrieve`、`_require`、`_check`，新增显式 `shadow_retrieve`；默认回复不调用影子接口，不需要安装可选 Tantivy。向量维护仍由原有 SemanticMemoryIndex/MemoryWorker 执行，完成对话写入路径未改。不同进程分别创建实例，但复用同一权威数据库和 owner。

## 下一步具体入口

步骤 3A 已实现 remember/revise 反思动作接入既有 worker 校验/提交链路，本地新增 18 项及相关 207 项通过，对应准确提交的 CI 已全部通过。契约见 reflection-actions.md；输入反思版本 1，持久化结果仍用既有 `_schema: 1`。关闭反思时仍用旧提案；3C 启用后可自动生成新格式，不另开模型请求。

3B 后端机制及准确提交 CI 全部通过。契约见 profile-notes-maintenance.md：画像/笔记/维护候选在 V12 保存来源 ID/版本，完整原句在读取时从 canonical 事实生成，不保存无据摘要。维护始终 preview_only；现有引用事实的变更/删除/待确认/合并使整个条目及标题失效。每 owner 最多 128 条、每条最多 8 个来源，全部动作共享单次 16 条上限；同 key 更新保留 ID，CAS 版本来自服务端 context_for，不由模型填写。

3C 已实现反思生成适配与完成回合驱动的到期维护，契约见 reflection-generation.md，新增 20 项及相关 285 项本地通过，最终准确提交 CI 全通过。开关默认关闭；启用只绑定 V2 成功回合任务，不增加模型请求或聊天历史，整理参考仍最多八条。配置版本/名额/时间/签名在 V13 持久化。没有新对话时不调用模型，时钟到期后由下次成功对话触发；来源不变跳过维护，明确更正快速路径不消耗维护成功时间。

步骤 4 后端机制及准确提交 CI 已通过，契约见 emotion-linkage.md。新增 24 项、相关 294 项本地通过；共用 builder_for 使用 YoumiEmotionContextBuilder，QQ/App/设备后续入口须复用此工厂。原始 user_text 与记忆完整原句、ID/内容/证据版本分开；两组最多八个不同维度，合并原始 delta 后一次执行原有 transition，成功完成后才消费内容版本冷却（新增佐证不重置）。联动新表故障保存点回退本轮变化，不得阻断原情感；授权及上下文变更错误仍传播。

6A账本、受控清理/启动恢复、空闲重试和页面pending反馈均已通过准确CI（最新入口见文末）。下一任务6B：先写自动认知能量/行为映射与固定条目契约，再实现及验证。四维认知独立于51维情感；固定条目不等于confirmed，明确纠错/遗忘不能被认知策略截断。持续磁盘故障仍无硬删除期限，保持pending，生产真实终端与容量/质量仍待6C–7。不能将读时全快照对账当作闲置缓存的即时物理删除，当前磁盘模式不得用于生产私有数据。随后继续 6B 认知自动能量/行为策略与永久固定条目契约、6C 可重复迁移/冻结中文质量与预算/性能对照。SQLite canonical 仍为唯一权威，旧引擎继续唯一写入/默认回复；不得直接取生产库备份、启用新引擎或切换。

6A 当前代码入口：memory_index_cache.py 原创协调器（私有目录、fcntl 文件锁等待 0.4 秒、dirty/fsync/原子清单、恢复/全重建/追加），memory_search.py 保留 GPLv3 归属，新增 path/append 复用原分词和排序。YoumiMemoryRuntime(index, shadow_cache_dir=...) 只有显式合成评估使用；普通 builder_for 没有传路径。每次仍捕获最多 10000 条/8 MiB canonical 快照；新增增量写，改文/检索元数据变化/删除/待确认/退役重建，佐证变化不重写 BM25，但返回仍重新读取当前来源/版本。无有效记录或关闭后的显式查询清空该分区，未查询的闲置目录不会自动清理。

方案与限制见 index-recovery.md，新增测试在 test_companion_memory_index_cache.py（16 项），实际安装脚本 verify_youmi_memory_shadow.py 新增 persistent_shadow_reopen/dirty_recovery/canonical_unchanged，最终 artifact 18 项全真，准确链接见 verification.md。最终完整安装工作流 40266257468 全通过；早期 40265772529 两次旧编码并发 case 3 为 embedding_busy，结果文件保存在 /tmp/youmi-index-first-results 和 /tmp/youmi-index-second-results。最终同算法短句18次/长句4次全部一致，但未定位根因；容量评估需保留这个观察，不说编码器问题已修复。CI 独立安装检查在 runtime 成功且未取消时继续，即使前项失败仍产出结果；无 continue-on-error，原失败保留。

5A 已公开 `/agent/{agentId}/memory-system`，经原管理代理的 `memory_system` 动作，只使用服务端 owner 证明；契约见 management.md。status/list/detail 提供版本、来源分页和最多 50 条合并关系；reflection/emotion_linkage/cognition 复用完整配置 CAS；artifacts/delete_artifact/merge 复用证据、版本及幂等。5B edit/delete 强制传 expected（revision/content_version/evidence_version），底层在 BEGIN IMMEDIATE 内校验；旧方法不传 expected 仍保持原行为，不能用事务外读版本冒充 CAS。内部运行时设备接口的 read/manage 权限保持不变，console 所有者证明拥有该智能体完整管理权，不应构造虚假设备 scope。Java 冲突使用 Result.error(409,msg)，不要用查询翻译词条的 RenException(int,String...)。

5B 已完成，入口为 server/admin_web/YoumiMemorySystem.vue、emotional_system.py 源锚点安装及 apply.py；保留旧记忆弹窗。使用同源 HTTPS + RequestService.sensitive，分组保存草稿，旧版本失败后不自动重试，忙时阻止切换，销毁使读取失效。草稿放弃用页面自有 el-dialog/Promise，避免通用 MessageBox 的 hashchange 监听与复用实例自动关闭；取消/关闭/销毁均结束等待。不要退回仅用通用确认框的中间态。浏览器夹具先代理静态资源后安装 mockBackend，afterEach 等待路由清理；测试保留真实开关、地址变化取消及在途读取断言，不用增加重试掩盖问题。

最终前端工作流 https://github.com/bboytang/Youmi/actions/runs/40263498051 提供完整源码/静态/基线/报告，artifact 名及大小见 verification.md（约135MB，14天）。本机磁盘余量较少，没有整体下载前端产物或安装大型依赖；后续应在有空间的环境检查候选包。CI 使用合成 API/模拟手机，不能作为真实用户登录、真机或生产验收。准确最终提交的其余全部 CI 链接也在 verification.md。

情感历史的 memory_link 记录关联 interaction_history_id、session_id、turn_id、来源 ID/版本及原因，无引用正文。5B 已显示原因并链接当前智能体详情，已删除来源只显示不可用。总开关默认关闭、方向开关默认开启且继承现代情感/记忆主开关，候选不在生产提前启用。排序只接显式影子检索且保护否定/纠错/时间查询，默认召回不变；默认回复不额外注入整理内容。自动合并/退役仍仅预览，管理端只允许所有者明确提交带版本的手动合并；索引恢复和质量评估仍在后续。

后续不得直接使用上游原生独立数据库/public scope 或按 judgment 跨 owner 去重。persona_id 的历史值 `mia` 不当作人物名称修改。V1–V14 已提交冻结；本轮新增 V15 失效账本，历史夹具同步清除新对象。V1–V16已冻结，后续新增schema从V17开始，不能修改旧 SQL。新检索结果仍不可进入默认回复，需完成剩余验收及步骤 6–7。

2B 未扩容 TurnCompleted/source_payload。完成钩子按 owner/session/turn 捕获状态，worker 仅为新来源关联既有快照；重复历史来源、旧回合和失败捕获保持未知。快照存储失败用保存点隔离，不能回滚已有情感更新；不得由延迟 worker 读取“现在”的状态补历史。快照只表示当时状态，不证明某条事实导致该情绪。

后台合并使用 merge_in_transaction，并传当前 job_id，不能嵌套管理入口的独立事务；必须先验证提案来源、归属、两种版本和 owner revision。cognition 管理接口需要 memory:manage，不应把模型反思当作管理员配置命令。四维认知自动能量更新和行为参数映射仍未接入，不能把双向情感联动当作认知策略完成。

现有情感系统必须持续生效；天使之魂四维状态不替代 51 维情感。人设原文保持不动。默认仍为现有记忆引擎，不允许未经验证启用新引擎。

## 研究材料与来源

上游固定提交及注意事项见方案。详细实验保存在 `/tmp/angel-memory-port-study/`，为临时材料，可能回收；不能作为产品运行依赖。165 文件清单、55 项上游用例、14 项研究用例和 Python 3.10 复跑不等于完成产品验收。

云运行环境技能已读；当前未提供 environment_status 工具，且网络策略快照不存在，不能据此宣称凭据或网络已就绪。正常使用现有工具并以实际返回结果为准，不读取或输出密钥。

本轮沙箱将 .git 设为只读且限制网络；提交、推送、GitHub API 和固定 Tantivy 下载已通过工具自动审批执行，无人工确认问题。受限沙箱内 asyncio 线程唤醒挂起，faulthandler 显示工作线程空闲/主线程 selector；获准在沙箱外运行相同合成测试通过。Tantivy 0.25.1 本地临时依赖在 `/tmp/youmi-memory-index-deps`，测试 Python 仍为 `/root/tmp/youmi-astrbot-venv/bin/python`；最终小型安装结果 `/tmp/youmi-index-final-results` 已核对。不要为沙箱限制修改产品事件循环或请求超时，也不要输出凭据。

## 切换前未完成项

受控缓存的真实生产/终端验收及容量/故障时限评估、认知自动策略、永久固定条目契约、真实模型反思/联动/召回质量影子对照、真实数据迁移、线上故障回退演练、真实手机和真实 QQ/App/设备验收。合成评估索引恢复已通过，不等于生产持久化索引验收；管理页虽已通过模拟测试，尚未发布生产。维护为完成回合驱动，画像/笔记是有证据原句的整理视图，不是上游全部自由文件笔记或空闲夜间唤醒；兼容旧算法测试也不是未来切换回退的完整演练。不得提前切换。

## 上轮V15交接（历史，当前以V16为准）

- 计划 `2916e7c7`，产品 `6459fe6dd8ee743e6cd811c971ea8d7207181407`，Python3.10 测试恢复修正 `879e62e2db089dcb75b2783c642da0d157c2a5f6`。最终 CI 结果见 verification.md / worklog.md，不引用首轮失败作为通过。
- schema-v15.sql 只新增 youmi_memory_cache_invalidations 与三个触发器：每 owner 一行 generation/cleaned_generation，首次迁移所有已有 owner 待处理，内容 epoch / owner revision / paused 变化同事务推进。它不是路径注册表，没有正文或情绪副本。当前 schema 的唯一 persona 为历史 mia，扩展前必须重审分区键。
- memory_cache_invalidations.py 的内部 pending(limit<=100) 与 acknowledge(owner,generation) 仅供未来受控清理器；禁止对 HTTP/模型暴露。必须先删完所有已注册分区并持久化删除结果再确认；清理失败/中断不确认；新写使旧代次 CAS 失败。确认不能借用未提交的事实事务。
- 当前没有任何运行时调用确认，没有注册目录或执行器，没有即刻清理闲置缓存；默认回复/情感完成不依赖文件系统或新增清理请求。正常工厂仍不提供 shadow_cache_dir，合成缓存继续读时对账；生产禁用限制仍在。
- 新增12项测试覆盖真实进程退出、事务与重试、源/元数据/设置/空清空、代次竞争、迁移失败/中断。安装三入口均带V15，全量/情感入口含内部模块；AstrBot增量入口只需SQL，不调用账本helper。新检查脚本中删除事实后重开仍能看见待处理代次，明确不宣称物理删除完成。
- 第一轮本地143项有两处最新版本14断言，已更新为15后全通过；CI Python3.10 的 set_authorizer(None) 恢复不兼容已用显式 SQLITE_OK 回调修正。保留迁移注入和全dump断言。
- 下一次应先为目录所有权、注册授权、孤立/已删除owner分区、跨进程锁序（先SQLite还是文件锁）、并发清理期间新搜索写入、磁盘失败状态与立即遗忘响应定义可验证契约，再实现实际清理。仅 generation 账本不能证明磁盘已清空；不得提前启用生产。

## V16底层代码入口（调度与页面以文末最新记录为准）

- 方案622648f0，产品与验证56aefbce140b3fd0c48080487101ac00c833c9f8，准确五组CI及artifact见verification.md。本地完整兼容364项及冻结旧版8项也通过。
- memory_cache_lifecycle.py / schema-v16.sql：固定根为canonical同目录 `.<db文件名>.youmi-memory-cache`，owner/历史mia分区哈希。只在授权capture后内部register，不接受任意清理路径；注册不随owner级联消失，孤立分区startup清完再删注册。数据库搬迁前必须清原根，不能自动认领旧临时路径。
- memory.py 的 _MemoryConnection 同时覆盖commit与上下文成功提交，_cleanup_cache在startup/后续提交分批100轮换，不等文件锁；前台edit/delete/clear直接再处理目标，不被批次遮住。失败不回滚已提交事实或情感。未注册时不访问缓存文件系统。
- memory_index_cache._locked保留稳定空writer.lock；_search_locked供协调器持同一锁调用。清除其余文件后fsync，再以独立短SQLite事务核对代次并同时清writing/error；V15单独acknowledge helper仍不用，因为V16需要两个状态原子确认。根/分区/锁/索引链接拒绝，清理内部链接不跟随目标。
- memory_shadow/runtime内部选managed_shadow_cache=True（与shadow_cache_dir互斥），先capture授权，native先用短事务注册并释放数据库写锁，再持文件锁校验版本、writing=1、搜索、复核。中途写入清掉旧段并拒绝；os._exit/native/磁盘/确认失败保留writing或代次，startup恢复。原默认工厂未传参数，不开启生产。
- 管理status及已注册edit/delete回执带cache_cleanup={state,error}，旧handle也带状态；saved只表示事实提交，pending不是物理清理完成。页面仍没有开启入口；本轮已补pending提示与核对，见文末，不能只把saved或成功toast当作物理清理证明。
- V16初段仅由startup/后续提交/查询/内部recover驱动；本轮已补独立空闲清理泵与页面反馈，见文末。不得为定时器增加模型请求或无界线程。
- 受控模式失效清整分区，新增也可能重建；原临时合成模式仍增量追加，两者别混用或夸大性能。原始外部sqlite连接只有事务失效保证，无提交清理回调；正常受支持写入须通过新MemoryStore。
- 新增25项含跨进程/中断/锁忙/旧快照/IO与DB失败/孤立owner/链接/批次/非默认51维及V15迁移。首轮两个夹具准备错误已修正，本轮最终准确SHA首次五组CI全通过。V1–V15、人设/情感算法、并行QQ文件均未改。
- 小型结果保存在/tmp/youmi-managed-cache-final-results，artifact109916字节、23项全真。无生产访问/部署/缓存注册/备份/真实消息，前端和物理设备未验证。后续schema从V17，继续6B/6C/7前保持生产关闭。

## 当前空闲清理与页面反馈交接

- 方案cb218765，产品/准确验证e3b512fb436e22d7ca0f1e40c3fe042ff4e7366e，六组CI首次全通过，准确链接/产物见verification.md。本地完整兼容371项通过。
- 新模块memory_cache_cleanup.py，YoumiMemoryCleanupPump默认interval=5/batch_size=10，构造参数供内部测试/集成，不是公开配置入口。每worker至多一条daemon线程，单批结束等待interval，recover轮换游标。未注册只读空注册表，不建目录/不调用Tantivy搜索或模型；有故障继续周期重试，不退出、不热循环。
- jobs.py的MemoryWorker初始化持有cleanup对象；start启动，主task完成/取消的done callback停止清理；close停止并把worker task与cleanup.wait放进同一个总预算。不要换成阻塞join、每次超时另开线程或无界to_thread队列。等待取消/超时后底层线程/锁仍保留，stop信号不清除，晚完成只用自己的SQLite连接，不接触已关闭store。
- 全量安装加helper，情感增量UPDATED增加jobs.py并带helper，确保真实服务启动入口同步；历史AstrBot增量包不具备开启受控模式的入口，不能拿它冒充新完整运行时。
- YoumiMemorySystem.vue显示当前agent缓存pending/complete及简短原因。已注册edit/delete回执pending只给警告，不把saved=true当成物理清理完成。核对按钮只POST现有status并更新cache_cleanup，不触碰配置/事实草稿、版本或重发写；缺失/读取失败不能清掉已知pending。默认未注册/旧后端没有状态不宣称已清理，不增加公开开启/强制清理按钮。
- 新增7项后端测试；四浏览器新增5场景*4=20项，总184项通过（含手机模拟）；完整后端698、人设、Java194/JAR、Manager150+4和安装24项通过。没有真机/真实QQ/真实模型质量结论。
- 正常静态N队列调度一轮约ceil(N/10)*5秒加处理时间，不是持续IO故障的硬期限。服务停服由下一启动恢复，失败维持pending。历史V16节中的“尚无空闲定时器/页面提示”已由本轮补齐，不要重复实现。
- 小型安装结果/tmp/youmi-idle-cleanup-results已核对，110113字节；前端源码/静态报告artifact134700454字节未整体下载。本轮未操作生产、注册、开启、备份或外发消息；原有并行QQ工作不覆盖/提交。
- 下一步6B先写方案/计划：memory_state.py已有四维配置/手动energy、成功回合capture_turn与来源快照，尚无自动energy/行为映射；memory_management.capabilities仍automatic_cognition=False/permanent_memories=False，必须实际实现验收后才改。固定/永久独立标志需评审V17，禁止修改V1–V16或把confirmed当固定，保留明确更正/删除与owner隔离、51维情感唯一权威/成功完成幂等。


## 最新6B交接（2026-10-26）

6B1产品08a99335已实现V17认知/固定，首次实际BGE并发失败；后续eb43257bd2559e3c658ebdc1dc6bb711e68f25c1公平有界准入修复五组CI全部通过，链接和失败保留见worklog。SSH已用原root默认身份连通43.143.230.174，不需再次向用户索取连接；生产仍V10，只读审计，未部署或备份。

6B2候选入口cognitive_context.py / reply.py，认知版本随共享context核验、可选指导预算省略、原生每轮参数；QQ仅系统指导，不能声称SDK采样改变。memory_shadow及缓存传递effective recall，memory_reflection限制自动整理impression并保护固定来源/明确事实，过时认知版本不提交整理及维护时钟。管理页固定/取消固定、有效值和失败阻写已经编码，测试及安装载荷同步，最终准确SHA及四浏览器CI仍待核对。

下一阶段仍须6C冻结独立中文质量/无答案/否定/纠错/隔离、预算和容量、V10迁移重复与中断；7新增按智能体引擎选择和V2任务统一接管、兼容新schema旧算法回退，再继承现生产定制镜像/JAR/静态部署。当前仅机制候选，不得直接切生产默认或宣布全目标完成。保留并行用户QQ文件，不能部署整个未审工作区。当前授权包括部署，不包括新增生产数据库/密钥备份或向他人发送QQ测试。


补充核实：JobStore默认已为V2，start_worker使用默认构造，原生/QQ共用该worker。因此6B2的automatic_cognition=False及“旧任务尚未启用”说明需要修正为能力true，实际仍须认知/反思开关；不需要为步骤7重新改任务默认。先前总结推测任务默认V1不正确，以实际jobs.py/start_worker为准。旧历史V1任务继续原策略、不回填。新检索统一切换仍未实现。
