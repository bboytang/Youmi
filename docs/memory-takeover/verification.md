# Youmi 记忆接管验证记录

## 步骤6A：空闲自动重试与管理页清理反馈

计划 `cb218765`；准确产品与验证提交 **`e3b512fb436e22d7ca0f1e40c3fe042ff4e7366e`**。以下六组检查均对应这一SHA且全部通过。

| 检查 | 结果 | GitHub Actions |
| --- | --- | --- |
| 前端构建/基线/四浏览器 | 原有回归与快照通过，Chromium/Android Chrome/iOS WebKit/Firefox共184项全部通过 | https://github.com/bboytang/Youmi/actions/runs/40274946743 |
| Python3.10/3.12 | 各371项通过，新增7项空闲重试/关闭/取消测试 | https://github.com/bboytang/Youmi/actions/runs/40274946760 |
| 完整后端/人设传输 | 后端698项及传输检查通过 | https://github.com/bboytang/Youmi/actions/runs/40274946739 |
| 实际安装/BGE/Tantivy/原链路 | 所有阶段通过，影子24项检查全真 | https://github.com/bboytang/Youmi/actions/runs/40274946736 |
| Java授权/JAR | 194项及JAR通过 | https://github.com/bboytang/Youmi/actions/runs/40274946812 |
| Manager基线 | 原有150项及基线4项通过 | https://github.com/bboytang/Youmi/actions/runs/40274946732 |

新增后端测试验证真实文件锁释放后没有对话/模型请求仍会清理、失败重试/游标、未注册无目录、同worker单daemon lane、等待取消/关闭超时仍保留实际文件锁、晚完成使用独立SQLite连接而不访问已关闭store、共用关闭预算、主task被取消也停止调度。本地7项、相关36项、完整371项均通过，范围重叠不相加。

新增5个浏览器场景在四个项目运行，共增加20项，原164项保留：pending删除回执不假称全部完成、编辑pending与配置草稿、核对失败及不同agent状态隔离、事实草稿不丢/不重发写、旧后端缺状态仍保持已知pending。原有53项组件回归与快照步骤通过；原始字节比较48/49，严格模块/依赖/CSS语义比较步骤通过，不写成字节完全一致。

前端artifact `Youmi-responsive-admin-e3b512fb436e22d7ca0f1e40c3fe042ff4e7366e`（ID12629997250，134700454字节，14天）包含候选源码/静态/提交标记/基线与浏览器报告；本机未整体下载大型包，不宣称人工看过所有截图。小型实际安装artifact（ID12630026337，110113字节）已下载至 `/tmp/youmi-idle-cleanup-results`，核对vector/code_sha与shadow/commit为准确SHA且passed=true；24项影子机制全真，新增managed_idle_cleanup=true。

默认5秒/最多10分区，单批结束后再等5秒。正常静态队列一轮约ceil(N/10)*5秒加执行时间，这是调度估算，持续IO/数据库故障无硬删除期限并保持pending；停服由下一次启动恢复。未注册不建根，不调用模型，不改变51维/人设或默认召回，也不公开开启受控模式。无schema变化，V1–V16冻结，后续从V17扩展。

本轮准确产品SHA首次六组CI全部通过。生产未连接/部署/注册/开启/备份或外发消息，浏览器为合成API/模拟手机，不等于真实生产或真机验收。6A候选机制与页面反馈的缺口已补，仍须完成6B认知自动策略/固定条目、6C质量/预算/容量与迁移、7授权真实链路/切换回退；不可提前切换。

## 步骤6A：V16受控缓存清理与启动恢复后端机制

方案提交 `622648f0`，准确产品与验证提交 **`56aefbce140b3fd0c48080487101ac00c833c9f8`**，以下五组检查全部对应这一SHA并通过。

| 检查 | 结果 | GitHub Actions |
| --- | --- | --- |
| Python3.10/3.12 | 各364项通过，新增25项生命周期/迁移测试 | https://github.com/bboytang/Youmi/actions/runs/40271011502 |
| 完整后端/人设传输 | 691项及传输检查通过 | https://github.com/bboytang/Youmi/actions/runs/40271011453 |
| 实际安装/BGE/Tantivy及原向量/检索/上下文/回复/提取 | 所有阶段通过 | https://github.com/bboytang/Youmi/actions/runs/40271011495 |
| Java授权/JAR | 194项及JAR通过 | https://github.com/bboytang/Youmi/actions/runs/40271011455 |
| Manager基线 | 原有150项及基线4项通过 | https://github.com/bboytang/Youmi/actions/runs/40271011484 |

本地新增25项、相关68项、另一组158项、完整兼容364项和冻结真实旧MemoryStore迁移8项通过；范围有重叠，不相加成独立测试数。首轮两项夹具准备错误已修正：未建普通向量、前次改文已撤掉待删除来源；未放宽断言或修改原情感算法。

新增行为测试覆盖真实进程中断/跨进程锁/其他MemoryStore写入、删除期间原生查询不复活旧缓存、提交/回滚与空清空、超100分区时前台目标直接清理、删除/fsync/数据库确认故障及代次竞争、孤立owner、符号链接拒绝和外部文件保持、非默认51维配置保持、V15→V16保持/幂等/失败与中断/冻结旧loader拒绝。成功清理保留稳定空writer.lock及目录，删除其余私有段/清单/未知文件并fsync；这不是介质取证级安全擦除。

已下载artifact `Youmi-actual-vectors-56aefbce140b3fd0c48080487101ac00c833c9f8`（ID12627963206，109916字节）至 `/tmp/youmi-managed-cache-final-results`。vector-results.json.code_sha、memory-shadow-results.json.commit均为准确SHA且passed=true，影子23项全真；新增managed_commit_purge / managed_no_stale_rewrite / managed_startup_recovery，原默认召回/51维/联动与索引恢复检查继续通过。

V1–V15未改，V16冻结，后续从V17扩展。默认不注册且不建缓存，正常retrieve仍旧算法；原shadow_cache_dir模式仍只限合成，受控模式需内部显式managed_shadow_cache=True且无公开开启路由。普通/情感完整安装含新协调器，AstrBot历史增量包只迁schema且不具备开启受控模式的入口。

验收限于候选后端机制，**不等于生产启用验收**：页面/真实终端pending与重试展示、最久保留时限/空闲重试策略、容量/质量/预算、切换回退仍待完成。当前重试由启动、后续提交/查询或内部recover驱动，没有独立空闲清理定时器；锁忙时明确pending。受控模式失效会清整分区，增量性能未达标验收。原始外部sqlite连接不能保证提交后立即回调，数据库搬迁必须先清原根。未访问生产/部署/注册/新建生产缓存/备份或发送真实消息；未作前端/固件修改及真机结论。

## 步骤 6A 第二段基础：V15 事务失效账本

方案 `2916e7c7`，产品 `6459fe6dd8ee743e6cd811c971ea8d7207181407`，最终准确验证提交 **`879e62e2db089dcb75b2783c642da0d157c2a5f6`**（仅修正 Python3.10 故障注入后的授权器恢复）。以下结果全部对应最终 SHA。

| 检查 | 结果 | GitHub Actions |
| --- | --- | --- |
| Python 3.10 / 3.12 | 各339项通过，新增12项真实SQLite/进程中断与迁移测试 | https://github.com/bboytang/Youmi/actions/runs/40268471662 |
| 完整后端及人设传输 | 后端666项及传输检查通过 | https://github.com/bboytang/Youmi/actions/runs/40268471562 |
| 实际安装/BGE/Tantivy/影子及旧链路 | 全阶段通过，原编码并发检查保持 | https://github.com/bboytang/Youmi/actions/runs/40268483056 |
| 绑定授权与JAR | Java194项及JAR构建通过 | https://github.com/bboytang/Youmi/actions/runs/40268485851 |
| Manager基线 | 原有Java150项及基线4项通过 | https://github.com/bboytang/Youmi/actions/runs/40268488555 |

新增测试验证事实与失效代次同事务回滚/提交，独立进程提交/未提交后退出及重开、旧代次确认不能吞新写、各owner隔离、来源/状态/元数据/开关/空清空、读取边界与约束、V14值保持/幂等/失败与进程中断恢复、冻结V14 loader拒绝V15。确认函数只供未来内部清理器使用，当前没有运行时调用。三种安装载荷包含V15，原V1–V14未改。

首轮本地143项的两个最新版本断言遗漏已修正，再次143项通过；相关新增/安装/整理38项也通过。首轮 [40268306912](https://github.com/bboytang/Youmi/actions/runs/40268306912) Python3.10在 set_authorizer(None) 恢复后 iterdump 报 not authorized；改为显式SQLITE_OK回调，保留迁移失败注入/完整dump/重试断言。未改变产品迁移校验或放宽测试。

下载最终artifact `Youmi-actual-vectors-879e62e2db089dcb75b2783c642da0d157c2a5f6`（ID12627741860，109914字节）至 `/tmp/youmi-invalidation-final-results`，核对 vector-results.json.code_sha 和 memory-shadow-results.json.commit 均为最终SHA且 passed=true。20项影子机制检查全真，新增 invalidation_survives_reopen、invalidation_stale_ack_refused；原51维/默认召回/联动冷却/持久化影子恢复检查继续通过。

**本轮只验收失效账本基础，未实现缓存目录注册、实际物理清理或启动清理器。** 闲置缓存即时遗忘和完整生产生命周期仍未验收，显式磁盘模式仍限合成评估。未部署、访问生产库、新增备份或外发消息；人设/情感权威和正常回复不改。V15冻结，后续schema从V16扩展。认知自动策略/固定条目、中文质量/性能/预算和切换回退、真实终端仍待后续。

## 步骤 6A 首段：合成评估磁盘索引机制

方案提交 `ecb1cc70`，产品代码 **`514c19ee13615b80459407a3528f6edeb8225a98`**；最终验证提交 **`1a4e7277ad812c2467e4d057f2257744f55a36a6`** 仅增加独立安装检查的运行条件，失败仍保留失败状态。以下检查对应最终 SHA，全部通过。

| 检查 | 结果 | GitHub Actions |
| --- | --- | --- |
| Python 3.10 / 3.12 | 各 327 项通过，新增 16 项真实 SQLite/Tantivy 磁盘机制测试 | https://github.com/bboytang/Youmi/actions/runs/40266267768 |
| 完整后端及人设传输 | 654 项及传输检查通过 | https://github.com/bboytang/Youmi/actions/runs/40266272350 |
| 实际安装/BGE/Tantivy/索引恢复及旧链路 | 所有阶段通过，原并发编码检查也通过 | https://github.com/bboytang/Youmi/actions/runs/40266257468 |
| 绑定授权及 JAR | Java 194 项及 JAR 构建通过 | https://github.com/bboytang/Youmi/actions/runs/40266277315 |
| Manager 基线 | 原有 150 项及基线 4 项通过 | https://github.com/bboytang/Youmi/actions/runs/40266281717 |

新增 16 项覆盖：不变语料重开不建 writer、新增增量追加与新佐证复用、正文/检索标签修正重建、删除/待确认/合并/清空/关闭后空对账、不同 owner/数据库分区、坏清单/坏索引/错误文档数恢复、真实子进程提交后 os._exit、真实跨进程有界锁、取消后原生槽位保持、并发删除与撤权拒绝返回、磁盘故障不影响默认召回/51 维情感、默认或未授权不建缓存。安装包独立测试加入新增模块；本地新增及相关 105 项通过。

首次安装检查 [40265772529](https://github.com/bboytang/Youmi/actions/runs/40265772529) 及同 SHA 原样重跑均在旧短句并发编码 case 3 发生 embedding_busy，未执行新增路径。没有修改 embedding.py、verify_youmi_vectors.py、断言、并发/资源/超时限制。为保证取证，最终 CI 使安装成功后的独立检查即使前项失败也继续，但不使用 continue-on-error。最终提交本次原编码检查全部通过：短句 9 例/18 次、长句 2 例/4 次，所有结果 bitwise_equal。两次先前失败保留在工作记录中，当前通过不等于根因已修复，容量评估须继续核对。

最终小型结果 artifact 109881 字节已下载：memory-shadow-results.json 的 commit 和 vector-results.json 的 code_sha 均为最终 SHA，二者 passed=true。影子检查全部 18 项为 true，新增 persistent_shadow_reopen、persistent_shadow_dirty_recovery、persistent_shadow_canonical_unchanged；原有 default_engine_unchanged/all_51_emotions_preserved/cooldown_committed_once 等仍通过。

本次不改 schema、正式回复工厂、人设或情感算法，不部署、不访问生产、不新增生产备份或真实消息。磁盘模式仅显式合成数据离线评估：仍全快照对账、闲置分区没有即时遗忘清理/启动生命周期，**生产持久化索引尚未验收**。认知自动策略、固定条目、质量/性能对照、迁移与切换回退及真实终端验收仍待完成。前端未改，本轮没有重复其浏览器构建；最近前端准确证据仍为下节 5B。

## 步骤 5B：独立记忆系统页面与版本化事实管理

准确最终产品及验证提交：**`088033fe827b102baac61076da713911752f503c`**。首轮 `f6329ed3`，后续浏览器修正逐次记录于 worklog.md；以下全部针对最终 SHA，后续文档提交不改变被测产品。

| 检查 | 结果 | GitHub Actions |
| --- | --- | --- |
| 前端基线、构建和四浏览器 | 原有契约 9 项、组件 53 项、快照 13 项及 Chromium/Android Chrome/iOS WebKit/Firefox 共 164 项全部通过 | https://github.com/bboytang/Youmi/actions/runs/40263498051 |
| Python 3.10 / 3.12 | 各 311 项通过；新增 4 项事务内 CAS 测试，管理路由累计 19 项 | https://github.com/bboytang/Youmi/actions/runs/40263572354 |
| 完整后端及人设传输 | 638 项及传输检查通过 | https://github.com/bboytang/Youmi/actions/runs/40263498050 |
| 实际安装、BGE/Tantivy、上下文、回复、提取及联动 | 全部阶段通过 | https://github.com/bboytang/Youmi/actions/runs/40263580515 |
| 绑定授权及管理代理 | Java 194 项及 JAR 构建通过 | https://github.com/bboytang/Youmi/actions/runs/40263574857 |
| Manager 基线 | 原有 150 项及基线 4 项通过 | https://github.com/bboytang/Youmi/actions/runs/40263577050 |

前端首次字节比较为 48/49，随后严格检查模块内容、依赖顺序及 CSS 规则的语义比较通过；没有把字节不完全一致当作完全相同。工作流保留现有前端回归、生产构建及四浏览器全套测试，不跳过原测试。新增 13 个交互场景在四种浏览器运行，覆盖独立保存、鉴权请求、草稿/409/500、取消切换和地址变化离开、事务版本提交、来源失效、合并确认、手机认知布局、外部智能体、深链接、导航及在途读取。原角色设定相关测试亦通过。

前端 artifact `Youmi-responsive-admin-088033fe827b102baac61076da713911752f503c`（ID 12625442301，134676454 字节，保留 14 天）包含可维护源码、静态文件、提交标记、基线结果、浏览器报告及已有路由截图。本机未整体下载大型前端 artifact；以准确 SHA 工作流和其产物供后续发布审查，不宣称人工验收了每张截图。

实际安装 artifact（109995 字节）已下载，核对 memory-shadow-results.json 的 commit 与最终 SHA 一致、passed=true，15 项机制检查全部为 true，包含 all_51_emotions_preserved/default_engine_unchanged/versioned_memory_emotion/cooldown_committed_once/emotion_ranking_bounded/legacy_recall_still_unchanged。

后台路由测试为真实临时 SQLite/本地 HTTP，Java 为真实登录/Shiro/MySQL/Redis及合成内部代理，浏览器使用合成 API。没有真实生产登录写入、手机真机、QQ/App/设备或真实模型质量验收。没有部署、生产库访问、新备份或外发消息；V1–V14、人设及情感算法保持。步骤 5A–5B 机制与页面验收完成，步骤 6–7、认知自动策略和永久固定能力仍未完成，不能提前切换生产。

## 步骤 5A：按智能体管理接口

首轮产品 `38eab3be5ca166fbe768b67f6c61f9c42a851367`；最终产品及验证提交 **`8d0e7209215a128d888c142aebca612c125f5c4b`**。以下检查全部对应最终 SHA；后续交接文档提交不改变产品代码。

| 检查 | 结果 | GitHub Actions |
| --- | --- | --- |
| Python 3.10 / 3.12 | 各 307 项通过，新增 15 项管理路由测试 | https://github.com/bboytang/Youmi/actions/runs/40260067662 |
| 完整后端及人设传输 | 634 项通过，传输检查通过 | https://github.com/bboytang/Youmi/actions/runs/40260067690 |
| 实际安装/真实 BGE/Tantivy/上下文/回复/提取及联动 | 所有阶段通过；对最终提交手动触发 | https://github.com/bboytang/Youmi/actions/runs/40260068662 |
| 绑定授权与管理代理 | Java 194 项通过（新增 2 项），JAR 构建通过 | https://github.com/bboytang/Youmi/actions/runs/40260067650 |
| Manager 基线 | 原有 150 项及基线 4 项通过 | https://github.com/bboytang/Youmi/actions/runs/40260067648 |

15 项管理路由用例使用真实临时 SQLite、本地 HTTP、完成任务生成的整理条目；验证鉴权/前后撤权、不同智能体隔离、来源分页、待确认和删除不复活、配置 CAS、合并幂等与回滚、整理删除/来源变化、失败不返回成功、长人设与非默认 51 维配置保持。Java 使用真实登录/HTTP/Shiro/MySQL/Redis 和合成内部代理服务，验证所有者/匿名/禁用账号、转发边界及冲突、服务错误响应。

首轮 [Java 检查](https://github.com/bboytang/Youmi/actions/runs/40259785763) 的新增测试发现 409 被错误翻译查询变成 500；检查固定上游异常/响应源码后改用 Result.error(code,msg)，原断言保留并增强提示断言。最终 194 项全部通过，没有跳过测试，也没有以首次 JAR 构建成功掩盖失败。

下载最终向量 artifact（109978 字节），核对 memory-shadow-results.json 的 commit 与最终 SHA 一致、passed=true，全部 15 项机制检查为 true。实际安装检查证明新增载荷不破坏既有机制，不代表浏览器已经接上新接口或真实模型质量通过。

没有新增 schema、部署或生产数据访问，没有新备份及真实消息。管理页面/草稿/手机布局/记录跳转尚未实现与验收，不能将 5A 当作步骤 5 完成；新页面事实编辑/确认/删除需补同事务版本校验。后续还需 5B、步骤 6–7、认知自动策略及获授权真实终端验收。

## 步骤 4：记忆与情感双向联动后端机制

准确产品及测试提交：`43c15bde7db0ca94ea02bdb64d1021a51b1e8dc0`。后续文档提交不改变被测产品代码。

| 检查 | 结果 | GitHub Actions |
| --- | --- | --- |
| Python 3.10 / 3.12 | 各 292 项通过，新增 24 项 | https://github.com/bboytang/Youmi/actions/runs/40258355178 |
| 完整后端及人设传输 | 619 项通过，传输检查通过 | https://github.com/bboytang/Youmi/actions/runs/40258355188 |
| 实际安装/真实 BGE/Tantivy/版本化联动/冷却/有界排序 | 所有阶段通过 | https://github.com/bboytang/Youmi/actions/runs/40258355198 |
| 绑定授权 | Java 192 项通过，JAR 构建通过 | https://github.com/bboytang/Youmi/actions/runs/40258355253 |
| Manager 基线 | 原有 150 项及基线 4 项通过 | https://github.com/bboytang/Youmi/actions/runs/40258355484 |

新增用例覆盖全 51 维配置动态值、固定/锁定/禁用/限幅、当前原话与选中记忆证据分开、总计八维、跨 owner/设备、源与配置版本、冷却/重启/佐证、故障保存点、只读排序与未知状态、原生实际回复完成、QQ 本地 HTTP 成功/重试/失败及 V13 升级失败/进程中断。没有覆盖或提交用户已修改的 context.py/astrbot_api.py。

已下载准确提交 artifact，memory-shadow-results.json 的 commit 一致且 passed=true，四个新增检查均 true：versioned_memory_emotion、cooldown_committed_once、emotion_ranking_bounded、legacy_recall_still_unchanged。初始只读阶段保留 51 维状态；随后合成联动案例预期 joy 从10变22、重复提交不再变化，旧默认召回前后相同。

联动总开关默认关闭，排序只作用于显式影子检索，没有生产升级或切换。合成提案不证明真实模型情境判断、相关性或风格质量；真实 QQ/App/设备及物理硬件尚未验收，步骤 5–7 和认知自动策略仍待完成。

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
