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
