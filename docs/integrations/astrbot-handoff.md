# AstrBot 融合交接记录

更新：2026-10-25。离线开发已完成，正在做最终提交与本地act验证；未上线、未做QQ/iOS真机验收。接手先读本文件和[方案](astrbot-implementation-plan.md)、[工作记录](astrbot-worklog.md)、[契约](astrbot-contract.md)、[兼容性](astrbot-compatibility.md)、[T01–T30](astrbot-acceptance.md)，不要从旧日志的“运行中”重新开始。

## 已确认范围

SnowLuma/反向OneBot、AstrBot内置Agent/工具/MCP、Meme管理所有表情、Youmi唯一人格/记忆/情绪/成长来源、无好友白名单、一次交付，不重问。后续Actions只用本地nektos/act，不启动GitHub托管运行。保留Youmi、CodeRove和原数据；不复制线上凭据、不擅自部署、不down -v。用户要求方案、工作和交接均持续写MD，已落实。

## 仓库与生产状态

- `/root/projects/Youmi`，`feature/astrbot-fusion`，基线`739c509eb3f4bc231d879367b33a15a8a94c38f5`。最终提交/验证SHA见验收末尾和git log；未跟踪的`YOUMI_ASTRBOT/`用户附件必须保留。
- 原origin仍是旧Mia地址。若推送使用明确`https://github.com/bboytang/Youmi.git`和feature分支，不force、不推main；提交使用skip ci以遵循用户不运行远端Actions的指令。
- SSH只读生产核查已成功。当前模型deepseek-flash、小何2.0、SQLite V7、memories=0；实际镜像ID与源码preimage见audit。没有生产切换、虚拟grant注册、QQ登录、模型/TTS请求或密钥导出。
- CodeRove在`/root/projects/claude`、`claude-session-concurrency`；state与镜像保留。仅清可再下载缓存、旧归档journal和本任务无用下载，磁盘约170MiB→2GiB以上，没有删除其他项目数据。

## 已实现入口

- `server/companion/astrbot_api.py`：prepare/check/claim/complete/tts/manage/status/health，同原HTTP app；定期擦除payload，真实角色/Scope/容量校验与同库ContextBuilder。
- `astrbot_identity.py`、原两个Java授权类：独立虚拟设备/grant，沿用授权生命周期；QQ ID不等于owner凭据。
- `astrbot_turns.py`、`schema-v8.sql`：回合与effects checkpoint；原JobStore/唯一worker幂等入队。仅新账本，无第二个事实库。prepared/sending超过TTL会expired，不重放QQ或工具。
- `astrbot_media.py`：当前Huoshan配置/voice和真实协议helpers→有界WAV；其他TTS类型明确失败并回退文字。
- `install_youmi.py`与`youmi_adapter.py`：干净上游安装与runtime attach；`install_astrbot.py`只处理严格审计preimage的**离线候选目录**旧xiaozhi_adapter入口。未在生产apply。
- `integrations/astrbot_plugin_youmi/`：临时核心/动态上下文、请求去重、Meme后检查、原生event.send、outbox、原生cron/发送工具兼容、图文用户来源分离、STT失败处理、管理命令。原生私有插件发送不能全局拦截，见compatibility。
- `deploy/astrbot-fusion/`：两个sidecar、digest锁、占位配置、bootstrap、setup/rollback/smoke；生产备份和切换步骤留给获得授权的操作员。
- `.github/workflows/youmi-astrbot.yml`及`scripts/ci/run_astrbot_act.sh`：手动workflow、act本机runner、独立测试MySQL/Redis；未修改iOS/firmware代码。

## 验证状态

最新工作区：39项后端专项（34 HTTP/账本/管理、2 updater/迁移、3固定Huoshan协议）、15项固定AstrBot/Meme集成、39项原CI辅助测试通过。较早完整act backend438+98通过；manager190/JAR成功；Meme1045passed、1skipped、85subtests。最终SHA的全套结果以acceptance末尾为准，不把工作区结果偷换成精确提交结果。

真实QQ、供应商、App双端、生产迁移回滚、sidecar运行/重启尚未验证。Linux act不能提供macOS/Xcode；原iOS编译/UI和ESP设备硬件验证不能冒称完成。QQ实时电话NOT SUPPORTED/NOT VERIFIED。

## 本地继续验证

Python venv `/root/tmp/youmi-astrbot-venv`，固定来源 `/root/tmp/youmi-astrbot-audit`，本地日志 `/root/tmp/youmi-act-evidence`，生产源码只读副本 `/root/tmp/youmi-runtime-v7`。源码缓存有固定SHA标记，Meme/XiaoZhi的本任务临时.git已清理，用户仓库未动。此前测试session已结束，没有需要接续的旧session。

```sh
PATH=/root/tmp/youmi-astrbot-venv/bin:$PATH \
YOUMI_TEST_SOURCE_DIR=/root/tmp/youmi-astrbot-audit \
YOUMI_ACT_RESULTS=/root/tmp/youmi-act-evidence \
YOUMI_MAVEN_REPO=/root/tmp/youmi-m2/repository \
YOUMI_MAVEN_SETTINGS=/root/tmp/youmi-m2/settings.xml \
scripts/ci/run_astrbot_act.sh backend connector manager
```

Maven目录是本任务tmpfs；settings可能有会话代理信息，不打印、不提交。manager驱动自动启动/停止`youmi-astrbot-tests`专用服务；不清CodeRove或生产Docker卷。运行中的shell脚本不要再编辑（此前发生过一次EOF，已经修复并记录）。

## 剩余步骤与真实阻塞

1. 最终diff/源码提交后跑上述act，记录精确SHA、输出摘要与日志hash；更新本文件和acceptance。已有通过测试不反复跑，除非新改动/失败或最终提交复现需要。
2. 最终证据提交与代码提交区分；推送feature不触发远端Actions。最终按用户要求的六段顺序汇报。
3. 生产验收仍需要写入/切换授权、QQ所有者扫码/手机操作、实际AstrBot模型容量/STT/MCP配置和表情包；获得这些条件后直接依setup及T01–T30推进，不重新讨论架构、不把未验项写成通过。
4. 回滚用V8-aware且关闭bridge的镜像，保留数据；原V7 loader会拒绝已迁移V8 DB。claim后崩溃可能delivery uncertain，绝不盲目重放副作用工具或QQ消息。
