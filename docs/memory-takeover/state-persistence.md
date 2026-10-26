# 步骤 2B：成功回合快照、认知状态与稳定 ID

当前代码仍默认使用原记忆算法；新增内部存储接口不等于启用反思写入、自动认知变化或双向情感联动。生产切换需要完成后续步骤。

## V11 增量存储

V1–V10 SQL 原文保持不变，V11 只新增四张表及相关索引/约束：

| 表 | 责任 |
| --- | --- |
| youmi_memory_turn_state | 成功回合时的角色状态，按 owner/session/turn 去重，只保存来源指纹、情感/认知结构化数值和时间 |
| youmi_memory_source_state | 用原 memory ID/session/fingerprint 关联回合快照，保留捕获时的内容版本 |
| youmi_cognitive_state | 每智能体独立的四维配置、能量、启用状态及 CAS 版本 |
| youmi_memory_replacements | 退休 ID 与幸存 ID 的合并关系、原版本、操作 ID 和请求摘要 |

原始 memories、sources、人设和 51 维情感数据不重写、不重新生成 ID，也不倒推填充历史快照。外键和触发器限制来源关联及合并关系的归属，关联行不可修改；source 删除会级联清理其快照关联。

## 完成回合与任务衔接

- QQ 在原 effects 事务中提交情感/事件后尝试捕获快照；发送失败、仅 prepare/claim、受抑制或非用户输入不捕获。
- 设备/App 的完成钩子在确有已校验 assistant 回复时捕获。有情感提案时与情感提交共用事务；无提案时复核 scope、上下文和写权限后捕获。
- 新快照写入用保存点隔离：成功时随外层完成事务提交；新增快照存储故障时回滚该保存点，保留既有情感更新并记录不含原文的错误类型。重复完成不重写已保存的过去状态。
- 后台记忆任务仅为本次新插入的来源，按 owner/session/turn/来源指纹关联已存在快照；关联与事实、任务终态共用原提交事务。相同 session/指纹的历史来源重复出现时，不用新回合状态回填旧来源。没有快照就返回未知，禁止读取 worker 当前状态来补历史。
- 关联插入失败会回滚该次记忆提交并按原任务策略重试，不影响已经完成的情感更新。历史任务、旧完成钩子和关闭状态产生的缺失快照保持未知。
- 影子结果的 state_snapshot 为关联来源对应的 completed_turn 结构，含捕获时间、内容版本及 affect/cognition；没有关联时明确为 null。

情感快照保留全部 51 维值、当时启用的维度及版本；不保存人设背景、模型密钥或新聊天全文。遗忘最后一条关联来源时清理无其他引用且无活动任务的回合快照；未关联记录最多保留 90 天、每 owner 10000 条，活动任务的记录受保护。仍关联记忆来源的快照属于该来源证据，保留期跟随来源，不当作松散聊天历史删除。

## 认知配置的内部接口

`YoumiMemoryState.cognition(scope, authorization, command)`：status 需要 memory:read，configure/adjust 需要 memory:manage；前后复核权限，owner 来自已鉴权 scope。没有新增 HTTP 路由或模型工具。

- 四维名：recall、impression、expression、creativity。
- 默认未启用、能量为 0、模式为 fixed；基线分别为 7、3、0.5、0.7。
- configure 提交完整 dimensions、enabled 和当前 version；每维包括 minimum/baseline/maximum/mode/sensitivity。recall 范围 1–20，impression 1–10，其余 0–1，sensitivity 为 0–3。模式为 fixed/dynamic。
- adjust 提交 energy 子集与当前 version；能量范围 -20 至 20，拒绝非有限数及布尔值。固定维度保持 0，调整固定值通过修改配置基线完成。
- 配置和能量重启后恢复；既有 51 维情感不受该接口影响。此阶段不移植自动反思调节算法，也不将认知能量直接映射成情绪或模型温度。
- 清空记忆时删除回合快照并重置认知能量，保留认知启用设置及配置基线；遵循既有情感清空行为。

## 稳定 ID 合并

`YoumiMemoryState.merge(scope, authorization, command)` 是内部管理原语，需要 memory:manage；未暴露给模型，也不自动执行。

命令绑定 operation_id、retired_id、survivor_id、owner revision，以及两条记忆各自的 content/evidence version。事务内检查同 owner、有效且确认、未退休和版本匹配；重复原请求幂等返回，复用操作 ID 改请求则冲突。

后台编排使用 caller-owned 的 merge_in_transaction 原语，必须在已鉴权的事实/任务事务内调用，并传当前 job_id 保留自身任务；不能把拥有独立事务的管理入口嵌入任务事务。

合并保留两条原始记录及各自来源，只使 retired ID 不再召回，不把来源复制到不同措辞的幸存事实下；失效关联性格、观察、事件与派生索引。旧措辞重复提取不自动恢复退休记录，修改应针对幸存 ID，历史记录仍可明确删除。新引擎写入编排在步骤 3 接入这些原语，不能绕过其版本与归属检查。

## 迁移、安装与回退

完整安装及 normalize_runtime 载荷包含 V11 和 memory_state.py。历史离线安装器也携带必要依赖；旧原生完成钩子未更新时不会伪造快照，完整功能验收和候选部署必须使用一致的完整载荷。

旧 V10 loader 会拒绝 V11；回退使用理解 V11、继续运行旧算法的兼容构建，不做 schema 降级或恢复旧库覆盖新数据。当前默认旧算法可作为该兼容构建基础，但这不是后续引擎切换的生产回退演练。

check_memory_rollback 只读检查任务、模型请求和 QQ prepared/sending 回合已排空，以及外键关系有效。它只是离线前提，不能证明进程已经停止或替代线上排空和授权复核。

验证包括 V10 中途 DDL 失败、进程退出后恢复、重复迁移、原人设与情感保持、V11 兼容旧算法保留升级后新增记忆与最新情感。结果和准确提交链接见 verification.md。
