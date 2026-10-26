# Youmi 记忆接管验证记录

## 步骤 3C：反思生成与完成回合驱动维护

产品提交 `299fc9947c9ec1d4e30f6adae98f46e2823e4449`；最终验证提交 `c0a4a288af38dfe7c1774f41052040f12faa633b`，包含 Python 3.10 测试夹具清理修正。后续文档提交不改变被测产品。首轮 Python 3.10 失败及中间缩进错误已如实记录在 worklog.md，不作为通过证据。

| 对应最终提交的检查 | 结果 | GitHub Actions |
| --- | --- | --- |
| Python 3.10 / 3.12 | 各 268 项通过，含新增 20 项及原有 17 项 V2 计量验证 | https://github.com/bboytang/Youmi/actions/runs/40255594310 |
| 完整后端及人设传输 | 595 项通过，传输检查通过 | https://github.com/bboytang/Youmi/actions/runs/40255594352 |
| 实际安装/真实 BGE/Tantivy/固定 Provider+SDK/反思生成 | 所有阶段通过，MockTransport 无远程模型调用 | https://github.com/bboytang/Youmi/actions/runs/40255631366 |
| 绑定授权 | Java 192 项通过，JAR 构建通过 | https://github.com/bboytang/Youmi/actions/runs/40255635550 |
| Manager 基线 | 原有 150 项及基线 4 项通过 | https://github.com/bboytang/Youmi/actions/runs/40255639489 |

安装结果 artifact 已下载，extraction-results.json 的 code_sha 为最终提交，passed=true；reflection_generation 确认固定 Provider、实际 SDK/MockTransport、原请求预算、成功完成任务及维护时钟提交通过，远程模型调用数 0。新增用例覆盖配置/CAS/owner、一次请求、时钟/签名、维护名额、重试/取消/重启、明确修订免模型、无来源/清空、51 维情感保持、V12 迁移故障/进程中断。

默认反思关闭，生产数据库及默认召回未升级或切换。未运行真实模型质量测试、真实 QQ/App/设备或物理硬件验收；维护并非无对话夜间唤醒，仍需完成步骤 4–7。

## 步骤 3B：画像、笔记与维护候选后端机制

准确产品及测试提交：`033cfd05efc5a1c4cba2ae3c532af4f031558564`。后续文档提交不改变被测产品代码。

| 检查 | 结果 | GitHub Actions |
| --- | --- | --- |
| Python 3.10 / 3.12 | 各 231 项通过，新增 24 项 | https://github.com/bboytang/Youmi/actions/runs/40253660358 |
| 完整后端及人设传输 | 575 项通过，传输检查通过 | https://github.com/bboytang/Youmi/actions/runs/40253660364 |
| 安装后真实 BGE/Tantivy、混合召回、上下文、回复、提取及整理 | 所有阶段通过；来源失效清理通过 | https://github.com/bboytang/Youmi/actions/runs/40253660362 |
| 绑定授权 | Java 192 项通过，JAR 构建通过 | https://github.com/bboytang/Youmi/actions/runs/40253660365 |
| Manager 基线 | 原有 150 项及基线 4 项通过 | https://github.com/bboytang/Youmi/actions/runs/40253660313 |
| 提取器语法 | 通过；push 未执行 live 模型作业 | https://github.com/bboytang/Youmi/actions/runs/40253660314 |

新增 24 项覆盖真实 SQLite 的分类/笔记/候选、稳定 ID 更新、128 条容量、跨设备共享及 owner 隔离、读写权限、条目/来源 CAS、证据级联清理、故障原子回滚及重启复用、V11 迁移失败/进程中断、兼容旧算法保留新整理和最新情感，以及 51 维情感/认知/长人设不变。安装后结果文件新增三个检查均 true：installed_reflection_artifacts、artifact_source_invalidation、maintenance_preview_only；准确 SHA 及 passed=true 已核对。

提案为合成输入，不是实际模型自动整理质量证明。默认模型/引擎、生产数据库尚未切换，无真实 QQ/App/设备或物理硬件验收；反思生成适配、维护调度、UI 和后续阶段仍未完成。

## 步骤 3A：反思动作与受控写入

准确产品及测试提交：`04a440ea6583a694a307019e86cadaf807fd82ee`。后续工作记录提交只修改文档。

| 检查 | 结果 | GitHub Actions |
| --- | --- | --- |
| Python 3.10 / 3.12 | 各 207 项通过，含新增 18 项反思动作验证 | https://github.com/bboytang/Youmi/actions/runs/40249592551 |
| 完整后端及人设传输 | 551 项通过，传输检查通过 | https://github.com/bboytang/Youmi/actions/runs/40249592533 |
| 安装后真实编码器、混合召回、影子检索、上下文、回复、结构化提取 | 所有阶段通过 | https://github.com/bboytang/Youmi/actions/runs/40249592608 |
| 绑定授权 | Java 192 项通过，JAR 构建通过 | https://github.com/bboytang/Youmi/actions/runs/40249592534 |
| Manager 基线 | 原有 150 项和基线 4 项通过 | https://github.com/bboytang/Youmi/actions/runs/40249592790 |

新增用例使用真实 SQLite、合成提案及确定性编码器，验证新增/稳定 ID 修订、来源/所有者/版本/授权、待确认、整批校验、impression 不截断、失败回滚、重启重试、两次尝试上限及有持久化数据的 51 维情感/认知/人设不变。真实 BGE 流水线证明安装及既有链路回归，不等于真实模型已生成新反思格式或新引擎质量通过。未进行生产升级、真实 QQ/App/设备对话；3B 尚未完成。

## 步骤 1：统一运行时兼容入口

产品代码提交：`37f5680c1738d03a7e2403e02ec360b8f4c55b43`。最终测试修正提交：`1f781da9a990433a3dbf1b719f14043133523860`。后续仅记录文档的提交不代表更改了被测产品代码。

| 对应最终提交的检查 | 结果 | GitHub Actions |
| --- | --- | --- |
| Python 3.10 / 3.12 兼容回归 | 各 135 项通过，包含新增 9 项 | https://github.com/bboytang/Youmi/actions/runs/40241747801 |
| 完整后端及人设传输检查 | 后端 491 项通过，传输检查通过 | https://github.com/bboytang/Youmi/actions/runs/40241726242 |
| 安装后真实编码器、向量/混合召回、上下文、回复及提取 | 全部阶段通过，已上传结果 artifact | https://github.com/bboytang/Youmi/actions/runs/40241782031 |
| 智能体绑定与授权 | Java 192 项通过，JAR 构建通过 | https://github.com/bboytang/Youmi/actions/runs/40241782024 |
| Manager 基线 | Java 原有 150 项及基线复现 4 项通过 | https://github.com/bboytang/Youmi/actions/runs/40241781900 |

新增 9 项在真实 SQLite、合成向量下验证：

1. 召回排序、来源和数据库内容与旧入口一致。
2. App/设备/QQ 工厂共用同一 store 的运行时，不同智能体不共享结果。
3. 完整长人设与全部 51 维情感进入模型上下文，构造过程不修改情感。
4. 编码/召回失败时保留情感状态和表达指令。
5. 关闭召回不调用编码器，权限撤销不降级成空结果。
6. 删除记忆或调整情感使旧回复上下文失效。
7. 临时情感提案不提前持久化；成功提交一次；固定/锁定、限幅和按时间恢复保持有效。
8. 重新打开数据库后读取最新情感，其他智能体不被隐式启用。
9. 全量安装载荷能在独立候选目录导入运行时及回复模块，不靠仓库包路径补漏。

### 首轮失败与处理

首次兼容检查 https://github.com/bboytang/Youmi/actions/runs/40241604362 的 Python 3.10 分支存在 3 项既有测试清理错误；原 `set_authorizer(None)` 未恢复故障注入后的访问。显式允许回调修正后，最终两版本各 135 项通过。没有跳过失败用例或改变生产数据库校验。

本地重点 135 项通过；本地完整测试未准备 CI 专用固定源文件，产生 16 个环境错误，不作为完整通过证据。完整结果使用上述 GitHub 工作流。

### 验证边界

没有变更 schema，没有打开生产数据库，没有新增生产备份，没有部署或发起真实 QQ/App/设备消息。此步骤只证明旧引擎经统一入口后的兼容性；不证明上游核心已经移植，不证明双向情感联动、数据迁移或生产回退已经完成。

## 步骤 2A：canonical 仓储及移植检索核心

准确产品提交：`e92439086a61810fc5c9a0e065b45e696a8b8e07`。以下检查均针对该 SHA，全部通过。

| 检查 | 结果 | GitHub Actions |
| --- | --- | --- |
| Python 3.10 / 3.12 兼容矩阵 | 各 152 项，包含新增 17 项；原生依赖已安装 | https://github.com/bboytang/Youmi/actions/runs/40244047520 |
| 完整后端及人设传输 | 后端 508 项及传输检查通过 | https://github.com/bboytang/Youmi/actions/runs/40244047754 |
| 实际安装、真实 BGE/Tantivy 与原有向量/上下文/回复/提取 | 全部阶段通过，包含新增影子机制脚本 | https://github.com/bboytang/Youmi/actions/runs/40244047561 |
| 中文 embedding 对照 | bge-small-zh 与 bge-base-zh 两个既有任务通过；不是新影子引擎质量对照 | https://github.com/bboytang/Youmi/actions/runs/40244047437 |
| 绑定授权与 JAR | Java 192 项及构建通过 | https://github.com/bboytang/Youmi/actions/runs/40244047533 |
| Manager 基线 | 原有 Java 150 项及基线复现 4 项通过 | https://github.com/bboytang/Youmi/actions/runs/40244047448 |

已下载并检查上述向量工作流 artifact `Youmi-actual-vectors-e92439086a61810fc5c9a0e065b45e696a8b8e07` 中的 memory-shadow-results.json：commit 与产品 SHA 一致，passed=true；real_bge、real_tantivy、canonical_sources、owner_isolation、read_only_shadow、all_51_emotions_preserved、default_engine_unchanged、old_content_not_resurrected 均为 true。

该小型合成语料中，纯 BM25 查询 17.26 ms、BM25+向量 55.13 ms、语义改写查询 163.60 ms；进程峰值 RSS 290052 KiB，包含 BGE。以上仅是本次离线机制用例的观测值，不能当作 10000 条容量、并发或生产延迟承诺。

步骤 2A 未写生产数据，未新增 schema 或持久化索引，未使新结果进入正式回复。情绪/认知快照持久化、稳定 ID 合并与写入兼容仍属于步骤 2B；画像、笔记、反思、双向联动和最终切换仍待后续实施。

## 步骤 2B：V11 状态持久化与稳定 ID 写入原语

完成日期：2026-10-26。产品代码提交 `39fb95b871dd74a051afbfa26863f41edd3a4b3a`；历史人物迁移夹具修正后的准确验证提交 `e2d6627528dde37e3d704cc72604068b9946c73a`。后续记录文档的提交不改变被测产品代码。

| 最终提交检查 | 结果 | GitHub Actions |
| --- | --- | --- |
| Python 3.10 / 3.12 兼容矩阵 | 各 189 项通过，含新增 25 项及人物回归 | https://github.com/bboytang/Youmi/actions/runs/40248324979 |
| 完整后端及人设传输 | 后端 533 项及传输检查通过 | https://github.com/bboytang/Youmi/actions/runs/40248324921 |
| 实际安装的编码器、召回、影子搜索、上下文、回复和提取 | 全阶段通过 | https://github.com/bboytang/Youmi/actions/runs/40248335094 |
| 绑定与授权、JAR 构建 | 通过 | https://github.com/bboytang/Youmi/actions/runs/40248334937 |
| Manager 既有及基线检查 | 通过 | https://github.com/bboytang/Youmi/actions/runs/40248335099 |

新增 25 项机制测试使用真实 SQLite、任务提交/重试、QQ 本地 HTTP 和原生完成钩子，模型与管理授权为明确夹具：

- 成功回合才捕获，预生成与发送失败不捕获；重复完成不重写过去状态。
- 延迟任务使用当时的 51 维/认知快照；历史任务、重复历史来源不回填新情绪。
- 快照写入故障不阻断既有情感；来源关联失败回滚事实/任务提交并可重试。
- 认知固定/动态设置、边界、版本、重启和 owner 隔离，不更改情感表。
- 稳定 ID 合并、来源保持、退休不可自动复活、目标归属/版本/权限、调用方事务回滚和保留自身任务。
- V10→V11 迁移失败/进程退出后恢复、完整人设和情感保持、旧 loader 拒绝 V11、兼容旧算法保留升级后新记忆和最新情感。
- 遗忘/清空与保留期、源状态外键约束、回退只读前提检查。

首轮 https://github.com/bboytang/Youmi/actions/runs/40247944996 的 533 项中有一个 V6 人物迁移夹具未清理 V11 表，导致重复建表；仅修正历史夹具并增强矩阵，没有修改 V11 SQL 或放宽迁移验证。最终所有上述工作流通过。

已下载最终向量工作流的 memory-shadow-results.json，确认 commit 为 e2d6627528dde37e3d704cc72604068b9946c73a、passed=true，八项机制检查均为 true，包含 all_51_emotions_preserved 和 default_engine_unchanged。

验证范围仍不包括真实生产库升级、实际引擎切换、上线回退演练及真实 QQ/App/物理设备验收。没有新增生产数据库/密钥备份，没有发送真实消息。
