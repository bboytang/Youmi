# 步骤6B：四维动态认知与固定记忆

2026-10-26，先记录契约再实现。用户最新已明确要求按计划推进并生产部署；部署授权已具备，仍须完成6B/6C/7的质量、迁移、兼容回退及精确CI，不以候选机制通过冒充已上线。原SSH身份已只读连通生产，不再要求用户重复提供；不得新增生产库/密钥备份。

## 四维认知

参考固定官方Angel Memory 1.6.14的core/soul/soul_state.py（17454a12669c9a7f0f2e2fbe6a424f6f8f4fffe5）：4位反思方向码、弹性阻尼/回归和tanh映射。保留现有Youmi四维配置的最小/基线/最大、fixed/dynamic、sensitivity及手动energy，默认关闭。能量中心为0，修正上游把参数基线当能量中心的混淆；动态值=baseline+对应正负区间*tanh(sensitivity*energy)，固定值始终baseline。recall/impression取整，其他保留两位，结果及能量均有限且有界。

自动更新复用已经授权的V2反思请求，绝不另开模型调用。反思可选cognitive_adjustment={code:4位01字符串,quote:本次完整原句}，只有本任务服务器context明确启用认知时才接受；版本由服务端绑定，不信模型填写。quote必须等于本任务实际来源，不取人设/历史/推理；不能改变配置、51维情感或工具权限。弹性更新direction*exp(-0.1*abs(E))-0.1*E，限制[-20,20]。仅在成功任务的原子提交中应用一次；重复/失败/历史任务不补写。后台配置已变则跳过旧调节，保存点隔离认知故障，不能回滚已验证事实或既有情感。历史来源快照保留完成当时值，不用延迟worker新值倒填。

effective行为：recall控制相关合格召回数量（1–20，原候选上限24/阈值不放宽）；impression只控制自动整理条目/参考深度，不截断明确事实、更正、遗忘、固定条目；expression与creativity生成一致的表达长度/发散策略，接入共享上下文和各端实际生成入口，响应/上下文不得超过已验证模型预算，不覆盖人设或捏造事实。自动配置/状态变更要在准备/续答/完成时核验版本，不允许静默覆盖后台设置。正式引擎仍待6C/7切换，不能只改capabilities宣称已生效。

## 固定条目

V17新增youmi_memory_pins扩展表，以canonical memory_id为主键/级联删除，不加第二份正文或更换ID。固定独立于confirmed，仅所有者memory:manage可在管理端带revision/content_version/evidence_version的事务CAS切换；初次固定须为当前owner已确认active事实，不自动确认敏感/待确认记录。固定不等于每轮无条件召回，不绕过相关性、Scope或上下文预算。

固定条目不自动衰减/退役/合并，已有相关维护候选在固定时失效；手动合并须先取消固定，避免意外退役。管理员明确改文/更正保留固定标志，明确删除/清空必须级联移除标志并照常清理所有派生数据。取消确认后即使标志保留也不得召回，不能用固定恢复旧证据。Pin切换推进owner revision与content_epoch，使缓存和并发管理版本一致；不同智能体不得跨owner标记/读取。

## 实施顺序与验收

1. 6B1：持久化能量/effective映射、同一反思请求的有据方向码、成功原子提交和V17固定条目/管理CAS。真实SQLite验证幂等、失败/回滚/版本竞争、fixed/dynamic边界、51维与人设不变、来源/owner/遗忘、迁移和旧loader拒绝。
2. 6B2：共享运行时/上下文/原生与QQ生成入口、管理页全部数值/模式/effective和固定切换接入；不覆盖并行QQ用户修改，若需同文件只提交本功能差异。验证各端实际请求参数/上下文、预算、明确操作优先、草稿/失败/手机模拟。
3. 精确产品SHA运行GitHub双Python、完整后端、实际安装/BGE、Java与Manager，以及变动页面四浏览器。更新工作记录/交接后继续6C冻结质量/预算/容量/迁移和7真实调用/部署/兼容回退，完成后才开启生产新引擎。

保持V1–V16不变，任何新schema验收后冻结。不能把6B1后端机制或静态JSON测试替代所有端实际生效及生产部署证明。

## 6B2平台适配确认（实现前补充）

已读取固定生产AstrBot4.29.3的ProviderRequest及internal agent pipeline：OnLLMRequest在选定provider/build_main_agent之后调用，ProviderRequest没有每轮temperature/max_tokens参数。不能动态修改共享provider配置或绕开原Agent重发模型请求。QQ采用同一cognitive policy的系统表达/创造指导，保持原生媒体/工具路径；App/设备原生OpenAI兼容provider同时接收受原预算限制的max_tokens与0–1温度。不能宣称QQ已改变SDK温度，验证必须检查实际合并system_prompt。若日后AstrBot提供per-request选项，再独立适配。

共享context通过干净的新子类接入，避免覆盖用户context.py/astrbot_api.py并行修改。cognitive版本加入上下文核验；新指导必须计入预算，保留人设、事实和工具序列，必要时放弃本轮认知指导并明确diagnostics，不因新可选指导阻断原情感/回复。召回参数先接显式影子候选，默认正式引擎仍由6C/7统一切换。

## 6B1实际安装检查中的并发问题（2026-10-26）

准确08a99335的双Python/后端/Java/Manager/结构化提供者通过，新影子27项（含认知/固定）全部真；实际向量并发短句case2/3为embedding_busy，其余逐位一致，长句4次全一致，工作流40280427034整体失败。失败样本仍保留，不将独立影子通过写成全流程验收。

短句两条活跃线程连续获取空位，两个早到等待者超0.4秒；原threading.BoundedSemaphore不保证FIFO。先写可重复的“释放后新到者抢占老等待者”机制测试，若复现，再仅改为有界FIFO准入（仍两路native/0.4秒，至多4等待者），不改模型、会话参数、并发验证断言、资源或延长超时。这是生产前容量问题的必要修复候选，仍需原真实BGE检查与6C完整容量验证，不能单凭机制测试宣称根因全部解决。
