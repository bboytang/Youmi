# 按原任务书逐项完成审查

2026-10-25，基于原附件和当前工作树重新审查。**目标仍为 active，不以离线代码提交替代完整产品验收。** 原b329c1e9的测试结果保留；后续修订必须重新验证对应代码。

## 本次发现及补齐

1. 固定版本Meme工具路径在第二张图片转换失败、Agent中断且没有tool-end时会遗留临时文件。新增两个测试在旧代码上实际失败；已改为提前登记、失败/tool-end/agent-done清理，并增加300秒过期媒体回收。原图库不在清理范围。
2. 凭据文件短时不可读原来会抛出未分类OSError，可能终止complete重试任务。现在返回可重试类别，恢复文件后继续HTTP提交；不输出路径或凭据值。
3. 原压力证据主要是重复事件小并发。现新增40个不同回合、120次complete的本机HTTP/SQLite/JobStore检查，断言恰好40个job、40个终态、零伪造事实。Manager/模型/worker消费为明确fixture，不替代供应商或QQ性能。
4. 生产Manager实际使用MiaDeviceAuthorizationController/Service及Binding内部类。先前Java回归仅证明当前Youmi命名，不能证明旧运行镜像的可部署性。已只读核查实际JAR路径、类名/哈希；新增旧类名构建和只替换三个已审计class的离线JAR补丁。完整旧命名Java回归和打包将在本地act单独验证。

## 原规格要求与当前证据范围

| 规格 | 权威证据/交付 | 完成审查 |
|---|---|---|
| §0 基线fetch、新分支、生产只读快照、保护未提交数据 | audit、git记录、实际镜像ID、工作树 | 已执行；附件保留，未部署生产 |
| §1.1 文字、Agent工具、唯一人格/记忆、情绪/成长 | API/Connector/现有Context/Memory/Affect/Event，T01–T03/T11–T24 | 已编码与离线机制验证；真实双端/供应商验收仍缺 |
| §1.1 Meme、图片/GIF/STT/TTS、主动聊天 | 固定Meme/AstrBot实码测试，T04–T10/T27–T29 | 已适配；真实图片包、QQ收发/手机播放/定时仍未验 |
| §1.1 重启、重连、去重 | 账本/outbox/迁移/失败注入，T20–T26 | 本机机制有证据；两实际sidecar与QQ重启/重连仍缺 |
| §1.2 禁止重造、无好友白名单、无实时电话冒称 | diff、配置/固定第三方源码、compatibility | 没有新事实库/选图库/回复模型；实时电话不支持 |
| §2–§3 实际接口与唯一所有权 | 审计/契约与固定源码、同owner Scope、唯一worker引用 | 源码与离线验证成立；完整生产运行尚未证明 |
| §4 与 §12 七项交付物 | audit/contract/compatibility/acceptance、源码、deploy、测试/提交/证据JSON | 文件已具备；旧Java命名部署补丁为本次新增修订 |
| §5.1 虚拟授权/撤销/绑定版本/服务凭据 | Python否定测试与真实Java授权测试 | 当前命名通过；旧命名须单独回归；生产grant未注册 |
| §5.2 稳定key、可信来源、真实容量、分层上下文、情绪投影、TTL与快照 | prepare/identity/ContextBuilder与测试 | 代码/本机机制通过；AstrBot真实模型容量需实际配置审定 |
| §5.3 完成时机/纯文字检查/effects与job分步幂等/撤销和删除竞态 | ledger、真实JobStore、固定AstrBot链 | 有本机证据；不将after钩子等同手机送达 |
| §5.4 当前音色、WAV/Record、临时生命周期与health | 原Huoshan构帧/解析测试、OneBot serializer、新媒体异常测试 | 供应商调用与QQ转码/播放未验；其他TTS类型回退文字 |
| §5.5 同库增量、保留旧写入/校验、备份/回滚 | V7→V8与失败原子回滚、旧loader拒绝测试、rollback | 本机迁移证据有效；生产备份和演练未执行 |
| §6.1 请求对象/事件分开去重、保留tools与history | 原ToolLoopRunner两次续答/新旧req测试 | 固定源码集成通过；不承诺未知第三方私有LLM链路 |
| §6.2–§6.3 Meme优先级/媒体/唯一发送/中断/命令/主动消息 | C类集成及新增清理失败测试 | 当前命名原生路径已测；QQ最终组件仍待实机 |
| §6.4 插件配置/秘密文件/超时/诊断 | _conf_schema、client、README、contract | 具备；QQ使用完整最终链，未提供不安全的流式开关 |
| §7.1.1–7 原拓扑、两个sidecar、持久卷、内网端口、WS、版本、模型 | 只读生产检查、compose config、versions/digest、setup | 配置/来源可复现；实际容器运行和真实tool模型未验 |
| §7.2 可运行模板 | compose.yaml、无真实密钥的env.example、bootstrap | Compose语法通过；模板通过不等于实际启动通过 |
| §8.1–7 审计、版本、身份、账本、Context、effects、Connector | 对应源码与本地act证据 | 离线有证据；发现异常/旧命名后继续修订，未缩小目标 |
| §8.8–9 Meme资源与媒体/主动联调 | 原Meme管理机制、安装/使用规则说明、媒体适配 | 实际包尚未导入，QQ/provider操作条件未提供 |
| §8.10 回归/压力/失败注入/真实冒烟 | 原回归、新本机并发、故障测试、T01–T30 | 自动化可执行；真实QQ、原App和生产压力仍缺 |
| §8.11 备份、部署、回滚演练 | setup/rollback、严格preimage updater、旧JAR离线补丁 | 源码/说明已备；生产执行未授权，不自行切换 |
| §8.12 统一提交与汇报 | feature分支、提交SHA、工作/交接/验收 | 已交付首轮；新修订需重新测试提交，不将goal标完成 |
| §9 T01–T30 每类真实断言及设备边界 | acceptance逐项矩阵、实际原始日志与源码 | fixture/固定源码/真实Java分开；全部QQ/iOS实机项未验 |
| §10 贯通trace/脱敏/指标/超时/重试/副作用控制 | turn_key、分类耗时日志、status/job原状态、原生工具与调度权限 | 本机可观测；供应商时延、首条回复、候选成功率、重连率等运行指标无真实采样 |
| §11 十一项真实接口清单 | audit CONFIRMED/ADAPT/BLOCKED及本次旧Manager修订 | 真实电话无可验证AV接口；Manager旧命名不能沿用原当前命名结论 |
| §12–§13 产物、复现、引用、通过标准 | 所有文档/固定源码链接/本地act | **完整通过标准尚未成立**：缺生产/QQ/原App真实证据 |
| 用户追加：Actions本地act | run_astrbot_act.sh及实际job日志 | 已使用本地act，未启动远端Actions |
| 用户追加：清理且保护Youmi/CodeRove | worklog、当前目录与镜像保留 | 仅清可回收缓存与本任务无用下载，不清项目数据 |
| 用户追加：当前方案、工作与交接MD | implementation-plan/worklog/handoff | 持续同步，新修订后刷新结果 |

## 目标尚未达成的外部条件

生产镜像只读访问仍可用，两镜像ID与前次一致；这不授予写入/重建/切换权限。完整验收需要生产备份/候选构建和部署授权、QQ所有者扫码与手机操作、实际AstrBot模型容量/STT/MCP配置、用户选择的Meme资源、以及本地Mac/Xcode。权限不足不伪造真机结果；也不把所有实现工作完成当成目标完成。

正式生产切换前还必须在原主机校验当时的Python/Java preimage并构建新候选，保留V8-aware回退镜像及数据；不导出密钥、不全量替换定制JAR、不down -v。获授权后的具体执行仍按setup/rollback和T01–T30。
