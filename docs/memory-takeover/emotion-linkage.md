# 步骤 4：记忆与情感联动

实施前计划，2026-10-26。

1. V14 按 owner 保存联动配置与同记忆内容版本冷却。两个方向默认选中，权重 0.25、排序影响上限 10%、冷却 24 小时；候选总开关默认关闭，实际生效还必须同时启用记忆召回和现代 51 维情感系统。配置可调、有版本及管理权限，不改线上设置。
2. 新评价版本 2 将本轮 changes 与 memory_changes 分开；本轮仍逐字校验 user_text，记忆必须引用实际选入本轮上下文的所属 ID、内容/证据版本及完整事实原句。所有维度合计最多 8 个；旧格式和旧三维流程兼容。
3. 共用 builder_for 工厂的派生 ContextBuilder 接入 QQ/App/设备，不覆盖已有 context.py/astrbot_api.py 用户修改。联动内部证据不额外注入回复正文/产品提示；评价使用原有一次请求和 token 上限。
4. 本轮与有效记忆的原始 delta 合并后，只执行一次原有恢复/敏感度/固定/锁定/单轮限幅。成功完成的授权事务才消费冷却并记只含 ID/版本/原因的审计；同内容版本的新增佐证不重置冷却。失败、重复、旧版本和撤权不写。联动读写故障用保存点隔离，回退本轮变化，不拖垮原情感流程。
5. 情感排序只在显式影子检索已合格的同一候选集合内，以当时状态关联作有限加权，不引入新候选、不改事实或旧默认召回；缺少历史状态保持未知。涉及否定、纠错、时间变化的查询保留原顺序。历史状态不证明事实引发该情绪。
6. 测试来源/版本/owner、冷却、并发、保存点故障、一次计算、全 51 维固定/锁定/恢复、主开关、排序边界、QQ/原生完成和 V13 迁移恢复。准确提交跑 GitHub 双 Python、完整后端、实际安装/真实 BGE、授权及管理基线，记录工作交接。保持生产、默认引擎和原始人设不变，真实模型质量/终端验证另行验收。

## 接口与验证范围

内部 emotion_linkage 的 status 需 memory:read，configure 需 memory:manage 和当前 version；字段为 enabled、memory_to_emotion、emotion_to_memory、stimulus_weight（0–1）、ranking_limit（0–0.1）、cooldown_seconds（0–30 天）。配置 CAS 改变 owner revision 并取消旧任务；未增加公开路由或 UI。历史审计关联原 interaction_history_id、回合和来源版本，原因不等于因果证明。

合成机制用例中，joy=10，本轮 delta=2、记忆 delta=40、权重0.25，合并后得到 joy=22；若 max_change=5 则最多变到15，不能先分别限幅再相加。成功完成才更新冷却；相同内容版本的新佐证不能绕开冷却。两个设备同时完成仍受原情感版本 CAS 保护。历史状态只用于有限排序，不用于直接生成记忆刺激。

准确提交 `43c15bde7db0ca94ea02bdb64d1021a51b1e8dc0` 的 CI 全部通过：双 Python 各 292 项、后端 619 项及人设传输、实际 BGE 安装链路及授权/管理基线。结果文件的 versioned_memory_emotion、cooldown_committed_once、emotion_ranking_bounded、legacy_recall_still_unchanged 均为 true，准确 SHA 已核对。详见 verification.md。

这证明合成输入下的联动机制，不是实际模型相关性/风格质量证明；当前没有生产升级、真实 QQ/App/设备对话或硬件验收。排序仅显式影子评估，默认召回不变；开启生产仍需后续质量、管理界面和切换验收。
