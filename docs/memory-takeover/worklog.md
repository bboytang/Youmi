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
