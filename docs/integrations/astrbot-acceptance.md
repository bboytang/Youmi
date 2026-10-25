# T01–T30 执行与验收记录

**13:32 UTC 最新验收：** 用户确认新消息收到回复。固定已完成回合（创建于13:26:35 UTC）实查为completed，唯一记忆job=1且worker最终committed，唯一情绪提交=1，来源关联长期事实=1（confirmed=true、state=active）；正常授权manage/list返回200且包含同一条事实。SQLite quick_check=ok。没有将接口最初的accepted/pending误当已保存，也没有导出私聊或事实正文。生产原主机证据为qq-real-memory-proof.json，integration-current.json已同步。

这证明真实QQ文字→AstrBot发送→Youmi同owner保存/管理读取链路通过；尚不等于原App/iOS界面召回、连续10轮或手机媒体全项通过。当前已请用户发简短语音，继续验原生转写及当前音色的实际QQ收发/播放。原API/Connector/镜像无新代码变化，沿用既有本地act证据。


**最新验证（13:18 UTC）：** 精确代码 `dbea27bfcc217759ca6dadf9ffa0ab5b16898ca5` 的本地act backend与新增snowluma job均成功：454 server、98 scripts、39 CI辅助/Compose；原生桥接46、OneBot414。日志/hash见test-evidence。测试先因缺proton产物、宿主空package.json失败，补显式构建/限制配置搜索、将公开源码缓存移到/tmp后成功，未改宿主无关文件或第三方源码。

真实TTS API复用原音色返回200、56248字节有效WAV/24kHz单声道，0.91秒；scheduled探针无QQ发送、无用户记忆/情绪。Youmi实际prepare系统上下文包含用户反馈的角色名字，角色资料未导出或更改。

用户再次反馈发偏好无回复：SnowLuma确实收到新消息且反向WS正常，但AstrBot现有配置开启了会话白名单、列表仅填裸QQ号，与原生统一会话ID不匹配，消息在模型前被拦截。按用户已确认的不加QQ白名单要求，仅关闭platform_settings.enable_id_white_list并重启AstrBot；保留其余用户新增插件/设置，Youmi服务Token及Scope检查不动。正在等待重启就绪后下一条真实消息验证，不将前一轮失败账本补成成功。


**13:07 UTC 接续：** v1.10.1官方补丁版已替换完整运行时，固定源码 `f470b7a10cc9da2d13534ea115287a0afc781d31`、官方archive SHA256 `b01c081e1e05411fa192ff58623251569e519d4b5c3ad67c4582538fb9c68f2b`。仅在原固定基础镜像上打包官方公开原档，未改第三方源码、QQ版本、Key、持久卷。先前QQ进程重启恢复登录但未修复回执；此次sidecar重建后登录自动恢复，反向WS接通，rkeys和好友列表均实际收到响应，好友读取0.116秒/3项（升级前15秒超时/空缓存）。镜像 `youmi-snowluma:v1.10.1-reviewed`，ID `sha256:7abf7dba05c5308ae2324eda3f23d996cefc343d02f508ff2f21cde703071fa2`。

v1.14.11只作只读候选核查，含消息缓存迁移，未部署；选择最小补丁v1.10.1，其bridge/OneBot接口源码不变、EULA/PRIVACY哈希不变。官方SnowLuma桥接/OneBot测试接入新本地act job，待精确提交验证。已请用户发真实偏好继续验发送回执/唯一记忆job，尚未将下一轮QQ记忆写入标通过。


**12:55 UTC 最新状态（覆盖下文历史快照）：** 上下文兼容修复 `ad302096` 已部署至生产 voice 镜像 `mia-xiaozhi-server:astrbot-ad302096`（ID `sha256:1481eaad587daf6a15441f62186ffc99b011101cc5ea1e3b2c26948d5ee173fc`）。实际镜像导入成功，API SHA256 `ce41c84a739cd56f91f190fabbbdf1abe7cc149b9c4bfe97acc42710c4b5c37c`；原 context/affect 哈希保持。SQLite quick_check=ok。精确提交本地act backend通过454 server、98 scripts、39 CI辅助及Compose检查。

用户已经确认收到真实QQ人格回复；**不能据此宣称记忆写入成功**：同一轮SnowLuma MessageSvc.PbSendMsg实际送达但15秒未收到协议回执，Connector记录delivery_uncertain并complete failed，关联记忆job=0、effects未提交。保持拒绝重发/重复保存策略，没有将失败账本改成成功。原owner共享授权和独立grant已经完成、同owner管理读取200；跨App真实记忆写入/召回仍待回执恢复后验证。

生产技术探针scheduled prepare=200、角色可用、user_evidence=false，claim/failed complete=200且memory not_submitted/emotion unchanged。首次探针遗漏claim导致close409，已保留该失败并按契约补测；这是探针错误，不能当成服务失败或真实主动聊天验收。最初未关闭探针由正常TTL过期，无用户job/effects。

管理入口 https://43.143.230.174:6186/ 已外网200/TLS有效、原生JWT鉴权保留；用户已放行TCP6186且后台密码不可覆盖。没有新增备份或导出生产密钥。SnowLuma daemon重连未恢复回执，正在原生QQ进程重启并检查持久登录恢复，暂不把全链路标完成。


日期：2026-10-25。当前已生产部署且收到过真实QQ回复，发送回执修复后端到端记忆验收仍在进行；iOS/设备未验。本报告区分固定源码测试、依赖fixture、只读生产审计与真实设备，任何离线通过均不得替换手机验收。

代码分支 `feature/astrbot-fusion` 已推送，基线 `739c509eb3f4bc231d879367b33a15a8a94c38f5`。本地 act 对精确代码提交 **`b329c1e91903eb4ae801444862784ac0c46dc644`** 的 backend、connector、manager 三个 job 全部通过。之后的交付记录仅修订文档，运行代码和测试与该提交相同。完整计数、日志 SHA256 和 JAR 指纹见 [机器可读证据](astrbot-test-evidence.json)。

证据代号：B=`server/tests/test_astrbot_bridge.py`（35项实际aiohttp/SQLite/RAG/JobStore，Manager/LLM/worker消费是fixture）；I=`test_astrbot_install.py`（2项离线更新/真实SQLite迁移）；A=`test_astrbot_tts_protocol.py`（3项固定Huoshan源码协议、WSfixture）；C=`integrations/tests/test_astrbot_compatibility.py`（16项实际上游类，QQ/模型网络fixture）；M=Meme原生suite；J=隔离MySQL/Redis真实Java完整suite；R=原有Python回归。

| ID | 执行方式及现有结果 | 真机/生产结论与剩余验证 |
|---|---|---|
| T01 | B单次/并发prepare、C原始OneBot序列化/无二次send通过 | 用户确认新回复；实际回合completed且1个job/1次情绪提交。连续10轮尚未验 |
| T02 | C真实ToolLoopAgentRunner两次续答，保留工具ID/参数/结果通过 | 真实web search/模型调用未验 |
| T03 | C使用MCP CallToolResult格式、工具钩子保留；J内部权限通过 | 外部MCP成功/异常/拒权服务未连接，不宣称全项通过 |
| T04 | C实际Meme parser/response/decorate/after，无控制标签出站通过 | 真实表情包选择与手机显示未验 |
| T05 | M完整原生测试通过；联动仍调用Meme公开API | 真实向量/辅助模型和用户图片包未验 |
| T06 | C单GIF/仅图/文字GIF及非混合补图通过；非Plain保留 | 手机多图/动图播放未验 |
| T07 | B媒体不提炼；C图片字段保留、caption不进用户证据通过 | 真实QQ图片理解未验 |
| T08 | C STT失败通过prepare/check/claim发送一次文字提示，重复钩子不重发；B ASR来源机制通过 | 真实STT质量/供应商/QQ语音未验 |
| T09 | A当前配置voice/private_voice及参数保留、真实协议helpers→WAV通过 | 真实TTS API 200/WAV 24kHz单声道/56248字节，原音色不变；QQ手机播放未验 |
| T10 | C实际Record+Plain序列化，仅一次complete通过 | 手机两种组件实际接收未验 |
| T11 | B后台角色变化使旧快照失效；R character多Scope共用通过 | 真实后台修改后QQ下一轮未验，生产角色未改 |
| T12 | B向原JobStore admission与status真实pending；R抽取worker回归通过 | 真实QQ偏好job最终committed，1条active/confirmed事实，正常授权manage/list可见；iOS界面检索尚未验 |
| T13 | B同owner既有App来源事实召回/删除通过 | fixture中的App来源不等于真实iOS交互 |
| T14 | B原MemoryStore管理和删除使旧快照失效；R纠错/派生证据失效通过 | 双端真实纠正/删除未验 |
| T15 | B disabled/unavailable/no_match区分通过 | DB/授权完全不可用时拒绝私有生成；无可信角色缓存，不承诺此时继续个性化回复 |
| T16 | B伪owner/错误映射/撤销/版本变化拒绝；J授权子suite16项通过，含bridge-config实际成功、伪造owner、普通token、实体设备和撤销后拒绝 | 正常virtual/exchange已签发，生产同Scope读取200、未映射403、错误Bearer401；原生QQ会话白名单已关闭 |
| T17 | B情绪投影/仅一次commit、R衰减/恢复/跨Scope状态通过 | 真实模型“生气→道歉”表达及手机重启未验 |
| T18 | B非法JSON保留旧状态；R情绪/原生模型超时回归通过 | 供应商超时率及正文体验未验；本轮评价有界等待 |
| T19 | B人格事件checkpoint与故障恢复通过；R成长/证据删除回归通过 | 生产没有为验证伪造成长证据 |
| T20 | B8个并发同消息prepare仅1个成功；重复complete不重复Job/effects通过 | 真实OneBot重传未验 |
| T21 | C真实两次tool续答+重复同/新req注入一次通过 | 真实供应商tool_call协议未验 |
| T22 | C重复after及原RespondStage无二次发送/complete通过 | 不能据after单独认定QQ送达 |
| T23 | B入队后故障、队列满/恢复；C短期outbox只重试complete通过 | 手机发送后崩溃仍可能delivery uncertain，无重放承诺 |
| T24 | B准备后删事实/改角色/撤销权限拒绝提交通过 | 生产竞态演练未做 |
| T25 | B重放键保留；Compose有重启与持久卷配置 | QQ已登录，原生QQ重启和SnowLuma sidecar重建均自动恢复登录/WS；风控演练未验 |
| T26 | B重开账本/过期清除、outbox重开；I真实迁移失败回滚通过 | AstrBot真实重启保留插件/398图库及规则；SnowLuma重建保留QQ登录/配置并重连，资源证明见部署记录 |
| T27 | C真实CronMessageEvent+原发送工具+Meme，B不建用户job通过 | 真实定时、静默限频和手机接收未验 |
| T28 | B有界WAV/坏文件拒绝；A错包/跨会话拒绝；插件TTS失败回退文字 | QQ特定编码/大媒体传输边界未验 |
| T29 | C独立命令/自调LLM不创建用户turn；发送工具保持原权限通过 | 任意第三方私有direct-send不可普遍拦截；逐插件实测 |
| T30 | R全部server/scripts与J完整权限/构建；I V7→V8故障回滚通过 | 未改firmware/iOS；Linux act不能运行macOS/Xcode/iOS UI，实机和App编译不冒称通过 |

## 本地复现

需要 Python3.12隔离venv、Docker、act0.2.89、JDK21、Maven、git、openssl/keytool；首次拉依赖需要可达网络。固定外部源码按 versions.json 拉取，仅测试缓存，不修改第三方仓库。

```sh
python3 -m venv /tmp/youmi-fusion-tests
export PATH=/tmp/youmi-fusion-tests/bin:$PATH
export YOUMI_TEST_SOURCE_DIR=/tmp/youmi-fusion-sources
export YOUMI_ACT_RESULTS=/tmp/youmi-fusion-results
scripts/ci/run_astrbot_act.sh backend connector manager
```

脚本用 `-P ubuntu-latest=-self-hosted`，是act本地执行工作流，不是容器化ubuntu runner的等价性声明，也不是GitHub run。manager启动任务专用MySQL/Redis（tmpfs、回环端口），退出仅down这两个测试服务且不带-v；3306/6379必须空闲。Maven可选 `YOUMI_MAVEN_REPO` 与受控 `YOUMI_MAVEN_SETTINGS`，不得提交含代理凭据的settings。原iOS工作流依赖macOS/Xcode，Linux act无法执行，需要有Xcode的本地Mac，未伪造运行链接。

工作流本次为手动dispatch定义，开发中没有调用GitHub workflow_dispatch。测试源码SHA由每个job输出的source_sha给出；有未提交改动的旧日志只证明当时工作区。最终日志路径、计数、哈希和提交见下表及机器可读证据。

## 当前生产验收状态（2026-10-25 06:12 UTC）

07:03 UTC更新：QQ登录/OneBot反向WS已确认；官方Meme398图/19分类与默认规则原生导入，素材固定77f9768。真实AstrBot重启后插件/图库/规则保留且QQ仍在线、WS自动重连，补充T26的AstrBot一侧运行证据，不能将SnowLuma冷重启也标通过。55e9428e本地act connector为29项，新增原生fetch_ptt_text适配与失败/身份保护；Meme1045/1skip/85subtests。owner会话复用许可、虚拟grant和好友仍待用户动作，QQ手机收发/转写/播放与App共享断言保持未验证。详情见生产记录。

实际部署已获授权，用户已取消本次新增备份，并允许模型Key在原主机内部复用。Manager与语音后端已切换；SQLite真实V7→V8、quick_check和所核旧表指纹、bridge真实worker健康已通过。两个sidecar运行、原生插件加载、原生provider真实deepseek-flash调用已通过；后者0.67秒，没有发QQ或写记忆。手机临时HTTPS桌面认证401/200、实际WebSocket101和RFB greeting已通过。这些是部署/连接证明，不能替代T01–T30中QQ手机收发与原App双端断言；QQ登录和独立虚拟grant仍待实际完成。完整明细见[生产记录](astrbot-production-deployment.md)。

最新全量本地act代码为 `e87596a630d33f6aec47df429ee717ff67c8c4c8`：452 server、98 scripts、39 CI辅助、21 Connector、Meme1045通过/1跳过/85子测试、当前及旧命名Java各190通过并构建成功。40回合/120complete→40唯一job的1.21秒压力用例只代表本机HTTP/SQLite/JobStore，模型/Manager/worker消费是fixture。日志`/root/tmp/youmi-act-goal-audit`；下文b329表保留历史证据。

## 早期授权边界（历史快照，已被上述状态覆盖）

已可SSH只读核查生产，因此不是“无服务器访问”。当前缺的是生产写入/切换授权、QQ账号扫码及实机操作、AstrBot实际模型容量与STT/MCP配置、用户选择的Meme图片包，以及本地macOS/Xcode。未复制生产密钥、未写数据库、未注册虚拟设备、未更改模型/角色/音色、未发布端口或重建现有服务。上述条件满足后按 [smoke-test](../../deploy/astrbot-fusion/smoke-test.md) 逐项记录，不需要重写本次代码才能开始验收。

QQ原生实时语音电话：**NOT SUPPORTED / NOT VERIFIED**。不以Record语音消息替代电话验收。

## 最终本地 act 结果（2026-10-25 05:03 UTC）

| Job / 检查 | 实际结果 |
|---|---|
| backend / server unittest | **448通过**，其中本次专项40项：Bridge35、updater/迁移2、Huoshan协议3 |
| backend / scripts unittest | **98通过** |
| backend / scripts/ci unittest | **39通过** |
| backend / docker compose config | **通过** |
| connector / 固定AstrBot/Meme源码集成 | **16通过**，含STT失败文字提示与无重复complete |
| connector / Meme原生完整suite | **1045通过、1跳过、85子测试通过** |
| manager / 真实Java完整suite | **190通过、0失败、0错误、0跳过**；独立MySQL/Redis |
| manager / Maven package | **BUILD SUCCESS**；本地JAR指纹见JSON，不是已部署制品 |

Meme唯一跳过项：`test_qqofficial_sdk_delivery.py:14` 缺少`botpy.message`，属于QQ官方SDK路径；本集成目标OneBot路径已执行。保留固定依赖的audioop/websockets及Spring MockBean弃用警告；此前测试遥测后台线程警告已通过CI禁用原生遥测解决，没有过滤掉警告来制造通过。

三个原始日志在 `/root/tmp/youmi-act-delivery/`，各job都输出上述source_sha及act=true，结尾Job succeeded；日志hash保存在JSON。本地manager JAR位于JSON所列target路径，SHA256为 `1d6a7cf635a727bfb503e20cff1a6f8434e50aa342817d76ee0814e4b20e8a19`。测试专用容器与网络已退出清理，未操作生产/CodeRove容器或数据卷。

已审计生产源码副本另在一次性本地候选目录执行严格preimage的`install_astrbot --apply`，备份和源文件保留断言通过；日志 `/root/tmp/youmi-reviewed-runtime-updater.log`。这不是生产安装或完整镜像启动验证。

首次失败及修复保留在worklog：旧迁移夹具、TLS/上游frontend测试条件、原生测试context夹具、临时Maven settings丢失；最终结果不替换历史失败。没有GitHub托管run链接，因为本轮按用户要求全部使用本地act。
