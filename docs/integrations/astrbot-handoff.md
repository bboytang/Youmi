# AstrBot 融合交接记录

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
