# AstrBot 融合工作记录

记录规则：按事实追加，明确源码/自动测试/真机/生产各自边界；不记录密钥或私聊原文。以下时间为 UTC，2026-10-25。

| 时间/顺序 | 工作 | 实际结果/证据 |
|---|---|---|
| 开始 | 阅读仓库 AGENTS.md、附件和 cloud-environment 技能；核对分支/工作区 | 原分支 feature/YOUMI_ASTRBOT；仅附件目录未跟踪；origin 为旧 Mia URL；保留原状态 |
| 只读审计 | fetch 用户指定人格记忆分支，核对上游 | HEAD=739c509eb3f4bc231d879367b33a15a8a94c38f5；从该提交建立 feature/astrbot-fusion |
| 只读审计 | SSH 查询现有 Docker 镜像/挂载/网络与源码 hash，SQLite mode=ro/MySQL 白名单字段 | 当前镜像/模型/音色/表计数见 audit；未写生产库、未请求模型/TTS、未复制密钥 |
| 固定依赖 | 独立 checkout AstrBot、Meme、SnowLuma、XiaoZhi 固定提交 | 逐项核实真实请求字段、钩子优先级、工具循环、cron 和媒体路径；没有修改第三方源码 |
| 审计产物 | 先写 astrbot-audit.md 和 astrbot-contract.md | 识别 after_message_sent 不足以证明发送成功、生产入口旧命名、manager 缺虚拟注册等冲突，随后继续开发 |
| 后端实现 | V8 账本、服务身份映射/授权、HTTP prepare/check/claim/complete/tts、复用 worker/affect/events | 在同一 SQLite 增量实现；无第二个长期记忆库/worker/QQ 回复模型 |
| 管理授权 | 原 Java 授权 Controller/Service 增加虚拟设备注册和 bridge-config | 复用原 grant 生命周期，新增 Java 否定测试，原 worker/check-scope 契约保留 |
| Connector | 临时上下文、逐对象注入去重、Meme 后 Plain 校验、发送 claim、native event.send、complete outbox | 不改非文本链；不重复调用 Youmi reply；失败不自动重放 QQ/工具 |
| 初次完整本地回归 | 直接 unittest | 400 项运行含失败：缺旧源码/音乐夹具，迁移测试旧版本预期未更新；保留原因，未宣称通过 |
| 修正后回归 | 补真实固定夹具、更新迁移测试对 V8 的预期/回退模拟 | **433 项 server 通过**；当时含新 bridge 25 项 |
| 补充故障测试 | 增加情绪投影/一次提交、事件 checkpoint、管理权限、QQ 抑制/恢复 | bridge **30 项通过**；总 server 后续为 438 |
| 固定源码兼容 | 使用真实 AstrBot registry/Agent/OneBot serializer 与 Meme parser/decorate/after | 初次 8 过/2 失败为测试夹具错误：AsyncMock 未 await 内层 coroutine、把真实 Message 当 dict；修正夹具后 **10 项全部通过**，保留实际类验证 |
| 用户追加要求 | 后续 Actions 用本地 nektos/act | 不触发新 GitHub 托管运行；检查 act=0.2.89、Docker=29.1.3；本机 runner 冒烟通过 |
| 环境准备 | Python 隔离 venv、JDK21/Maven、固定 MySQL/Redis 测试镜像 | MySQL 首次拉取磁盘不足失败；清理可再下载安装缓存后重试成功；不触及生产容器/数据 |
| 04:03–04:05 | 新本地 act backend workflow | 首次工作流默认 working-directory 表达式不合法，改为步骤内 cd 后成功；**438 server + 98 scripts + Compose config 全部通过**。日志 `/root/tmp/youmi-act-evidence/backend.log` |
| 04:04 起 | act manager | 首次 Maven 依赖下载中；运行独立 youmi-astrbot-tests MySQL/Redis，非生产；当前尚无测试/构建通过结果 |
| 04:05 起 | 用户授权不影响 Youmi/CodeRove 的文件清理 | 确认 CodeRove 位于 claude 与 claude-session-concurrency；保留整个 projects、CodeRove state/镜像、源码/密钥/数据库/已安装依赖。清 npm 可再下载缓存与归档 journal，空间约170MiB→2.0GiB。旧日志/installer 清理扫描实际删除0项，不冒称释放其空间 |
| 04:09 | 用户要求将当前实施方案、计划、工作与交接写 MD | 新增 implementation-plan/worklog/handoff；如实记录实施中、当前测试、未验证边界和下一步，不以文档替代开发 |

## 日志与制品位置

本地临时日志未当作生产数据上传：

- `/root/tmp/youmi-act-evidence/backend.log`：已成功 act job。
- `/root/tmp/youmi-act-manager-driver.log`、`/root/tmp/youmi-act-evidence/manager.log`：运行中的 manager job。
- `/root/tmp/youmi-pinned-tests.log`：10 项固定源码集成结果。
- `/root/tmp/youmi-meme-native-tests.log`：Meme 原生测试当前执行结果，未结束前不得写通过。
- `/root/tmp/youmi-astrbot-tests.log`：30 项 bridge 专项。

最终提交前需整理脱敏摘要、准确源码 SHA 与 T01–T30 状态，补入正式 compatibility/acceptance；当前不能把以上临时工作区验证说成最终提交验证。

## 2026-10-25 04:39 UTC 接续核查与实现

- 已先重读方案、工作和交接，再核对工作树；仍在feature/astrbot-fusion，未丢弃附件或既有修改。旧表中的“运行中”为当时历史状态，已被本条更新。
- Java首轮190项有1失败/1错误：测试专用TLS信任和上游frontend夹具缺失；修复隔离测试环境后第二轮**190全部通过，JAR构建成功**，新增授权子suite16项全过。日志manager-first-failed.log保留，不隐藏首次失败。
- act connector前轮10项通过、Meme **1045passed/1skipped/85subtests**。驱动脚本运行中被编辑导致过一次shell EOF，当前bash语法已核对；最终运行期间不再编辑脚本。
- 新增原生cron+send_message_to_user+Meme测试，首次夹具缺get_config；补完整context后13项通过。发现Meme图片路径受原工具限制，改用单张选中图片的受保护临时副本，保持原路径授权检查。
- 补图文caption来源分离、STT失败两项，当前**15项Connector通过**，修复测试loop关闭前未释放原生数据库池的夹具警告。
- 新增status真实job查询、定时擦除payload、provider/model名称、暂停/媒体不生成事件、effects已提交不能改报failed；复用CONTROL语义，保持原情绪状态。
- 离线updater合成源码保护测试、真实V7→V8迁移失败事务回滚/旧loader拒绝V8通过；另对已审计生产源码副本执行严格preimage只读检查通过，没有运行生产--apply。
- 固定Huoshan完整provider源码构帧/解析+WS fixture的3项测试通过，配置voice与参数保留；无生产音频调用。后端新增专项总计**39项通过**。原CI辅助39项也通过。
- 已写compatibility和T01–T30 acceptance，明确Mac/Xcode、QQ与生产授权边界。下一步对提交精确SHA跑act并记录最终结果。
