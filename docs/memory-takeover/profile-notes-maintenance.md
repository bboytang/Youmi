# 步骤 3B：画像、笔记与维护候选

实施前计划，2026-10-26。继承 plan.md 的权限、完成回合、预算及情感保护约束。

1. 新增 V12 增量表保存 owner 隔离的整理条目及其来源关联；V1–V11 不修改。画像按用户别名、事实属性、技能、关系、项目分类，笔记使用稳定 key；维护仅保存退役/合并候选。
2. 扩展反思版本 1，增加 profile、note、propose_retirement、propose_merge 动作。所有动作合计最多 16 条；每条引用 1–8 个已确认、当前 owner 的已有事实，提供完整原句。服务端赋予真实版本，模型不能指定 owner/版本/情感/人设。画像和笔记正文读取时由原句生成，初期不保存无证据的自由推测摘要。
3. context_for 捕获有限条目版本；模型提案只能在验证后的完成任务中提交，和事实、来源、检查点、任务终态同事务。每个条目版本及目标内容/证据版本都要 CAS；更新保留条目 ID。失败整批回滚，重启复用已验证结果，不新增模型调用或扩容聊天历史。
4. 证据更改、删除、转待确认或合并退役时，清除引用它的整个整理条目及标题，防止残留旧资料或已遗忘原文。删除一个条目不删除其来源事实；管理操作需要 memory:manage。同智能体跨设备读取一致，跨智能体不可读写。
5. 运行时提供显式读取及删除内部接口，默认回复暂不注入。维护候选标明只供预览，不自动删事实或执行模型合并。现有管理合并原语保持权限边界；自动调度和模型提示激活仍随新引擎切换阶段验收。
6. 合成 SQLite 验证分类/笔记/候选、更新/删除、所有者/版本/来源、证据级联清理、原子故障与重启、V11 升级失败恢复、旧算法回退保留新数据、情感及人设不变；历史迁移夹具和安装载荷同步升级。准确提交在 GitHub Actions 跑双 Python、完整后端、实际安装/BGE、授权及管理端回归，再记录工作交接。

上游设计依据：固定提交的 core/services/user_profile_service.py、llm_memory/utils/user_profile.py、llm_memory/service/note_service.py、core/services/sleep_maintenance_service.py。采用其画像分类、笔记和整理思想，在 Youmi 既有权威数据库上重新实现证据关联，不复制其按 tag 猜用户、全局文件笔记索引、独立数据库或自动备份行为。当前交付不等于原插件全部文件笔记、模型生成、定时整理或 UI 已上线。

## 后端契约与验收

反思动作形式为 `{action,key,title,evidence:[{id,quote}]}`；action 为 profile/note/propose_retirement/propose_merge。profile 的 key 为 alias/attribute/skill/relationship/project，其他 key 为 1–64 位字母数字或 `_.:-`，不当作文件路径使用。title 最多 64 字且必须字面存在于来源；quote 必须是当前事实完整正文。退役候选恰好一个来源，合并候选恰好两个来源。每 owner 总量 128 条、每条最多 8 来源；模型不能填自由 body、owner、版本或设置。正文在读取时由当前来源完整原句生成，保留否定及人称。

YoumiMemoryRuntime.list_artifacts 显式读取，delete_artifact 需要 memory:manage 及当前条目版本；无公开 HTTP 路由、无默认注入。更新按 owner/kind/key 保留 ID。原始事实和任务终态与整理写入同事务，条目更新提升 owner revision 并取消其他旧快照任务。来源任一版本变化即清除整条整理视图，宁可后续重新整理，也不保留局部过期资料。

提交 `033cfd05efc5a1c4cba2ae3c532af4f031558564` 的 CI 全部通过，准确链接见 verification.md：Python 3.10/3.12 各 231 项、后端 575 项及人设传输、实际 BGE 安装链路、绑定授权和 Manager 基线。新增 24 项使用合成 SQLite/提案；安装后额外验证整理写入及证据失效清理。没有真实模型反思生成、线上升级或真实终端对话；自动生成与维护调度仍待接入。
