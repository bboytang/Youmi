# Youmi 记忆接管工作记录

## 2026-10-25 — 步骤 0：方案与实施记录落库

- 用户要求：先写方案和实施计划，再实施；每完成一步做工作记录与交接。
- 基线：分支 `feature/emotional-system`，HEAD `4635a839`。情感系统现有规则：51 维、单轮最多 8 维提案、原话 quote 校验、完成回合幂等提交和配置版本检查。
- 已完成：`plan.md` 固化接管步骤、双向联动、默认参数、验收条件、许可及回退边界；建立本记录和 `handoff.md`。
- 已有用户修改：deploy/astrbot-fusion/bridge-config.example.json、docs/integrations/astrbot-contract.md、integrations/astrbot_plugin_youmi/{README.md,_conf_schema.json,main.py}、integrations/tests/test_astrbot_compatibility.py、server/companion/{astrbot_api.py,context.py}、server/tests/{test_astrbot_bridge.py,test_companion_context.py}；另有未跟踪 YOUMI_ASTRBOT/。本步骤未更改这些文件。
- 验证：只读核对记忆、情感、ContextBuilder、回复入口、任务提交、安装器和 GitHub 工作流。步骤 0 仅文档，不声称产品测试完成。
- 生产影响：无。未迁移数据库、未切换记忆、未改情感、未发送消息、未部署。
- 下一步：步骤 1，统一兼容入口，保持既有算法默认。

## 2026-10-25 — 步骤 1：统一兼容入口实现，等待 CI 验收

- 新增 `YoumiMemoryRuntime`，由原回复 builder 工厂统一构造；AstrBot 原有工厂调用自动复用，不改动用户正在编辑的 context.py 和 astrbot_api.py。
- 只委派既有 SemanticMemoryIndex 的召回、鉴权及快照校验；没有增加另一套状态或写入路径，没有启用新算法，没有变更数据库。
- 更新全量安装器、历史离线情感升级载荷及命名转换载荷，避免安装后的 reply.py 引入不存在的模块。
- 新增 9 项真实 SQLite/合成向量回归：旧排序/来源和数据不变、三端共用工厂及智能体隔离、51 维与完整长人设上下文、召回故障情感仍有效、功能关闭及撤权、删除/配置变化使旧上下文失效、固定/锁定/限幅/恢复与成功提交去重、重开数据库、安装后独立导入。
- 测试夹具初跑发现三处前提不足：长人设超过夹具模型容量、模拟安装缺少上游父目录、撤权场景未建向量索引。仅修正合成夹具的容量和准备步骤，未放宽生产校验；重跑 9/9 通过。
- 本地重点回归：135 项通过（新运行时、情感、功能开关、向量、回复、任务、连续性）。完整后端回归进行中。新增 Python 3.10/3.12 GitHub 工作流，并为现有真实向量工作流补充运行时路径触发。
- 状态：代码完成，CI 未完成前不计为步骤 1 全部验收；真实终端未测试，未部署。下一步先完成准确提交 CI，再进行步骤 2 上游核心适配。

### 步骤 1 首轮 GitHub 验证与修正

- 产品提交：`37f5680c1738d03a7e2403e02ec360b8f4c55b43`；[首次兼容工作流](https://github.com/bboytang/Youmi/actions/runs/40241604362)。Python 3.12 的 135 项通过；Python 3.10 的新运行时 9 项通过，但 3 项既有迁移故障注入测试在清理后报 not authorized。
- 原因及修正：这些测试用 `set_authorizer(None)` 清理注入回调，Python 3.10 下未恢复访问；改为显式返回 SQLITE_OK 的清理回调，保持原故障注入及回滚断言，不修改生产迁移、不跳过测试。
- 本地完整后端探索跑到 485 项，但因缺少 CI 专用源文件环境变量报 16 个错误（ASR、TTS、历史 context/memory、音乐源）；没有将其记为完整通过。现有服务器工作流会准备这些固定公开源文件，完整结论以对应提交的 GitHub 结果为准。

### 步骤 1 验收完成与交接

- 最终验证提交：`1f781da9a990433a3dbf1b719f14043133523860`。该提交包含运行时代码 `37f5680c` 以及 Python 3.10 测试清理修正。
- [兼容矩阵](https://github.com/bboytang/Youmi/actions/runs/40241747801)：Python 3.10/3.12 各 135 项通过。
- [完整后端](https://github.com/bboytang/Youmi/actions/runs/40241726242)：491 项及人设传输检查通过。
- [实际安装与真实向量链路](https://github.com/bboytang/Youmi/actions/runs/40241782031)：编码器、向量检索、混合召回、上下文预算、回复/工具及结构化提取全部通过。
- [绑定授权](https://github.com/bboytang/Youmi/actions/runs/40241782024)：Java 192 项及 JAR 构建通过；[Manager 基线](https://github.com/bboytang/Youmi/actions/runs/40241781900)：原有 150 项和基线复现 4 项通过。
- diff 已检查；本次代码与测试单独提交，用户已有 10 个修改文件和 YOUMI_ASTRBOT/ 保留未提交。
- 步骤 1 计划内兼容验收完成；不等于上游核心已移植。步骤 2–7 未完成，真实终端与生产切换未执行；没有 schema 变更、数据库备份或真实消息。
- 下一步：按 handoff.md 的仓储适配入口实施步骤 2，完成后继续更新记录与交接，不需要重复询问是否继续开发。

## 2026-10-25 — 步骤 2A：只读仓储与移植检索，等待 CI

- 用户要求继续下一步；按步骤 2 先完成只读仓储和影子检索，剩余持久化/写入适配归入 2B，未改变完整验收条件。
- 新增 owner-aware canonical 仓储，独立 mode=ro 连接读取既有 memories、meta、sources，保留 ID、内容/证据版本、来源和计数；按 ID 重读也验证 owner 与快照。拒绝 public 回退及伪造 owner。
- 通过 GitHub 连接器重新获取固定上游源码，并与研究快照比对一致后移植 CJK、BM25 分阶段及 RRF 核心；移除 AstrBot 和持久化目录依赖，保留 GPLv3 许可、来源及变更说明，安装器同时携带。
- 运行时新增显式 shadow_retrieve，旧 retrieve 不变。索引仅内存，查询后回库核验；可选复用 BGE，无向量时标注并回退。超限或原生错误不冒充无匹配；取消不释放仍在运行的原生槽位。
- 本地新增 17 项通过；相关回归合计 152 项通过。数据库内容、51 维情感和默认上下文在影子查询前后相同。
- CI 增加原生依赖和双版本覆盖，并为实际安装包/真实 BGE 增加离线影子机制验证及 JSON artifact。准确提交结果待 CI 完成后补充。
- 未变更 schema、未调用真实模型提供者、未操作生产数据库、未部署、未发送消息。功能及预算限制见 shadow-retrieval.md。

### 步骤 2A 验收完成与交接

- 产品提交 `e92439086a61810fc5c9a0e065b45e696a8b8e07` 已推送；[双版本矩阵](https://github.com/bboytang/Youmi/actions/runs/40244047520) 各 152 项通过，[完整后端](https://github.com/bboytang/Youmi/actions/runs/40244047754) 508 项及人设传输通过。
- [真实向量链路](https://github.com/bboytang/Youmi/actions/runs/40244047561) 全部通过，包括安装后新增 BGE/Tantivy 影子机制检查；已核对 artifact SHA、passed=true 和八项机制检查全为 true。
- [embedding 对照](https://github.com/bboytang/Youmi/actions/runs/40244047437)、[绑定授权](https://github.com/bboytang/Youmi/actions/runs/40244047533)、[Manager 基线](https://github.com/bboytang/Youmi/actions/runs/40244047448) 均通过。前者仅证明既有模型任务未回归，不证明新引擎召回质量优于旧引擎。
- 本地 17 项和相关 152 项通过；diff 已检查，原有 10 个用户修改文件及 YOUMI_ASTRBOT/ 未提交、未覆盖。准确验证与观测值见 verification.md。
- 步骤 2A 验收完成，步骤 2 整体尚未完成。下一项 2B：成功回合时的情感快照、认知状态、稳定 ID 合并关系及其增量迁移/写入兼容。新增存储不能绕过完成回合、证据和版本约束。

## 2026-10-25 — 步骤 2B：增量状态存储与写入兼容，等待 CI

- 新增 V11，不改 V1–V10 原文。成功回合角色快照、来源关联、四维认知配置/能量和稳定 ID 合并关系均按 owner 隔离，旧数据不倒推填充。
- QQ effects 和设备/App 完成钩子捕获当时状态；记忆任务仅关联既有来源快照，与事实及任务终态原子提交。旧任务、关闭状态和缺失快照保持未知。
- 初轮故障测试发现新增快照写失败会回滚原情感更新，已在提交前改用保存点隔离。现已验证新增快照故障不阻断既有情感，且不会在后台补造过去状态。
- 认知内部接口支持固定/动态模式、基线/范围/敏感度、能量调整及版本冲突；不修改情感表，不启用自动行为或新模型提供者。合并保留原 ID/来源、校验版本、幂等，并防止旧措辞重复提取或编辑自动恢复退休记录。
- 本地新增 23 项及相关 182 项通过，覆盖成功/失败/重复完成、真实 QQ HTTP 及原生完成钩子、延迟任务、快照故障、重启、隔离、遗忘、合并回滚、V10 迁移中断及兼容旧算法保留升级后新增数据。
- 回归发现新捕获路径不应依赖模型提供者容量属性，已改为直接复核上下文快照和写权限；旧连续性完成测试恢复通过。历史迁移夹具显式移除空 V11 扩展对象，版本断言更新为 11，不放宽生产校验。
- 安装载荷与 CI 已补齐。详细数据契约见 state-persistence.md；等待准确提交的 GitHub 检查后更新交接。未部署、未打开生产库、未新增备份或真实消息。

### 2026-10-26 — 步骤 2B 提交前复核

- 补上重复历史来源不回填新情绪的边界；来源新增时才建立快照关联。新增反向来源/任务索引，避免保留期清理反复扫描所有来源。
- 合并提供 caller-owned 事务原语和当前 job_id 排除项，验证上层失败可完整回滚，不能在后台事务中嵌套独立管理事务。
- 最终新增 25 项本地通过；此前相关 183 项通过，最后两项及合并原语调整后重新跑新增测试通过。完整结论等待当前提交的 GitHub 全套相关检查。

### 步骤 2B 首轮 CI 与夹具修正

- 产品提交 `39fb95b871dd74a051afbfa26863f41edd3a4b3a` 已推送；[双版本检查](https://github.com/bboytang/Youmi/actions/runs/40247944101) 各 177 项通过；[真实向量链路](https://github.com/bboytang/Youmi/actions/runs/40247944077)、绑定授权和 Manager 基线通过。
- [完整后端](https://github.com/bboytang/Youmi/actions/runs/40247944996) 跑到 533 项，其中一个既有 V6 人物迁移夹具未移除 V11 表，模拟旧库后再次迁移时重复建表。补用相同历史夹具清理函数，并将人物测试加入双版本矩阵；生产迁移 SQL 不修改、不放宽。

### 2026-10-26 — 步骤 2B 验收完成与交接

- 最终验证提交 `e2d6627528dde37e3d704cc72604068b9946c73a`，包含产品代码 `39fb95b8` 及历史夹具修正；准确链接与验证范围见 verification.md。
- [双版本矩阵](https://github.com/bboytang/Youmi/actions/runs/40248324979) Python 3.10/3.12 各 189 项通过；[完整后端](https://github.com/bboytang/Youmi/actions/runs/40248324921) 533 项及人设传输通过。
- [实际安装与真实向量链路](https://github.com/bboytang/Youmi/actions/runs/40248335094)、[绑定授权](https://github.com/bboytang/Youmi/actions/runs/40248334937)、[Manager 基线](https://github.com/bboytang/Youmi/actions/runs/40248335099) 全部通过。
- V1–V10 SQL 原文未改；最终 diff 已检查，原有 10 个用户修改文件及 YOUMI_ASTRBOT/ 保留未提交。V11 仅在合成测试/CI 候选中运行，生产数据库未升级、引擎未切换，没有新增生产备份或真实消息。
- 步骤 2 存储与检索适配阶段验收完成，不等于完整记忆系统接管完成。下一步 3：受来源/版本/授权约束的反思提案、画像、笔记和维护编排。保持写入仅在完成回合后，认知调整不能直接改写 51 维情感或人设。

### 2026-10-26 — 步骤 3A 实施与本地验证

- 先写 reflection-actions.md 细化实施范围与成功条件，再增加 memory_reflection.py。反思 `remember`/`revise` 映射到既有来源验证结果 `_schema: 1`，由原 worker 复核授权并原子写入，不建立第二队列/数据库或额外模型请求。
- 保留旧提案、模型提示、任务结果格式、请求上限与 jobs.validate_output 观测入口。新动作只接受版本 1，拒绝混合/未知字段、裸替换、控制状态、未实现的合并/删除等动作。没有来源的模型判断不是证据；不按 impression 丢弃明确动作。
- 新增 18 项本地通过，相关回归共 207 项通过。覆盖同 ID 修订、所有者隔离、伪造来源、撤权、版本变更、敏感/推断待确认、整批校验、两次尝试上限、数据库失败回滚后重启复用结果、认知 impression=1 仍保留四条动作。实际启用且有持久化数据的 51 维情感/认知/长人设在反思前后相同。
- 初轮相关回归发现新增断言使用了 V9 情感表旧名称，修正为 V10 后名称再跑 207 项全通过；产品迁移 SQL 未改。
- 全量安装包加入新模块；双版本 CI 加入新测试、真实向量流水线加入新模块路径触发。准确提交的 CI 结果待后续记录。3B 的画像、笔记、维护及剩余动作仍待实现，当前没有新模型提示适配和引擎激活。
- 未连接生产数据库、未部署、未新增生产备份或外发消息，原有用户修改继续保留。

### 2026-10-26 — 步骤 3A 验收完成与交接

- 产品与验证提交 `04a440ea6583a694a307019e86cadaf807fd82ee` 已推送；[Python 双版本](https://github.com/bboytang/Youmi/actions/runs/40249592551) 各 207 项、[完整后端](https://github.com/bboytang/Youmi/actions/runs/40249592533) 551 项及人设传输通过。
- [实际安装的真实向量链路](https://github.com/bboytang/Youmi/actions/runs/40249592608) 所有阶段通过；[绑定授权](https://github.com/bboytang/Youmi/actions/runs/40249592534) 192 项及 JAR 构建通过；[Manager 基线](https://github.com/bboytang/Youmi/actions/runs/40249592790) 原有 150 项及基线 4 项通过。
- 已检查最终 diff，产品新增仅反思校验边界与 worker 导入、安装载荷、CI 覆盖；V1–V11 SQL、情感权威和人设代码未修改。原有用户修改仍未提交，生产未升级、未切换。
- 下一步为 3B 画像/笔记/维护及剩余动作编排。模型提示尚未适配新格式，本次不宣称自动反思、整个步骤 3 或完整记忆接管完成；实际新提案机制用合成输入验证，没有真实模型调用或外发消息。

### 2026-10-26 — 步骤 3B 实施与本地验证

- 先写 profile-notes-maintenance.md 明确范围，再增加 V12 表、证据关联与级联清理；V1–V11 原文不改。画像分类、笔记、退役/合并预览使用相同 owner 和稳定条目 ID，保存来源版本而不是第二份事实正文。
- 反思增加 profile/note/propose_retirement/propose_merge，共享 16 动作上限和既有完成任务事务。仅接受当前所属完整事实原句，标题必须有字面依据；模型不提供条目/来源版本，服务端捕获并在提交时 CAS。不把整理当作新用户事实，不能同批修改自己的整理证据。
- 新增内部运行时读取和带版本删除，复核权限与读快照；memory_recall 关闭只隐藏不清除。明确删除条目保留事实，沿用已有 WAL 清理。来源改动/删除/转待确认/合并退役都会清除整个整理结果及标题；不同智能体互不影响。
- 新增 24 项本地通过；补充最终旧算法回退用例前，相关回归 232 项通过。覆盖 128 条容量、更新稳定 ID、跨设备/智能体、撤权、并发来源/条目版本、整批回滚后重启复用、维护仅预览、V11 迁移失败/进程中断、兼容旧算法保留条目和最新情感、51 维情感/认知/长人设不变。
- 历史迁移夹具先移除空 V12 对象，版本断言与完整/增量安装载荷同步；生产迁移校验未放宽。实际安装验证脚本加入合成反思生成的画像/笔记/候选及来源失效清理，等待准确提交 GitHub 验证。
- 未打开生产库、未部署、未切换引擎、未新增备份或外发消息。当前默认提取器不自动生成新格式，自动反思生成适配和维护调度仍待后续接入；没有宣称完整步骤 3 或整个迁移已完成。

### 2026-10-26 — 步骤 3B 后端机制验收完成与交接

- 产品与验证提交 `033cfd05efc5a1c4cba2ae3c532af4f031558564` 已推送。[双 Python](https://github.com/bboytang/Youmi/actions/runs/40253660358) 各 231 项、[完整后端](https://github.com/bboytang/Youmi/actions/runs/40253660364) 575 项及人设传输通过。
- [实际 BGE 安装链路](https://github.com/bboytang/Youmi/actions/runs/40253660362) 全部阶段通过；下载其 109694 字节结果 artifact，memory-shadow-results.json 的 commit 与本次准确提交一致、passed=true，新增 installed_reflection_artifacts/artifact_source_invalidation/maintenance_preview_only 及原有八项检查均为 true。
- [绑定授权](https://github.com/bboytang/Youmi/actions/runs/40253660365) 192 项及 JAR 构建通过，[Manager 基线](https://github.com/bboytang/Youmi/actions/runs/40253660313) 原有 150 项及基线 4 项通过；[提取器语法](https://github.com/bboytang/Youmi/actions/runs/40253660314) 通过，live 作业按 push 条件跳过，没有真实模型调用。
- 最终 diff 已检查，原有 10 个用户修改及 YOUMI_ASTRBOT/ 未覆盖或提交。V1–V11 未改，V12 本次提交后冻结；没有连接生产库、部署、备份或发送消息。
- 下一步仍是步骤 3 的反思生成适配和维护调度，再进入步骤 4 联动。当前整理结果是来源支持的原句视图，尚无模型自动整理、自由文件笔记、维护自动执行或管理页面；不得把后端机制验收当作完整系统上线。

### 2026-10-26 — 步骤 3C 实施与本地验证

- 先写 reflection-generation.md 固化范围，再增加 V13：按 owner 的反思开关/间隔/版本/最近成功签名和任务维护名额。默认关闭，原成功回合任务触发，不建立空闲模型任务或额外请求。V1–V12 保持原文。
- LLMMemoryExtractor 复用原 factory、Provider、SDK/HTTP 计量、前台优先槽位与两次请求上限；启用后提供版本 1 动作，只有到期任务提供整理工具。明确更正免模型快速路径保持；只读签名基于原有最多八条提取参考，不把这次实现当全库扫描。
- 每 owner 只有一个活动维护名额；入队绑定配置版本，失败重试保留名额，终态释放，成功同事务更新时钟和来源签名。无新来源不反复整理；配置改变取消旧任务，清空记忆保留配置但重置执行历史。无来源的后台编辑不触发整理请求。
- 新增 20 项，相关 285 项本地通过。覆盖真实 SDK+MockTransport 的一次计量请求、版本契约、来源/配置过时、冷却/去重、重试上限、原子回滚与重启、不变的 51 维情感及 V12 迁移失败/进程中断。初轮发现旧普通列表调用在返回时被写新属性，修正为仅启用模式写属性后全部通过。
- GitHub 双版本矩阵加入全部 17 项既有 V2 计量回归；安装链路加入固定实际 Provider/SDK、MockTransport 和已有 BGE 环境中的启用生成验证，等待准确提交检查。V2 仍要求已审阅的单次发送 Provider，未为了 JSON 支持降级计量边界。
- 未部署、未升级生产库、未启用任何生产智能体、未新增备份或外发消息；模型响应全部为测试合成内容，不是召回/反思质量或真实端验收。

### 步骤 3C 首轮 CI 与 Python 3.10 夹具修正

- 产品提交 `299fc9947c9ec1d4e30f6adae98f46e2823e4449` 的完整后端 595 项及人设传输通过；双版本中 3.12 的 268 项通过，3.10 在新纳入矩阵的既有 V2 故障测试中失败。
- `test_reservation_transaction_failure_does_not_call_sdk` 使用 set_authorizer(None) 清理注入，Python 3.10 后续读仍报 not authorized；按其他夹具已有方式改用返回 SQLITE_OK 的回调。保留故障注入、原断言及计量边界，不跳过测试，不修改产品代码或迁移 SQL。待修正提交完整相关 CI 验证。
- 首个夹具修正提交 `0eb4e066` 的 finally 内缩进漏写，本地单测已报语法错，但串联命令仍继续提交；立即补正缩进，独立确认单测通过后再提交。该提交不能作为验证通过证据。

### 2026-10-26 — 步骤 3C 机制验收完成与交接

- 最终验证提交 `c0a4a288af38dfe7c1774f41052040f12faa633b`，产品代码保持 `299fc994`。修正单测本地通过后，最终 [双 Python](https://github.com/bboytang/Youmi/actions/runs/40255594310) 各 268 项、[完整后端](https://github.com/bboytang/Youmi/actions/runs/40255594352) 595 项及人设传输通过。
- 对准确最终提交手动补齐了不由测试路径触发的检查：[实际 BGE/安装链路](https://github.com/bboytang/Youmi/actions/runs/40255631366)、[绑定授权](https://github.com/bboytang/Youmi/actions/runs/40255635550) 192 项及 JAR、[Manager 基线](https://github.com/bboytang/Youmi/actions/runs/40255639489) 原有 150 项及基线 4 项全部通过。没有调用 live 模型工作流。
- 下载最终 artifact 并核对 extraction-results.json：code_sha 与最终提交一致，passed=true；reflection_generation 的 installed_provider、real_sdk_mock_transport、existing_request_budget、completed_job、maintenance_clock_committed、default_recall_unchanged 均为 true，remote_model_calls=0。实际模型响应仍是测试合成内容，不能作为质量/风格证明。
- 最终 diff 已复核，V1–V12 未改、V13 冻结，10 个原有用户修改及 YOUMI_ASTRBOT/ 未覆盖或提交；生产未连接、未升级、未开启反思、未新增备份或发送消息。
- 步骤 3A–3C 后端机制验收完成。下一步 4：版本化记忆证据、单次情感计算、冷却及受控排序；管理 UI、索引恢复、真实质量对照与获授权终端验收仍在切换前完成，不能提前上线。

### 2026-10-26 — 步骤 4 实施与本地验证

- 先写 emotion-linkage.md，再增加 V14：owner 级联动总开关、双向开关、刺激权重/排序上限/冷却及按内容版本的冷却记录。方向默认开启、候选总开关关闭，继承记忆召回与现代情感总开关；V1–V13 原文不改。
- 派生 YoumiEmotionContextBuilder 接入共用 builder_for，未编辑用户已有 context.py/astrbot_api.py。只从实际选入回复上下文且有来源的所属事实提供内部评价证据，不将内部联动字段注入回复 prompt。旧三维及旧 changes 格式保持。
- 版本 2 分开校验当前原话和记忆完整原句/ID/内容/证据版本；最多八个不同维度。原始 delta 合并后一次执行既有恢复、敏感度、固定/锁定与限幅。完成时重验来源/配置/冷却，成功才保存；冷却写故障保存点回滚记忆贡献并提交本轮变化。
- 历史记录只保存来源 ID/版本/原因，关联实际 interaction 历史 ID 及回合，不保存新引用正文，不把历史情绪认作事实因果。相同内容版本的新佐证不会重置冷却；遗忘级联清理，清空保留配置。
- 情感仅对显式影子检索已有合格候选作至多 10% 加权，历史快照需匹配内容版本，未知保持未知。否定/纠错/时间查询不改顺序，原默认召回仍委派旧算法。
- 新增 24 项及相关 294 项本地通过；最后补历史关联字段后新增 24 项再次通过。覆盖全 51 维可调、单次限幅、固定/锁定/关闭、主开关、冷却/重启/佐证、配置及来源变化、故障隔离、排序界限、原生完成及 QQ 成功/重试/失败、V13 迁移失败/进程中断。真实安装/BGE 验证脚本补充联动/冷却/排序及原召回不变的合成机制，待准确提交 CI。
- 未部署、未升级生产库、未开启生产开关、未新增备份或真实消息，原始人设和已有用户修改保留。

### 2026-10-26 — 步骤 4 后端机制验收完成与交接

- 准确产品及验证提交 `43c15bde7db0ca94ea02bdb64d1021a51b1e8dc0`。[双 Python](https://github.com/bboytang/Youmi/actions/runs/40258355178) 各 292 项、[完整后端](https://github.com/bboytang/Youmi/actions/runs/40258355188) 619 项及人设传输通过。
- [真实 BGE/安装链路](https://github.com/bboytang/Youmi/actions/runs/40258355198) 全部通过；下载结果文件并核对 SHA 与 passed=true，新增 versioned_memory_emotion/cooldown_committed_once/emotion_ranking_bounded/legacy_recall_still_unchanged 均 true。旧“51维保持”断言属于只读影子阶段，启用联动后的 joy=22 是预期受控变化，不是全流程数值不变。
- [绑定授权](https://github.com/bboytang/Youmi/actions/runs/40258355253) Java 192 项及 JAR 构建通过；[Manager 基线](https://github.com/bboytang/Youmi/actions/runs/40258355484) 原有 150 项及基线 4 项通过。模型评价使用合成提案，没有真实模型质量或真实终端结论。
- 最终 diff 已复核，V1–V13 未改，V14 冻结。原有 10 个用户修改和 YOUMI_ASTRBOT/ 保留未提交。未连接生产库、部署、备份或外发消息。
- 下一步为 5 管理页/接口及审计展示；认知自动策略、索引恢复、质量影子对照与切换回退仍在剩余阶段，不提前启用生产或替换旧默认召回。

### 2026-10-26 — 步骤 5A 管理接口实施及首轮验证

- 先写 management.md，将步骤 5 分为 5A 管理接口与 5B 独立页面。方案提交 `3e80cba9`，接口首轮产品提交 `38eab3be5ca166fbe768b67f6c61f9c42a851367`。整个步骤 5 尚未完成。
- 新增 `/agent/{agentId}/memory-system`，复用原登录、HTTPS、manager 前后所有者核验和内部管理端点；服务端证明的 owner 才能执行配置、查看来源与整理结果、删除整理条目和手动合并。没有伪造设备 scope、模型管理工具或新的数据权威。
- 列表/来源每页 50 条，读操作同一 SQLite 快照；返回内容/证据版本、revision、待确认/退役状态和最多 50 条相关合并关系。陌生、跨智能体或已删除来源统一不可用，不从旧记录恢复正文。
- 反思、认知、情感联动的配置复用原有 CAS；整理删除保留事实，合并保留稳定 ID、原来源与原幂等边界。status 明示默认旧引擎、排序仅影子、认知自动策略/固定条目/候选自动执行未实现，不随读取启用开关。
- 新增 15 项 Python 路由测试和 2 项 Java 登录代理测试。本地新增及相关 123 项通过，涵盖长人设、非默认情感配置保持、各智能体隔离、来源分页、冲突、撤权、来源失效和失败回滚。
- 首轮 GitHub 双 Python 各 307 项、完整后端 634 项及人设传输通过。Java 194 项中新增冲突测试失败：把 409 传给 RenException(int,String...)，该构造器查询不存在的翻译词条，导致实际响应变成 500。检查固定上游 Result/RenException 源码后，修正为 Result.error(code,msg)，保留原测试且补断言冲突提示。修正提交 `8d0e7209`，等待准确最终提交 CI，不能以首轮 JAR 能构建代替测试通过。
- 安装载荷加入 memory_management.py；V1–V14、人设/情感权威及原完成回合路径均未改。未连接生产、未部署、未启用开关、未新增备份或真实消息；原有 10 个用户修改和 YOUMI_ASTRBOT/ 保持未提交。

### 2026-10-26 — 步骤 5A 接口验收完成与交接

- 最终产品及验证提交 `8d0e7209215a128d888c142aebca612c125f5c4b`。[双 Python](https://github.com/bboytang/Youmi/actions/runs/40260067662) 各 307 项、[完整后端](https://github.com/bboytang/Youmi/actions/runs/40260067690) 634 项及人设传输通过。
- [绑定授权及管理代理](https://github.com/bboytang/Youmi/actions/runs/40260067650) Java 194 项及 JAR 全部通过，新路由冲突码和提示断言生效；[Manager 基线](https://github.com/bboytang/Youmi/actions/runs/40260067648) 150 项及基线 4 项通过。
- 为最终 SHA 手动补齐 [实际安装/BGE 链路](https://github.com/bboytang/Youmi/actions/runs/40260068662)，全部阶段通过。下载 109978 字节结果 artifact，memory-shadow-results.json 的 commit、passed 和 15 项检查均核对通过；合成机制不是模型质量或生产性能证明。
- 最终 diff 已复核；V1–V14 无变化。保留且未提交 10 个用户修改和 YOUMI_ASTRBOT/，未操作生产、备份或外发消息。
- 下一步 5B：复用原 Vue2/ElementUI 和源码补丁安装，在导航/智能体卡片添加“记忆系统”，接入新 API、草稿/保存失败/版本冲突、来源详情和情感记录跳转，完成四浏览器及手机模拟验收。新页面事实编辑/确认/删除需先补同事务版本校验，不能直接把旧无 CAS 写入包装成新版。整个步骤 5 与后续迁移/切换仍未完成。

### 2026-10-26 — 步骤 5B 页面实施与本地验证

- 先补 management.md 实施顺序和成功标准，计划提交 `25068cb3`。首轮产品 `f6329ed3020c54d74fb8b9901e00bc76f4bf9489`；后续补齐当前智能体导航和请求期间切换测试的提交 `340b5e56fc4feaffebbbe70eed49a21707aac64c`，等待最终浏览器与相关 GitHub 检查。
- 新增 YoumiMemorySystem.vue；通过原源码补丁入口加入导航、智能体卡片和独立路由，原弹窗保持兼容。按智能体管理记忆/来源/合并关系、画像/笔记/维护预览、反思配置、独立四维认知配置与手动能量、双向情感联动参数。保存各组不会覆盖其他组草稿；失败/冲突保留并阻止旧版本重复保存，切换/离开确认未保存内容，请求未结束时禁止切换。
- 事实编辑/确认/删除新增强制 expected（revision/content_version/evidence_version），校验位于原 BEGIN IMMEDIATE 内、先于写操作；原非管理调用省略该可选参数时行为不变。合并/删除逐条确认，待确认和退役状态不被 UI 当成有效事实。改文撤回旧来源/整理，删除级联清理，不新增正文副本。
- 情感 memory_link 历史显示处理原因及统一限幅的含义，链接当前智能体/当前来源；未知或已删除来源显示不可用，不恢复旧审计原文。页面标明认知自动策略尚未接入、当前排序仅影子检索，不因打开自动启用。
- 新增 4 项后端 CAS 用例，本地新增及相关 85 项通过；复核并发进程/新来源、外部智能体、写锁内校验、删除级联及旧调用兼容。原 5A 保持情感状态的用例曾因真实时钟跨秒使 now 字段不同而失败，冻结合成测试时钟后保留全部断言通过，不调整产品情感算法。
- 使用已有本地 Vue2 编译器/Babel 检查两页模板及脚本通过；没有安装大型新依赖。新交互测试覆盖独立保存/智能体隔离、登录、409/500 草稿、切换/离开、编辑/删除取消和版本、证据失效、合并确认、四维与手机布局、外部智能体、深链接、导航当前智能体和进行中请求；原情感测试新增审计跳转。
- V1–V14、完整人设、现有情感权威及用户的 10 个已有修改均未改写；未连接生产、部署、备份或发送真实消息。此记录不是浏览器测试完成或整个移植完成证明。

### 步骤 5B 首轮浏览器结果与修正

- 首轮前端 https://github.com/bboytang/Youmi/actions/runs/40261564480 的原有回归、基线比较及生产构建通过，浏览器共 156 项，128 通过、28 失败。四个浏览器重复的七类失败均保留原断言修正：输入框 aria-label 未传入 ElementUI textarea；宽泛文本定位匹配到消息框残留的隐藏节点；通用 alert 定位混入公共配置连接提示；新增审计测试的认知夹具遗漏四维配置。
- 输入框改用现有 ElementUI 的 label 属性，测试精确匹配草稿状态并限定页面错误；新记忆页夹具按已有模式先代理静态资源、后安装 mockBackend，使未单独覆盖的 API 仍被合成后端接管，不再请求本地静态服务上的公共配置接口。没有跳过断言或放宽超时。
- 静态复核还发现从当前页面再次点击不带 query 的导航应保留当前智能体，`340b5e56` 已修正并增加导航/在途读取测试；`e2766fabe3309468f1a9d55510b6cb6be0a498ce` 包含标签与定位修正，等待最终完整浏览器验收。后端 51 维情感算法和原文人设未变。
- 中间 `340b5e56` 的前端 https://github.com/bboytang/Youmi/actions/runs/40261876503 共 164 项，131 通过、33 失败；标签/定位修正尚未包含在这个提交中，另有一个原有测试的浏览器上下文清理错误，不作为通过证据。
- `e2766fab` 前端 https://github.com/bboytang/Youmi/actions/runs/40262385415 共 164 项，151 通过、13 失败。来源编辑/删除、合并、深链接、手机与在途读取已通过；剩余为未选中时框架省略 aria-checked 导致的断言错误、连续弹窗/地址变化导致确认框关闭，以及一个原有静态代理响应在上下文销毁时被释放的错误。改为检查真实 checkbox 状态、等待弹窗关闭、先注册静态代理后安装合成 API、afterEach 等待路由处理完成；没有改变产品状态判定或增加测试重试。
- 检查 [ElementUI v2.15.14 MessageBox](https://github.com/ElemeFE/element/blob/v2.15.14/packages/message-box/src/main.vue) 及其 main.js，确认通用弹窗默认监听 hashchange 且复用全局实例；仅设置一次选项不能稳妥覆盖先前弹窗的监听状态。`55ed2b02` 为选项修正中间态，最终 `088033fe` 用页面自己的 el-dialog 承接草稿放弃的 Promise，关闭/取消/销毁都明确结束等待，保留地址变化取消离开的浏览器断言。记忆删除/合并等逐条确认仍沿用原通用弹窗。
- `249afd91` 前端 https://github.com/bboytang/Youmi/actions/runs/40263117655 共 164 项，160 通过、4 失败，仅剩四个浏览器的地址变化草稿确认用例，证明仅等待前一个弹窗关闭并不足以解决问题；没有删除该用例。`088033fe` 的完整前端验收仍在运行，其准确提交的后端、双 Python、Java、Manager 基线及实际安装链路已通过，待浏览器结束后再更新最终交接。

### 2026-10-26 — 步骤 5B 页面验收完成与交接

- 准确最终产品及验证提交 `088033fe827b102baac61076da713911752f503c`。[前端](https://github.com/bboytang/Youmi/actions/runs/40263498051) 契约 9 项、组件 53 项、快照 13 项、四浏览器 164 项全部通过；基线字节比较 48/49，严格模块/依赖/CSS 语义比较通过，没有忽略差异。前端源码/静态/报告 artifact 信息见 verification.md，本机未整体下载大型前端产物。
- [双 Python](https://github.com/bboytang/Youmi/actions/runs/40263572354) 各 311 项、[完整后端](https://github.com/bboytang/Youmi/actions/runs/40263498050) 638 项及人设传输通过；[绑定授权](https://github.com/bboytang/Youmi/actions/runs/40263574857) Java 194 项及 JAR，[Manager 基线](https://github.com/bboytang/Youmi/actions/runs/40263577050) 150 项及基线 4 项通过。
- [实际安装/BGE 链路](https://github.com/bboytang/Youmi/actions/runs/40263580515) 全部通过；下载 109995 字节结果 artifact，核对准确 SHA、passed=true 与全部 15 项检查为 true。该合成用例不是新引擎质量或生产性能证明。
- 最终 diff 已检查；V1–V14 未改，人设、51 维情感计算与原完成协议未改，原有用户修改均未覆盖或提交。期间出现的用户并行新文件 `.github/workflows/youmi-qq-reply.yml` 及 YOUMI_ASTRBOT/ 同样保留未提交。未连接生产、部署、备份或外发真实消息。
- 步骤 5A–5B 机制和页面验收完成。下一步 6：在不改变旧唯一写入/默认召回的前提下，补持久化索引恢复与增量同步、认知自动策略及尚缺的固定条目契约，准备可重复迁移和冻结中文影子质量/预算对照。不能把 UI 可调认知当作自动策略已实现，也不能在质量与回退验收前切换生产。真实手机与真实 QQ/App/设备、模型质量仍待后续获授权验收。

### 2026-10-26 — 步骤 6A 方案读取、实施与本地验证

- 按用户要求重新读取最新 plan.md、worklog.md、handoff.md，并核对 AGENTS.md、当前分支和未提交文件。保持 `feature/emotional-system`，原有并行 QQ/AstrBot 修改、新 workflow 与 YOUMI_ASTRBOT/ 不覆盖或提交。先新增 index-recovery.md，计划提交 `ecb1cc70`。
- 产品提交 `514c19ee13615b80459407a3528f6edeb8225a98`：YoumiMemoryRuntime 新增仅内部可传的 shadow_cache_dir，默认 None 保持 RAM 影子与旧默认回复。显式合成数据评估按数据库绝对路径/owner/persona 哈希隔离私有目录，同步/搜索持有跨进程锁；新增记录增量追加，改文/元数据变化/删除/待确认/退役重建分区以丢弃旧段文件。
- 每次仍完整捕获并核验有界 canonical 快照，清单只存 ID/文本指纹；新佐证不改检索文本时复用索引，来源和版本当场从 canonical 读取。选择全快照对账而非新增 outbox/schema，减少对旧写入路径侵入，保留每次全语料扫描的性能限制。V1–V14 不改。
- dirty 标记在提交前 fsync，Tantivy commit/reload 后原子清单/目录 fsync，再清 dirty。缺失/坏清单、坏索引、异常文档数和进程中断从当前事实重建；磁盘错误明确 unavailable。禁用/空语料的显式影子查询同步空索引；默认调用和鉴权失败不建目录。正常情感、认知、人设和完成协议不变。
- 新增 16 项真实 SQLite/Tantivy 测试及相关 105 项本地通过；包含真实子进程 commit 后 os._exit、真实跨进程锁、未变时不建 writer、追加/佐证、修正/标签/删除/待确认/合并/清空/关闭、损坏恢复、磁盘错误、撤权/查询中删除、原生取消后槽位保持。增加安装包模块和实际 BGE 验证脚本的重开/dirty恢复/canonical不变检查，准确提交 CI 运行中。
- 本轮沙箱更新将 .git 设为只读且限制网络；Git 提交/推送、固定依赖下载及 GitHub 只读检查通过工具自动审批后执行。受限沙箱内 asyncio.to_thread 完成后不能正常唤醒事件循环，12 秒 faulthandler 显示工作线程已空闲、主线程仍在 selector；相同合成用例在获准的沙箱外运行全部通过，未为了本地环境修改产品异步流程。没有生产访问或真实模型请求。
- 重要限制：闲置缓存只在下一次显式影子调用时对账，不会在 canonical 遗忘完成瞬间清理。因此该磁盘模式只限合成数据离线评估，不得接到生产私有数据或默认工厂。后续必须补即时失效/清理、启动生命周期，再做质量/性能和切换验收；不能将本阶段标成生产持久化索引完成。
- 首轮 GitHub 双 Python 各 327 项、完整后端 654 项及人设传输已通过。实际安装工作流 40265772529 首次在旧 verify_youmi_vectors.py 的并发短句编码中失败，尚未执行本轮新增磁盘检查；下载 3031 字节合成报告，18 次短句任务中 1 次为 EmbeddingUnavailable/embedding_busy，其余成功结果全部 bitwise_equal，长句并发也全部一致。没有修改编码器、并发槽位或超时，也没有删除断言；对同一准确提交和原限制手动重跑失败作业，等待结果。不把初次失败写作通过。
- 同 SHA 的第二次安装验证仍在短句 case 3 出现 embedding_busy，不能再称为已证实的单次瞬时故障。代码比较确认本轮未修改 embedding.py 或 verify_youmi_vectors.py；根因/当前 runner 负载影响尚未完整归因，作为切换前必须核对的未解决项记录。
- 为避免一个独立检查的失败遮住新增路径证据，CI 提交 `1a4e7277ad812c2467e4d057f2257744f55a36a6` 给安装步骤标记 runtime，后续五个互相独立的安装检查在安装成功且任务未取消时继续执行。没有 continue-on-error，没有修改任何原断言、并发/资源/超时限制，原检查失败仍让工作流失败。该最终提交已补触发双 Python、完整后端、Java 绑定及 Manager 基线，等待实际结果；产品机制代码仍为 `514c19ee`。

### 2026-10-26 — 步骤 6A 合成评估机制验收与交接

- 最终准确验证提交 `1a4e7277ad812c2467e4d057f2257744f55a36a6`，产品机制仍为 `514c19ee`。[双 Python](https://github.com/bboytang/Youmi/actions/runs/40266267768) 各 327 项、[完整后端](https://github.com/bboytang/Youmi/actions/runs/40266272350) 654 项及人设传输通过。
- [实际安装/BGE](https://github.com/bboytang/Youmi/actions/runs/40266257468) 全部阶段通过，本次原并发编码检查也通过，未修改编码器代码/阈值。下载 109881 字节合成结果，准确 SHA 一致、vector 与 shadow 的 passed=true，全部 18 项影子检查为 true；短句 18 次及长句 4 次编码全部 bitwise_equal。之前两次 embedding_busy 不被抹去，未声称其根因已修复，保留给后续容量评估。
- [Java 绑定](https://github.com/bboytang/Youmi/actions/runs/40266277315) 194 项及 JAR，[Manager 基线](https://github.com/bboytang/Youmi/actions/runs/40266281717) 150 项及基线 4 项通过。前端未改，没有重跑浏览器构建；本轮不涉及固件或物理硬件修改。
- 最终 diff 复核通过，V1–V14 未改，未覆盖/提交并行 QQ/AstrBot 文件。生产未连接、部署或新建缓存，没有新增备份或外发消息。普通工厂依然不设置 shadow_cache_dir；默认召回继续旧算法。
- 该成果只验收 6A 的显式合成评估缓存首段。下一步应先补生产生命周期契约与实现：缓存目录的注册/授权、canonical 完成写事务的失效/遗忘/清空清理、启动/跨进程恢复及失败可追溯性，再判断是否需要 V15 outbox。闲置缓存读时对账不能冒充立即遗忘。然后继续 6B 认知自动策略/固定条目和 6C 中文质量、预算、性能、可重复迁移；步骤 6 和最终切换仍未完成。

### 2026-10-26 — 6A 生产生命周期基础：实施前记录

- 重新读取最新计划、工作记录、交接和 index-recovery.md，核对分支及并行工作。应用云运行环境技能；当前无 environment_status 工具，策略快照若缺失不推断网络就绪。
- 核对实际写入和 schema 后，第二段先实现 V15 同事务失效账本，避免只靠单进程回调丢掉跨进程/中断任务；详细契约先写入 index-recovery.md。仅登记/有界读取/代次 CAS 确认，不注册目录、不执行删除、不用于生产。
- 验收完成后继续受控目录及实际清理执行；不能将账本通过当作立即遗忘已完成。保留全部并行 QQ/AstrBot 工作，不启用新引擎。

### 2026-10-26 — 6A V15 事务失效账本实现及本地验证

- 计划先提交为 `2916e7c7`。新增 owner 级 generation/cleaned_generation 账本与触发器：首次升级已有 owner 待清理，合法内容 epoch/owner revision/paused 变化同事务推进代次；每 owner 一行，不复制事实或缓存路径。不修改事实/情感写入算法，不启动清理线程。
- 新增内部 YoumiMemoryCacheInvalidations：有界读取最多100行，清理确认按 generation CAS，重复/旧代次返回 false，拒绝借用尚未提交的事实事务。没有 HTTP/模型入口，也没有实际运行时调用确认；只有后续清理器真正完成所有注册分区删除后才能确认。
- 新增12项真实SQLite测试：事务回滚、独立写进程提交/未提交后退出、重开恢复、旧代次竞争、owner隔离、清空/暂停/开关、来源与状态撤回、有界读取、约束、V14迁移保持与幂等、失败/进程中断恢复、冻结V14 loader拒绝V15。三种安装载荷加入V15；全量/情感安装同时加入内部模块。实际安装/BGE脚本增加失效持久化和旧代次拒绝两项检查，待CI。
- 首轮相关143项中两项仍期待最新schema=14而失败；更新这两处最新版本断言为15，保留原冻结历史版本与迁移失败断言。再次相关143项全部通过；新增及安装/整理相关38项也通过。没有调整产品算法掩盖失败。
- V1–V14内容保持不变；所有并行QQ/AstrBot文件保留。尚未注册目录、执行磁盘清理或接入启动恢复；不得据此宣称即时遗忘/生产生命周期已完成，生产未连接或切换。
- 首轮准确提交 `6459fe6dd8ee743e6cd811c971ea8d7207181407` 的双 Python 工作流 40268306912 中，Python3.10 新增故障注入用例在恢复 `set_authorizer(None)` 后 iterdump 仍报 not authorized；这是旧 Python sqlite3 清除授权器的兼容问题。改用仓库其他迁移用例已使用的显式 SQLITE_OK 回调恢复，保留原故障注入、全量dump回滚及重试断言，不修改产品 SQL 或迁移校验。后续以修正准确提交重新验证。

### 2026-10-26 — 6A 第二段 V15 账本基础验收与交接

- 最终准确验证提交 `879e62e2db089dcb75b2783c642da0d157c2a5f6`，[双Python](https://github.com/bboytang/Youmi/actions/runs/40268471662) 各339项、[完整后端](https://github.com/bboytang/Youmi/actions/runs/40268471562) 666项及人设传输通过。
- [实际安装/BGE/Tantivy](https://github.com/bboytang/Youmi/actions/runs/40268483056) 全部阶段通过；小型artifact109914字节已下载至 /tmp/youmi-invalidation-final-results，准确SHA及vector/shadow passed=true均核对，全部20项影子检查真，含新增失效重开保持和拒绝旧代次确认。
- [Java绑定](https://github.com/bboytang/Youmi/actions/runs/40268485851)194项及JAR、[Manager基线](https://github.com/bboytang/Youmi/actions/runs/40268488555)150项及基线4项全部通过。首轮3.10授权器恢复失败已完整记录，最终没有放宽任何断言。
- 最终diff已复核；V1–V14、人物原文/情感算法与并行QQ/AstrBot工作未改写或提交。V15冻结。没有生产访问、升级、新建生产缓存、备份或真实消息；目录注册仍未实现。无固件/前端修改，不重复无关构建，也无真机验收结论。
- 更新方案、index-recovery、验证和交接。下一步继续6A受控目录注册及真正清理执行器、写入成功/清理失败的可见语义、跨进程搜索/删除锁序、启动恢复和失败重试；账本不代表立即遗忘或生产索引已完成。6B认知自动策略/固定条目、6C质量/性能/预算与迁移、7切换回退仍待后续。

### 2026-10-26 — 6A 受控目录与清理实施前记录

- 重读最新方案/交接/工作记录及代码，保留并行QQ/AstrBot修改。接续V15，先写入受控目录、稳定锁、提交回调、查询前后版本栅栏、失败状态与启动重试契约。
- 新增V16注册表以保留孤立owner分区；默认不注册/不启用。清理必须在SQLite提交之后，不让文件故障回滚原情感；锁忙明确pending。原始外部sqlite连接只能保证持久化失效，不能宣称立即删除。生产与默认回复保持原状。

### 2026-10-26 — 6A 受控清理机制实现及本地验证

- 方案提交 `622648f0`。新增V16注册表（owner、writing中断标记、错误类别；无任意路径/正文），不随owner删除级联消失。内部 managed_shadow_cache=True 只使用固定私有根，经过仓储授权/快照后注册，默认工厂不传此参数；原任意临时路径模式仍仅限合成评估。
- MemoryStore连接同时覆盖显式commit与SQLite上下文成功提交回调，失败/回滚不清理，启动及后续提交有界轮换恢复。未注册时不访问缓存文件系统；故障不回滚已经提交的事实/情感。前台编辑/遗忘/清空另直接处理目标分区，避免被100行批次遮住。
- 搜索/清理共用稳定writer.lock，清理保留空锁文件及目录，移除其他历史段/清单/临时或未知文件并fsync后才确认代次。根/分区/锁/索引符号链接拒绝，清理内链接只unlink不跟随。独立短SQLite事务不持写锁等待文件锁。
- 受控搜索持锁核对canonical版本，写原生索引前持久化writing=1，成功后复核；中途修改清掉本次旧片段并拒绝结果。进程退出、原生/删除/fsync/确认失败保留可恢复状态。状态为not_registered/complete/pending，管理接口/旧管理响应提供状态；saved指事实已保存，不表示派生清理完成。当前不增加生产前端启用入口，真实展示仍待切换验收。
- 新增25项真实SQLite/Tantivy测试全部通过，含真实子进程退出/锁忙/另一MemoryStore写入、旧快照不复活、提交/回滚/空清空、超过批次的前台目标、故障/CAS/孤立owner/符号链接/非默认51维保持、V15升级/中断/失败及旧loader拒绝。首轮两项夹具错误（普通召回前未建向量、待撤回来源已由前次编辑删除）已修正，保留原断言。相关68项、另一组158项及真实冻结旧MemoryStore迁移8项通过，完整兼容矩阵本地364项也已通过。
- 三种安装载荷加入V16，全量/情感入口带新协调器；原V1–V15冻结不改。实际安装脚本增加managed_commit_purge / managed_no_stale_rewrite / managed_startup_recovery，待准确提交CI。
- 受控模式当前安全策略为失效后清空整个分区，新增事实也可能导致重建；不能将其宣称为生产增量性能达标。原临时合成模式继续新增追加。原始外部sqlite连接只保证失效账本，清理由启动/后续提交/显式恢复负责；数据库搬迁须先清原受控根。
- 生产未连接/注册/部署/启用，无生产缓存或备份，无真实模型/QQ消息，所有并行工作保留。

### 2026-10-26 — 6A V16受控清理后端机制验收及交接

- 准确产品/验证提交 `56aefbce140b3fd0c48080487101ac00c833c9f8`。[双Python](https://github.com/bboytang/Youmi/actions/runs/40271011502)各364项，[完整后端](https://github.com/bboytang/Youmi/actions/runs/40271011453)691项及人设传输通过。
- [实际安装/BGE/Tantivy](https://github.com/bboytang/Youmi/actions/runs/40271011495)全阶段通过；下载109916字节artifact（ID12627963206）至/tmp/youmi-managed-cache-final-results，vector/code_sha和shadow/commit均为准确SHA且passed=true。23项影子机制全真，新增提交清理/旧快照不复活/启动恢复三项。
- [Java授权](https://github.com/bboytang/Youmi/actions/runs/40271011455)194项及JAR，[Manager基线](https://github.com/bboytang/Youmi/actions/runs/40271011484)150项及基线4项全部通过。本轮准确产品SHA首次五组CI全通过；本地夹具首轮失败保留记录，不作为通过。
- 最终diff复核，V1–V15、人物原文与原情感算法未改，V16冻结；所有并行QQ/AstrBot文件未覆盖/提交。没有生产连接/升级/目录注册/缓存/备份或真实消息，无前端/固件/真机验收。
- 更新方案/管理契约/索引生命周期/验证/工作交接。本轮完成候选后端清理机制，不等于生产启用：还需pending页面及真实回执、有界本地空闲重试/最久保留时限、容量/质量/预算及切换回退。当前只在启动/后续提交/查询/内部recover重试，无空闲定时器，锁释放后一直闲置不能宣称已自动清理。
- 下一任务先补这个重试与页面反馈缺口，再继续6B动态认知策略与固定条目、6C迁移/中文对照和7切换回退。生产保持关闭，后续schema从V17扩展。

### 2026-10-26 — 空闲清理与页面反馈实施前

- 重读最新计划、交接、工作记录及V16实现，保持分支和并行QQ工作。先写入单daemon线程/5秒/10分区的空闲重试契约，与worker共用关闭截止时间，独立SQLite连接及锁保护晚完成，不使用模型lane。
- 页面仅增加当前智能体清理状态/回执警告和只读核对，不公开启用新模式，不覆盖草稿或重发写入。无schema变化，生产不开启；先计划后实现和准确CI。

### 2026-10-26 — 空闲重试与页面反馈实现、本地验证

- 方案 `cb218765` 先提交。新增memory_cache_cleanup.py，每worker一条daemon线程，默认5秒/10分区、独立SQLite连接与轮换游标。MemoryWorker.start/close持有其生命周期，取消主task触发停止；共享原关闭截止时间，超时/取消保留底层线程、停止信号和文件锁，不派生重叠lane，也不调用模型。
- 未注册时不建缓存根；失败保留V16状态，下一周期重试。不是持续磁盘故障的硬删除期限；服务停止则下一次启动恢复。全量与情感更新包加入helper，情感增量同步更新jobs.py，避免漏掉实际服务启动入口；无schema变化。
- 管理页显示当前智能体pending/complete和简短原因，edit/delete回执pending给警告，saved仅表示事实提交；只读核对只更新cleanup字段，保护草稿/写版本，不重发删除。旧后端缺状态不假称完成，没有公开开启缓存或强制清理按钮。
- 新增7项本地测试全部通过，相关worker生命周期36项、完整兼容371项通过。覆盖释放真实文件锁后无对话/模型的自动清理、失败重试和游标、单线程/超时/取消/真实锁保持、服务共享截止时间/关库后晚完成的独立连接、未注册不建目录、主task取消。未放宽原关闭预算。
- 新增5个四浏览器场景：删除pending与只读核对、编辑pending与配置草稿、状态失败与跨agent隔离、事实草稿保持、缺失状态不清掉pending。node语法检查通过，等待准确提交浏览器CI，未安装本地大型前端依赖。
- 实际安装脚本新增managed_idle_cleanup检查，等待准确提交CI。生产未连接/注册/部署/开关/备份/真实消息，人设和情感权威未改，并行QQ文件保留。
