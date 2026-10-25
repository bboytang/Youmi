# AstrBot 融合交接记录

**15:00 UTC 最新语音真机验收：** 用户确认“已识别成功，并回复语音，但是同时也回复了文字”。真实新语音回合 `6bf1708816d6a7713606aac1308a0cc382a4e4d4d8818b501bb8ee1ae0cfc505`（14:43:17 UTC）账本 completed；关联记忆job恰好1个、最终empty（本轮未提取长期事实），情绪提交恰好1次，SQLite quick_check=ok。14:30以来无Connector发送失败/TTS失败告警；diagnostics未输出该轮components日志，因此不虚构独立发送回执ID。手机收到语音和文字由用户确认，completed来自正常await发送后完成路径。

当前 `reply_to_voice` 策略是在语音输入时附加同一份回答的语音并保留文字，非两套模型各自回答。已询问用户是否改为语音输入只回复语音、生成失败回退文字；答复前保持现有行为。此轮只更新验收/计划/工作与交接，不改运行代码、不重启服务，沿用精确运行代码的本地act证据。QQ原生ASR error9没有被修复，成功链路使用已上线Youmi ASR降级。图片/GIF/Meme、真实工具/MCP/主动聊天及App界面双端仍待逐项真机验收；项目整体未宣称完成。


**14:27 UTC 当前接手点：** QQ文字回复、1个真实记忆job最终committed/1条active事实/1次情绪提交、正常同owner管理读取200已通过。最新SnowLuma v2.1.0固定8fc68c0，Node24.21.0；本地act原生桥接272/1skip、OneBot1576、协议1293、SQLite19通过。原生迁移30/30旧消息ID和原媒体记录保留、quick_check=ok、QQ登录/反向WS恢复。旧v1.10.1发送回执修复是历史，当前已升级。

QQ自带ASR对新语音仍返回error9，不能宣称已修好。已实现降级：原生get_record转WAV→有真实binding/Scope及前后授权检查的Youmi transcribe→复用原doubao_stream auth/framing/parser→临时Plain→唯一AstrBot Agent/标准回合。凭据只在原Youmi后端，不新建ASR服务/改变原App设置。真实已有语音的HTTP transcribe已200，0.947秒/6字，无QQ重放或记忆副作用；手机新语音整链正在等用户反馈。

语音后端镜像已换 `mia-xiaozhi-server:astrbot-494f0bab`（ID9c1ce637…68cb）；Connector main/client核对原55e哈希后原子替换并重启AstrBot，健康通过。494f0bab本地act backend461+98+39/Compose通过；77d1a744本地act Connector31、Meme1045/1skip/85subtests通过。中途19项Meme因本机磁盘不足失败；仅清本任务公开下载重复件/可下载Action缓存后复跑通过，没有改Meme或原图库。生产没新增备份，旧App角色/音色及数据卷保留。


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


**12:40 UTC 当前接手点（覆盖以下历史状态）：** 用户已明确授权记忆共享并重新登录，独立QQ grant已走正常virtual/exchange签发、绑定已配置；原owner接口读取200及越权403/错误服务token401已实测。无需再次询问授权或登录。AstrBot后台 https://43.143.230.174:6186/ 已外网TLS200；用户已放行6186，不要重置其后台密码。

两次真实QQ消息仍无回复。桥接容量先调整为已核实的deepseek-flash 1M上下文、262144保守UTF8字节输入上限；随后查明生产ContextBuilder仍输出mia_context/mia_affect，新API硬读youmi字段导致400。兼容修复及两项真实旧源码回归已完成，38桥接测试通过；接下来提交并本地act backend，候选构建仅更新API、切换voice容器，再验证实际prepare和QQ。不能宣称QQ已成功。

生产release仍为 /opt/xiaozhi-server/astrbot-fusion-e87596a6；实际镜像/插件SHA以部署记录为准。原App角色/模型/音色及数据卷保持，用户明确不要新增备份。以下待授权/空bindings等段落均是历史，已由以上状态覆盖。

**07:03 UTC 当前接手点：** 代码55e9428e本地act connector成功（29集成+Meme1045/1skip/85subtests），已上线并原生reload。官方Meme素材固定77f9768，经原生导入398图/19分类，默认规则已保存；首次网络下载失败后用同提交codeload原档导入，未重造图片管理。真实AstrBot容器重启证明插件、图库/规则保留、QQ仍online且反向WS自动重连；SnowLuma/QQ冷重启未验。

**待用户动作：** 只查到1个有效原owner后台会话，已询问可否仅同机复用它走正常virtual/exchange；尚无答复，token未读取、grant未签发，bindings为空，QQ不能使用原App私有上下文。好友列表仍为0，已通知用户加专用QQ。已有生产/模型Key同机复用授权不必重问；此处owner会话的明确问题保持待答，不绕过当前Scope。收到答复后继续原生授权和T01–T30真机联调，不能把任务标完成。临时HTTPS桌面已关闭并恢复原Nginx哈希。

最新证据与素材hash见production-deployment/test-evidence；下面06:43及更早条目为历史，插件不再是未提交修改。运行时release仍位于生产/opt/xiaozhi-server/astrbot-fusion-e87596a6，connector-deployment.json另记新插件SHA，避免只从目录名误判。

**06:43 UTC 最新：QQ已登录，OneBot反向WS真实connected，临时手机桌面已关闭并恢复原Nginx指纹，原QQ进程/登录态保留。好友列表返回0，已通知用户添加专用账号。现正等待用户选择是否允许仅在生产主机内复用原owner有效后台会话来走正常virtual/exchange；未读取token或签发grant，bindings仍为空。不要把QQ登录视为已经取得原App人格/记忆授权。**

新增入站语音适配复用固定SnowLuma原生fetch_ptt_text，等待钩子在Meme之前转换临时Record、保留raw；原native STT若已转换则保持原样。29项直接集成通过；目前工作树包含这一小改动，尚须精确提交act connector和原生插件reload。用户并未提供QQ好友/手机语音/原App双端验收结果。

更新：2026-10-25 06:12 UTC。**生产后端已上线，两sidecar已启动；用户只有手机，已提供临时HTTPS桌面，正在先登录QQ。** 先读[生产部署记录](astrbot-production-deployment.md)，其中包含实际镜像、V8迁移、数据指纹、原生插件/真实模型调用证明和临时入口清理办法。目标仍进行中，不能因上线或本机回归通过就标记完成。

最新完整act代码 `e87596a630d33f6aec47df429ee717ff67c8c4c8` 的四job全过：452 server、98 scripts、39 CI辅助、21 Connector、Meme1045/1skip/85subtests、当前/旧命名Java各190及JAR/严格补丁包。日志在 `/root/tmp/youmi-act-goal-audit`。后续只有Dockerfile/Compose/文档变化，须对相关变更完成最终检查；下文b329段是上一轮历史证据，不能充作最新SHA证明。

Compose修订 `14390134daf413002675a673b53dcf3ccc2ba54f` 已再次通过本地act backend（452+98+39/Compose），日志youmi-act-production-config/backend.log与hash另存证据JSON。临时HTTPS入口清理器同时断开原生VNC现有viewer；QQ进程不重启。待用户登录期间不要反复抓取可能含账号/密码输入的桌面。

用户最新授权：实际生产部署；明确不新增备份；允许仅在生产主机内部复用模型Key；先绑定QQ再继续其余验收。不要重新询问这些选择。没有备份文件、密钥导出或角色/模型/音色变更。内部Scope/owner授权不可跳过，QQ未登录/未签发虚拟grant时维持bindings为空、adapter关闭。

接续优先级：观察用户QQ登录结果（不截取输入密码的桌面）→确认bot UIN和用户会话→原owner合法注册/exchange独立虚拟grant→接入QQ→媒体/工具/Meme/主动/双端与重启验收。临时移动入口一小时定时关闭，成功后提前运行生产记录中的close脚本；不得将其URL/密码提交。旧V7镜像不能直接连接已迁移V8卷，回退保持当前V8-aware镜像并关闭bridge。

其余架构上下文见[方案](astrbot-implementation-plan.md)、[工作记录](astrbot-worklog.md)、[契约](astrbot-contract.md)、[兼容性](astrbot-compatibility.md)、[T01–T30](astrbot-acceptance.md)。以下旧段仅保留上一轮交付背景，由以上最新状态覆盖。

## 已确认范围

SnowLuma/反向OneBot、AstrBot内置Agent/工具/MCP、Meme管理所有表情、Youmi唯一人格/记忆/情绪/成长来源、无好友白名单、一次交付，不重问。后续Actions只用本地nektos/act，不启动GitHub托管运行。保留Youmi、CodeRove和原数据；不复制线上凭据、不擅自部署、不down -v。用户要求方案、工作和交接均持续写MD，已落实。

## 仓库与生产状态

- `/root/projects/Youmi`，`feature/astrbot-fusion`，基线`739c509eb3f4bc231d879367b33a15a8a94c38f5`。最终提交/验证SHA见验收末尾和git log；未跟踪的`YOUMI_ASTRBOT/`用户附件必须保留。
- 原origin仍是旧Mia地址。本次代码已推送到`https://github.com/bboytang/Youmi.git`的feature分支，不force、不推main；提交使用skip ci以遵循用户不运行远端Actions的指令。
- SSH只读生产核查已成功。当前模型deepseek-flash、小何2.0、SQLite V7、memories=0；实际镜像ID与源码preimage见audit。没有生产切换、虚拟grant注册、QQ登录、模型/TTS请求或密钥导出。
- CodeRove在`/root/projects/claude`、`claude-session-concurrency`；state与镜像保留。仅清可再下载缓存、旧归档journal和本任务无用下载，磁盘约170MiB→2GiB以上，没有删除其他项目数据。

## 已实现入口

- `server/companion/astrbot_api.py`：prepare/check/claim/complete/tts/manage/status/health，同原HTTP app；定期擦除payload，真实角色/Scope/容量校验与同库ContextBuilder。
- `astrbot_identity.py`、原两个Java授权类：独立虚拟设备/grant，沿用授权生命周期；QQ ID不等于owner凭据。
- `astrbot_turns.py`、`schema-v8.sql`：回合与effects checkpoint；原JobStore/唯一worker幂等入队。仅新账本，无第二个事实库。prepared/sending超过TTL会expired，不重放QQ或工具。
- `astrbot_media.py`：当前Huoshan配置/voice和真实协议helpers→有界WAV；其他TTS类型明确失败并回退文字。
- `install_youmi.py`与`youmi_adapter.py`：干净上游安装与runtime attach；`install_astrbot.py`只处理严格审计preimage的**离线候选目录**旧xiaozhi_adapter入口。未在生产apply。
- `integrations/astrbot_plugin_youmi/`：临时核心/动态上下文、请求去重、STT失败幂等文字提示、Meme后检查、原生event.send、outbox、原生cron/发送工具兼容、图文用户来源分离、STT失败处理、管理命令。原生私有插件发送不能全局拦截，见compatibility。
- `deploy/astrbot-fusion/`：两个sidecar、digest锁、占位配置、bootstrap、setup/rollback/smoke；生产备份和切换步骤留给获得授权的操作员。
- `.github/workflows/youmi-astrbot.yml`及`scripts/ci/run_astrbot_act.sh`：手动workflow、act本机runner、独立测试MySQL/Redis；未修改iOS/firmware代码。

## 验证状态

代码提交 **`b329c1e91903eb4ae801444862784ac0c46dc644`** 的三个本地act job全部通过：448 server（含40专项：35 Bridge/媒体/outbox、2 updater/迁移、3固定Huoshan协议）、98 scripts、39 CI辅助、16固定AstrBot/Meme集成、Meme1045passed/1skipped/85subtests、Java190全通过及JAR构建。唯一跳过项为未装botpy.message的QQ官方SDK测试；OneBot目标路径已执行。精确日志hash、JAR路径/指纹见[证据JSON](astrbot-test-evidence.json)。后续记录提交只改文档，运行代码与该测试提交一致。

真实QQ、供应商、App双端、生产迁移回滚、sidecar运行/重启尚未验证。Linux act不能提供macOS/Xcode；原iOS编译/UI和ESP设备硬件验证不能冒称完成。QQ实时电话NOT SUPPORTED/NOT VERIFIED。

## 本地继续验证

Python venv `/root/tmp/youmi-astrbot-venv`，固定来源 `/root/tmp/youmi-astrbot-audit`，最终原始日志 `/root/tmp/youmi-act-delivery`，生产源码只读副本 `/root/tmp/youmi-runtime-v7`。源码缓存有固定SHA标记，Meme/XiaoZhi的本任务临时.git已清理，用户仓库未动。所有测试session已结束，本任务测试MySQL/Redis与网络已自动清理；没有需要接续的运行中测试。

```sh
PATH=/root/tmp/youmi-astrbot-venv/bin:$PATH \
YOUMI_TEST_SOURCE_DIR=/root/tmp/youmi-astrbot-audit \
YOUMI_ACT_RESULTS=/root/tmp/youmi-act-next \
YOUMI_MAVEN_REPO=/root/tmp/youmi-m2/repository \
scripts/ci/run_astrbot_act.sh backend connector manager
```

旧tmpfs与settings在恢复环境后已不存在，曾导致一次manager在测试开始前失败；已用普通独立目录缓存和默认HTTPS Maven仓库重跑成功。现在不需要settings；如以后显式配置受控settings，先确认文件存在，不打印凭据。新运行使用act-next目录，保留act-delivery原始证据。manager驱动自动启动/停止`youmi-astrbot-tests`专用服务；不清CodeRove或生产Docker卷。运行中的shell脚本不要再编辑（此前发生过一次EOF，已经修复并记录）。

## 后续实际工作与边界

1. 本次离线开发、回归、文档和代码分支交付已完成；不要接手后重复跑无变化的全部测试或重新设计架构。最终记录提交可用git log和与代码提交的diff核实。
2. 生产验收需要写入/切换授权、QQ所有者扫码/手机操作、实际AstrBot模型容量/STT/MCP配置和表情包；获得条件后依setup及T01–T30继续。当前没有这些真机结果，不将fixture或只读审计当上线。
3. 本地Mac/Xcode仍是iOS编译/UI验证条件；Linux act不能提供macOS runner。原App与QQ共享记忆需要实际双端操作。
4. 回滚用V8-aware且关闭bridge的镜像，保留数据；原V7 loader会拒绝已迁移V8 DB。claim后崩溃可能delivery uncertain，不盲目重放副作用工具或QQ消息。
5. 原生独立插件私有发送不一定经过Connector，范围见compatibility；实时QQ电话NOT SUPPORTED/NOT VERIFIED。固定Huoshan以外TTS类型会回退文字。
