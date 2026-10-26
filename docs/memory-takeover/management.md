# 步骤 5 管理入口实施与验收

日期：2026-10-26。步骤 5 分成 5A 管理接口和 5B 页面，两阶段均通过才算完成。本文件先记录计划，不表示已经实现或上线。

当前状态：5A、5B 的机制和页面验收已完成，最终准确提交 `088033fe827b102baac61076da713911752f503c`；全部 CI 链接及验证边界见 verification.md。以下计划保留作为实施依据，生产尚未部署或切换。

## 5A 管理接口

- 新增 `/agent/{agentId}/memory-system`，沿用登录、同源 HTTPS 和 manager 所有者核验，经现有 `/youmi/management` 代理。owner 仅取服务端核验结果，不接受浏览器指定 owner 或设备身份。
- 代理动作 `memory_system`，operation 使用明确白名单：status、list、detail、reflection、emotion_linkage、cognition、artifacts、delete_artifact、merge。配置、整理条目删除和合并复用既有 CAS/来源/幂等规则，不新增模型管理工具。
- status 返回配置、功能状态和能力范围，明确旧引擎仍生效、排序仅影子检索、认知自动策略未接入。列表/详情提供版本、来源分页及合并关系；不存在和外部智能体 ID 使用同一种不可用错误。
- 读取在同一 SQLite 快照完成；manager 在操作前后复核所有者。新接口的冲突/来源变化用结构化错误告知页面，不把未经确认的操作显示为成功。撤销后的写入具有原有管理链路的边界：操作后的复核可以阻止返回数据，不能撤回已经提交的操作。
- 不改 V1–V14，不启用生产开关，不增加生产备份，不动现有用户修改。

验证：真实临时库和本地 HTTP 验证鉴权、隔离、分页、配置 CAS、合并幂等、来源失效、失败及撤销；Java 登录代理测试；安装包包含新增模块；准确产品提交的 GitHub 双 Python、完整后端、Java 绑定及必要安装检查。

## 5B 独立页面（5A 后执行）

- 导航及智能体卡片新增“记忆系统”，按智能体选择，保留原记忆弹窗兼容。
- 记忆列表/详情/来源及合并关系、画像/笔记/维护候选、反思设置、独立四维认知设置、记忆与情感联动配置。
- 情感记录显示记忆联动原因并跳转当前智能体来源详情；来源已删除时显示不可用，不从历史恢复正文。
- 保存失败保留草稿；切换智能体和离开页面确认未保存内容；冲突要求刷新后重新保存；避免过期响应覆盖新智能体。
- 浏览器自动化验证桌面与手机布局、登录权限、草稿与失败、版本冲突、记录跳转及来源消失。真实终端/模型质量仍属后续验收。

固定条目能力当前没有存储契约，本阶段不得将 confirmed 冒充永久固定。候选不自动合并/删除；手动合并必须逐条确认且提交服务端版本。生产启用仍需后续步骤 6–7。

## 5A 已实现的接口契约

首轮产品提交 `38eab3be5ca166fbe768b67f6c61f9c42a851367`；修正 Java 冲突响应后的 5A 产品/验证提交 `8d0e7209215a128d888c142aebca612c125f5c4b`，5A 检查全部通过，链接见 verification.md。没有新增数据库版本。浏览器只向现有 manager 的 `/agent/{agentId}/memory-system` POST，下列 operation 直接作为请求体；manager 包装成 `memory_system` 后才发送到原内部管理端点。5B 已通过独立页面接入，edit/delete 为 5B 补充能力。

| operation | 字段与返回 |
| --- | --- |
| status | 仅 action；返回 revision、features、reflection、emotion_linkage、cognition、capabilities |
| list | action、可选 offset；每页 50 条，返回 memories、total、offset、page_size、revision |
| detail | action、id、可选来源 offset；返回 memory、sources/source_total、分页、revision、最多 50 条相关 replacements |
| edit（5B 新增） | action、id、content、confirmed、expected；expected 为 revision/content_version/evidence_version，三项均在写事务内检查；保留 ID，改文撤回旧来源 |
| delete（5B 新增） | action、id、expected；同事务版本校验，删除事实及其衍生引用 |
| reflection | action、operation；子命令仅 status 或完整 configure（version、enabled、interval_seconds，60–604800） |
| emotion_linkage | action、operation；子命令仅 status 或完整 configure（全部配置及 version），沿用权重 0–1、排序上限 0–0.1、冷却 0–2592000 秒 |
| cognition | action、operation；子命令 status、完整 configure（version、enabled、dimensions）或 adjust（version、energy）；自动策略未接入 |
| artifacts | 仅 action；返回 artifacts，包含完整证据原句、证据版本与 preview_only；召回关闭时隐藏，恢复后可重新读取 |
| delete_artifact | action、id、version；只删除整理条目，保留事实；不接受布尔数值充当版本 |
| merge | action、operation_id、retired_id、survivor_id、revision、versions；versions 包含 retired/survivor 各自的 [content_version,evidence_version]；相同操作重试返回 duplicate=true |

所有操作均通过前后所有者证明；子命令不接受 owner、设备身份或任意模型动作。console 所有者证明授予该智能体的管理权，不能伪造一个设备 scope 复用设备授权。设备内部的 read/manage 权限接口不变。配置和合并复用既有事务实现，读操作使用同一 SQLite 快照且不创建 owner。

列表和详情保留待确认、失效及合并退役状态，不把它们转成有效召回。来源仅返回已保存的摘录和已有回合元数据，不保存或返回新的聊天全文、指纹和状态快照正文。详情 ID 已删除、未知或属于其他智能体，统一返回不可用；记录跳转须使用当前有效详情，不能从旧情感审计拼回原文。

内部冲突返回 HTTP 409 和 `{code:409,error:<类别>}`；manager 复核所有者后转为标准业务 `code=409`，提示保留草稿后重新核对。无效输入/不可用来源返回错误；网络错误和服务端失败返回“操作结果未确认”，不提示成功。配置保存结果不明时需刷新，不能自动重试旧 CAS。成功响应仍是 `{code:0,data:...}`。

本阶段没有修改旧 memory_list/memory_delete 弹窗接口。5B 如要在新页面加入事实编辑/确认/删除，应先补充同事务的内容/证据版本校验，不能在事务外先查版本再调用旧无 CAS 写接口。来源分页与相关合并关系上限是不同限制，页面不能声称展示了无限合并图谱。

5B 已在候选代码中补齐上述 edit/delete：新接口不允许省略 expected 或传 null，底层原方法仅为既有调用保留可选参数兼容。完整页面验收结果见验证记录，不因新增能力表而代表已上线。

## 5B 实施顺序与成功标准

1. 在既有事实编辑/删除事务中增加可选版本前置条件，新管理接口强制传 owner revision、内容/证据版本；旧调用契约保持。真实库验证并发修改、来源新增、外部智能体、待确认、合并退役、删除与来源级联清理。
2. 新增 YoumiMemorySystem.vue，导航和智能体卡片进入同一路由。复用原登录和 sensitive 同源 HTTPS 请求，独立保存反思/认知/联动草稿；保存失败保留，冲突阻止重复保存直到重新核对。按智能体切换需确认放弃草稿，销毁/过期响应不得恢复旧记录。
3. 列表和详情支持来源分页、事实编辑/确认/删除；整理分画像/笔记/维护预览，保留证据跳转。手动合并只接受页面选定的两条有效事实，逐条核对、带版本确认；不把模型候选当成授权。
4. 情感变化记录显示联动处理原因和来源跳转。详情跳转始终重新读取当前智能体来源，未知/删除显示不可用；不复制旧审计正文。
5. GitHub 验证准确提交的后端/安装/Java 与原前端基线、Vue 构建、Chromium/Android Chrome/iOS WebKit/Firefox 全套模拟。页面草稿、失败、冲突、智能体切换、来源消失、删除取消、手机溢出须有交互断言。真实手机与 QQ/App/设备验证仍属后续获授权验收，不部署生产。

5B 最终页面包含导航及卡片入口、来源详情与版本化事实写入、证据整理与候选、分组配置和能量调整、情感审计跳转。草稿放弃使用页面自有 el-dialog，Promise 在确认/取消/关闭/销毁时结束；不同于通用 MessageBox 的 hashchange 关闭逻辑，浏览器地址变化仍允许用户明确取消离开。结果不明时保留草稿，配置或事实旧版本不自动重试；正在请求时阻止切换。现有记忆弹窗继续兼容，事实内容仍沿用原有存储限制，人设原文不由此页编辑或截断。

## 受控缓存清理状态（V16，默认未注册）

status追加cache_cleanup={state,error}；state为not_registered / complete / pending，error只为busy / io / database / null，不暴露目录或错误正文。事实edit/delete响应在已注册分区时同时返回saved=true与cache_cleanup，saved只代表canonical事实已提交，pending不能显示成物理清理已完成。原MemoryStore.handle响应也提供此状态。当前未注册的edit/delete保持原saved响应兼容，不提供公开注册或开启受控磁盘模式的操作。页面显示当前智能体pending/complete，pending编辑/删除回执使用警告反馈；只读核对只更新清理状态，不重发写入、不丢草稿。旧后端缺状态不能把已知pending变成complete。

受控模式仅通过内部运行时managed_shadow_cache=True显式选择，默认回复工厂仍不传；页面的模拟浏览器证据见verification.md，真实终端对pending和重试的展示仍须在启用前验收，不因候选页面/后端通过而发布生产。


## 画像与笔记空列表反馈修正计划（2026-10-26）

用户反馈该页点不动。核查当前页面分类是静态h2，条目存在时才显示查看证据/删除；生产只读聚合确认四类条目均0，反思已开启且整理间隔86400秒。已有openDetail会切换记忆标签并重新读取来源，不能据空列表推断按钮逻辑失效。已向用户确认具体点击对象，先处理独立确认的空列表反馈缺失。

仅为四分类补暂无条目提示，说明成功对话后反思自动整理、满足条件才可能生成，分类标题不是操作入口；不增加手动生成/编辑、模型请求、数据库或开关改动。以既有四浏览器全套及准确构建验证条目证据跳转和配置交互不退化。验证后仅更新管理页静态制品，继承当前memory-8048536b镜像、保留JAR/旧hash资源/其他服务，记录制品与部署结果。若用户实际反馈标签或已有按钮不可用，继续按其反馈复现，不能以空提示修正冒充该问题解决。


## 画像与笔记空状态：已验证并上线（2026-10-26）

用户已确认是页面仅四个分类标题，并非已有按钮无响应。生产只读核查四类条目均0；标题按原设计是分类，生成条目后才出现证据/删除操作。产品ce25d4f80205f2a4c01fa034db64997ea0cf31c9仅加自动整理说明和每类暂无条目提示，未增加手动生成/编辑或修改整理间隔。GitHub前端40335447431与后端40335447082均通过；前端212通过、0跳过/失败/波动，制品12657376066，ZIP SHA3464610c4bafdad5e5e29f21084bc19399affb4e7bed2fe90f1136eaecaf1104。

仅覆盖9个变化的静态文件、保留旧hash资源，454个发布文件逐项核对及HTTP8002实际index匹配。管理端新镜像youmi-manager:artifact-empty-ce25d4f8，ID9e7264698cfa3f42e83b4f57ff6386afc7ef3babc628f76643b00f2295c0bf40；继承memory-8048536b，JAR保持a90389e5f3f09b611e679199abd62fd8601d49f388e6a5a616cce054fc2bc8da。Compose仅改管理端image，语音后端/QQ侧车启动时间未变；后端继续8048536b/V18，不改记忆/人设/情感/认知配置，也未人为制造条目。操作证据/脚本在/opt/xiaozhi-server/youmi-artifact-empty-ce25d4f8，包括frontend-manifest.json、ci-proof.json、compose-command.json、deployment-proof.json；本地/tmp同名目录。
