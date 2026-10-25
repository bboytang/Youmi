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

## 日志与制品位置（04:09历史快照，最终结果见文末）

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

## 2026-10-25 05:04 UTC 最终开发、验证与交付

- 代码提交顺序：9f47d658（完整融合）、17d66729（媒体来源与测试遥测隔离）、a6d33891（真实bridge-config权限、null角色、scheduled来源）、b329c1e9（STT失败幂等文字提示）。当前最后代码SHA为 `b329c1e91903eb4ae801444862784ac0c46dc644`。
- 9f47d658本地act backend447+98+39通过，connector15+Meme通过；manager在启动测试前因旧`/root/tmp/youmi-m2/settings.xml`不再存在失败，原日志保留在youmi-act-final。恢复环境的旧tmpfs已消失，未重建或复制任何旧代理密钥；改普通独立缓存和默认HTTPS仓库后重跑。
- 17d66729在本地act三job均通过；16项Connector仅保留上游audioop弃用警告。原生遥测会创建异步指标任务，CI改用公开ASTRBOT_DISABLE_METRICS开关关闭；未屏蔽线程异常来制造通过。
- a6d33891三job均通过：448+98+39、16、Meme1045/1skip/85subtests、Java190/JAR。新Java断言实际调用bridge-config，验证成功输出只含允许字段、普通登录token/伪造owner/实体grant/撤销后拒绝；空prompt:null规范为空角色；scheduled上下文明确非用户发言。
- b329c1e9增加语音识别失败文字提示：prepare→check→claim→原event.send→complete，media来源，不调用回复模型、不写假转写；重复request钩子只通知一次。固定源码集成含实际Meme after钩子通过。
- **最终完整本地act**对b329c1e9：backend448 server+98 scripts+39 CI辅助及Compose config全部通过；Connector16通过；Meme1045通过、1跳过、85子测试通过；真实Java190通过、0失败/错误/跳过，JAR package BUILD SUCCESS。原始日志 `/root/tmp/youmi-act-delivery/`，hash与制品指纹已写入astrbot-test-evidence.json。
- Meme唯一跳过为未安装botpy.message的QQ官方SDK测试，不是本次OneBot目标路径。固定依赖弃用警告保留。没有GitHub托管run，本次全部Actions使用act本地self-hosted runner。
- 对审计生产源码的本地一次性副本实际运行严格preimage updater --apply，备份逐字节一致、原始副本未改变、候选新增文件存在的断言通过；未运行生产安装或完整生产镜像。真实V7→V8原子失败/重开和旧V7拒绝V8均在专项中通过。
- 已推送代码分支feature/astrbot-fusion到明确Youmi URL；提交带skip ci，未改origin/main或重写历史。后续交付记录修订只改文档。保留用户未跟踪的YOUMI_ASTRBOT附件。
- 已同步方案/契约/兼容/T01–T30/交接/部署/回滚，区分实现、自动测试和未验证。生产数据、角色、模型、音色均未修改；QQ/iOS、真实供应商、两个sidecar启动/重启及生产迁移回滚仍未验，不伪造通过。
- 清理仅为已确认可再下载安装缓存、归档journal和本任务无用下载；Youmi、CodeRove项目/state/镜像、数据库与密钥均保留。当前测试依赖和证据属于本项目后续复现所需，保留。

## 2026-10-25 05:51 UTC 生产部署授权与备份覆盖指令

用户明确选择“实际部署到生产服务器”，随后明确要求“不需要备份，我怕磁盘空间不够了”，并要求继续执行。生产部署已授权，**本次不新增生产数据或整包备份**；此前尚未执行生产备份。保留现有镜像和数据卷作为回退基础，SQLite以V8-aware禁桥接方式回退，实际切换前仍验证候选和数据完整性。生产磁盘实测空闲约14.5GiB，物理内存约1.9GiB/可用约0.64GiB，另有4GiB swap；按顺序启动侧车并监测原服务。

旧命名Manager完整190项回归/JAR构建和严格class补丁打包通过；实际生产公开API成员与候选对比无删除：Controller 8→10、Service14→16、Binding6→6。仅导出公开签名/哈希，没有导出配置或密钥。DockerHub直连拉AstrBot镜像遇到网络超时，官方GHCR同digest返回未授权，正在尝试仍严格固定原digest的镜像分发路径；没有更换版本。

## 2026-10-25 06:12 UTC 实际部署与手机先登录QQ

- e87596a6四个本地act job全过：452 server、98 scripts、39 CI辅助、21 Connector、Meme1045/1skip/85subtests、当前/旧命名Java各190与JAR/补丁包。原始日志保留在youmi-act-goal-audit；40/120/40压力用例为明确本机fixture边界。
- 用户补充允许生产主机内部复用现有Key；已在原主机将现有deepseek-flash凭据写入新root0600 env文件，AstrBot仅配置环境引用，不回传、不入库。核对官方实际模型容量后为桥接设置32K/24K/2048保守预算；没有沿用标着旧GLM的profile，没有修改原角色/音色/模型。
- 腾讯镜像分发成功拉取两个原固定digest。旧builder停滞后只停止本次构建客户端，改用已有BuildKit，未重启daemon。两候选以原生产镜像为FROM；Manager只替换严格审过的class，非全量干净JAR覆盖。
- 按原六份Compose顺序加新override，只切换原Manager和语音容器；原MySQL/Redis不重建。实际V8、quick_check、旧记忆/traits/affect等所核表指纹一致、bridge worker health200通过。未新增任何备份，现有镜像/数据卷保留。
- 两sidecar启动。核对SnowLuma原镜像start.sh发现持久化目录必须是/app/snowluma-data，修正模板；首次runtime.json被root创建导致EACCES，只修该文件所有权并启动daemon子进程，未重启QQ。两个原生插件实际activated=true；AstrBot原生provider test于06:10:44实际调用deepseek-flash返回available，0.67秒；首次错误路由405保留原因，按固定源码纠正。
- 用户要求先绑定QQ，确认只有手机。已建立现有HTTPS下临时随机路径桌面，Basic+原生VNC验证、无新公网端口、access_log关闭、60分钟timer自动清理。实际未认证401/认证200、WebSocket101/RFB greeting均通过；只向用户提供本次新建桌面凭据，QQ密码由用户在桌面直接输入，不进入聊天/仓库。临时URL和密码不记MD。
- QQ登录、虚拟grant及T01–T30真机断言仍未完成；不以sidecar健康或真实模型ping代替QQ/共享记忆验收。最新状态以production-deployment和handoff开头为准。

06:15 UTC：Compose修订提交14390134本地act backend再次通过452+98+39及Compose config，日志youmi-act-production-config/backend.log与hash已入JSON；Connector/两种Java运行代码仍对应e875四job证据。临时入口清理器补充原生x11vnc disconnect:all，关闭已建立的桌面会话，不重启QQ；只读清理预览验证恢复原Nginx哈希。尚未检测到登录后的QQ账号配置，继续等待用户在手机桌面操作。

## 2026-10-25 06:43 UTC QQ登录后继续

- 用户两次明确已登录；检测到实际账号配置，SnowLuma日志14:22:39 connected，原生connections API返回wsClient=ok/已连接；AstrBot平台running、error_count0。临时桌面已按脚本关闭、htpasswd删除、原Nginx哈希恢复，QQ仍PID75。
- 原生WebUI按固定发行版完成安装协议登记，并通过原API轮换首次生成的管理密码；新密码只留原主机root0600文件，不改QQ密码。原生好友列表返回0，已通知用户加专用QQ；未自行添加陌生人/白名单。
- 独立grant尚未签发。用户此前只允许模型Key同机复用，owner后台会话是另一种凭据，已明确询问是否允许在原主机内用有效会话走正常virtual接口；等待选择，未读取token或伪造身份。
- 发现原生SnowLuma已有fetch_ptt_text，核对actions/extended、media-actions、PTT异步协议及AstrBotPreProcess→waiting→Agent顺序后，补入Connector等待钩子。无需新增ASR服务或Key；成功只换临时Record保留raw，失败走原media-only notice/claim/complete，独立插件请求不改。
- 新增测试在旧代码上5失败/1通过，证明缺口；修复后完整固定源码集成29通过，含Meme等待优先级、8个新增转写/失败/授权用例。原始before/after日志位于/root/tmp/youmi-snowluma-stt-{before,after}.log；真实手机语音仍未测。正在准备精确提交本地act和线上原生reload。

## 2026-10-25 07:03 UTC 语音适配交付、原生表情资源与重启证明

- 55e9428e本地act connector通过29集成、Meme1045通过/1skip/85subtests；日志youmi-act-native-voice/connector.log。仅插件main.py在核对旧/新SHA256后原子替换并原生reload，原生API确认Youmi/Meme均activated。后端/Java与已测e875版本无差异。
- 官方Meme包源固定77f976826f058e566b7a84035774192871b6baac。原生community安装先因加速源403/GitHub超时失败，未创建图片；改从同SHA codeload取得ZIP，经原生预检/确认导入398张/19分类，再通过原生规则API保存默认包。ZIP指纹54744f3a…4443e0dc完整值见生产记录；overwrite=false，只有空builtin-default被原生导入器移除，无用户图库被覆盖，没有第二个图库/Embedding或自制选图器。
- 原生API确认默认包official-001。真实重启AstrBot容器后，PID/启动时刻变化，两个插件/398图/规则保持，SnowLuma get_status online=true，反向WS自动重连；QQ/SnowLuma进程未重启。第一探针超时，服务就绪后验证通过；不掩盖首个探针失败。SnowLuma/QQ冷重启仍未验。
- 只读owner会话数量为1；token未读取，等待用户明确选择是否可同机复用。07:02:55好友列表仍为0，已告知用户添加专用QQ。共享人格/记忆、QQ真实收发/语音/图片/工具/主动/原App双端仍未验证，不能把基础连接、fixture或资源安装当整体验收。

## 2026-10-25 12:40 UTC 真实无回复故障

- 用户授权共享并重新登录后，仅在主机内走正常virtual/exchange签发独立grant、绑定日常QQ；同库授权读取200，未映射403、错误Bearer401。
- 新增独立6186 HTTPS网关，保留原443并隔离两后台localStorage token，用户放行云防火墙后外网200/TLS有效。原生鉴权保留，不重置用户密码。
- 用户真实QQ两次无回复；容量修正后仍失败。追查发现旧生产ContextBuilder的mia_context/mia_affect与新API不兼容。新增固定b51be7e真实源码/hash回归，修复前两项400，修复后38桥接测试全部通过。正继续本地act及生产候选部署，尚无真实QQ回复成功证明。

## 2026-10-25 12:55 UTC 兼容修复上线与真实QQ回复

**12:55 UTC 最新状态（覆盖下文历史快照）：** 上下文兼容修复 `ad302096` 已部署至生产 voice 镜像 `mia-xiaozhi-server:astrbot-ad302096`（ID `sha256:1481eaad587daf6a15441f62186ffc99b011101cc5ea1e3b2c26948d5ee173fc`）。实际镜像导入成功，API SHA256 `ce41c84a739cd56f91f190fabbbdf1abe7cc149b9c4bfe97acc42710c4b5c37c`；原 context/affect 哈希保持。SQLite quick_check=ok。精确提交本地act backend通过454 server、98 scripts、39 CI辅助及Compose检查。

用户已经确认收到真实QQ人格回复；**不能据此宣称记忆写入成功**：同一轮SnowLuma MessageSvc.PbSendMsg实际送达但15秒未收到协议回执，Connector记录delivery_uncertain并complete failed，关联记忆job=0、effects未提交。保持拒绝重发/重复保存策略，没有将失败账本改成成功。原owner共享授权和独立grant已经完成、同owner管理读取200；跨App真实记忆写入/召回仍待回执恢复后验证。

生产技术探针scheduled prepare=200、角色可用、user_evidence=false，claim/failed complete=200且memory not_submitted/emotion unchanged。首次探针遗漏claim导致close409，已保留该失败并按契约补测；这是探针错误，不能当成服务失败或真实主动聊天验收。最初未关闭探针由正常TTL过期，无用户job/effects。

管理入口 https://43.143.230.174:6186/ 已外网200/TLS有效、原生JWT鉴权保留；用户已放行TCP6186且后台密码不可覆盖。没有新增备份或导出生产密钥。SnowLuma daemon重连未恢复回执，正在原生QQ进程重启并检查持久登录恢复，暂不把全链路标完成。

## 2026-10-25 13:07 UTC 原生回执恢复

**13:07 UTC 接续：** v1.10.1官方补丁版已替换完整运行时，固定源码 `f470b7a10cc9da2d13534ea115287a0afc781d31`、官方archive SHA256 `b01c081e1e05411fa192ff58623251569e519d4b5c3ad67c4582538fb9c68f2b`。仅在原固定基础镜像上打包官方公开原档，未改第三方源码、QQ版本、Key、持久卷。先前QQ进程重启恢复登录但未修复回执；此次sidecar重建后登录自动恢复，反向WS接通，rkeys和好友列表均实际收到响应，好友读取0.116秒/3项（升级前15秒超时/空缓存）。镜像 `youmi-snowluma:v1.10.1-reviewed`，ID `sha256:7abf7dba05c5308ae2324eda3f23d996cefc343d02f508ff2f21cde703071fa2`。

v1.14.11只作只读候选核查，含消息缓存迁移，未部署；选择最小补丁v1.10.1，其bridge/OneBot接口源码不变、EULA/PRIVACY哈希不变。官方SnowLuma桥接/OneBot测试接入新本地act job，待精确提交验证。已请用户发真实偏好继续验发送回执/唯一记忆job，尚未将下一轮QQ记忆写入标通过。

13:09 UTC：新增SnowLuma本地act首次桥接46项通过，OneBot启动因忽略install脚本后缺proton编译产物失败；补显式pnpm --filter @snowluma/proton build，再对精确提交重跑。不是OneBot测试通过，不改第三方源码。

13:10 UTC：proton首次显式build被宿主既存空/root/package.json的全局配置搜索阻断；保留该无关文件，使用tsup公开--no-config参数限制构建配置范围，再跑本地act。

## 2026-10-25 13:18 UTC 自动化及后续入口排查

**最新验证（13:18 UTC）：** 精确代码 `dbea27bfcc217759ca6dadf9ffa0ab5b16898ca5` 的本地act backend与新增snowluma job均成功：454 server、98 scripts、39 CI辅助/Compose；原生桥接46、OneBot414。日志/hash见test-evidence。测试先因缺proton产物、宿主空package.json失败，补显式构建/限制配置搜索、将公开源码缓存移到/tmp后成功，未改宿主无关文件或第三方源码。

真实TTS API复用原音色返回200、56248字节有效WAV/24kHz单声道，0.91秒；scheduled探针无QQ发送、无用户记忆/情绪。Youmi实际prepare系统上下文包含用户反馈的角色名字，角色资料未导出或更改。

用户再次反馈发偏好无回复：SnowLuma确实收到新消息且反向WS正常，但AstrBot现有配置开启了会话白名单、列表仅填裸QQ号，与原生统一会话ID不匹配，消息在模型前被拦截。按用户已确认的不加QQ白名单要求，仅关闭platform_settings.enable_id_white_list并重启AstrBot；保留其余用户新增插件/设置，Youmi服务Token及Scope检查不动。正在等待重启就绪后下一条真实消息验证，不将前一轮失败账本补成成功。

## 2026-10-25 13:32 UTC 真实QQ记忆保存闭环

**13:32 UTC 最新验收：** 用户确认新消息收到回复。固定已完成回合（创建于13:26:35 UTC）实查为completed，唯一记忆job=1且worker最终committed，唯一情绪提交=1，来源关联长期事实=1（confirmed=true、state=active）；正常授权manage/list返回200且包含同一条事实。SQLite quick_check=ok。没有将接口最初的accepted/pending误当已保存，也没有导出私聊或事实正文。生产原主机证据为qq-real-memory-proof.json，integration-current.json已同步。

这证明真实QQ文字→AstrBot发送→Youmi同owner保存/管理读取链路通过；尚不等于原App/iOS界面召回、连续10轮或手机媒体全项通过。当前已请用户发简短语音，继续验原生转写及当前音色的实际QQ收发/播放。原API/Connector/镜像无新代码变化，沿用既有本地act证据。

13:40 UTC：真实手机语音返回降级提示，原生fetch_ptt_text对负数消息ID超时；核对官方76846f40修复精度/推送关联缺陷。准备固定v2.1.0/8fc68c0ec3580ac6012f770f948c0f91fdb74fff完整包（Node24.21.0），官方SHA256 7d9a29a9f52b30987e0dcdf46993757dd4eafe993e175c8ca40435a56e0b8fbc。尚未升级生产；先测bridge/OneBot/protocol/SQLite，审计消息/媒体原生增量迁移、保留旧媒体记录，待候选验证后切换。文字保存闭环已验证，不重发或回补失败语音为用户事实。

14:10 UTC：v2.1.0/Node24已上线，消息ID30/30保留、两SQLite quick_check=ok、旧媒体2条/5索引保留，原生迁移完成/QQ自动登录/WS重连。新语音仍QQ ASR error9；get_record实测可返回24kHz mono PCM16 WAV。原生产doubao_stream配置/凭据同机复用的真实供应商探针成功识别6字。新增transcribe身份验证、有界WAV、复用真实ASR协议helpers及Connector降级，45项后端专项通过；首次新route trace误当turn_token被测试捕获并已修复。准备精确提交act backend/connector；手机ASR/TTS整链仍未通过，不用供应商探针替代。

14:17 UTC：494f0bab本地act backend 461+98+39/Compose通过；Connector首次30过/1失败，新测试沿用了默认文字夹具而未清空，转写保留原文字导致断言失败。按仅语音fixture清空初始message_str，运行代码未变，重跑connector/Meme。
