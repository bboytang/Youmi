# Youmi 记忆接管验证记录

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
