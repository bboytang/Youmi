# 运行与部署

2026-10-25 已获授权并实际切换生产后端、启动两个 sidecar，真实模型调用已通过；QQ 登录及端到端验收进行中。当前实测状态、镜像和例外见[生产部署记录](../../docs/integrations/astrbot-production-deployment.md)。先阅读 [审计](../../docs/integrations/astrbot-audit.md) 与 [契约](../../docs/integrations/astrbot-contract.md)；不要用旧的“未部署”历史结论覆盖最新记录。

本次2026-10-25执行已获用户明确生产授权；用户随后明确取消新增备份。本次不执行下文第2项的新备份动作，保留现有镜像/数据卷并做升级前后完整性校验。该最新用户指令覆盖本次操作的默认备份步骤，不代表已创建备份。

## 版本与本地验证

源码及官方发行包SHA256锁在 `versions.json`。AstrBot固定amd64镜像digest；SnowLuma必须先按下方配方构建本地 `v1.10.1-reviewed` 子镜像，不使用已实测回执超时的裸v1.10.0运行时。其固定基础镜像保留现有QQ与启动/卷布局，只替换官方完整lite运行时，没有第三方源码补丁。当前配方及生产验证仅Linux amd64；arm64需要对应官方包、独立校验及真机复验，不能套用amd64 archive。

先在仓库外新建只含公开发行包的构建目录（不得放入生产配置/Key），下载[官方v1.10.1](https://github.com/SnowLuma/SnowLuma/releases/tag/v1.10.1)的 `SnowLuma-v1.10.1-linux-x64-lite.tar.gz` 并命名为 `release.tar.gz`，复制本目录 `snowluma.Dockerfile` 为该目录Dockerfile，然后执行：

```sh
docker build --pull=false -t youmi-snowluma:v1.10.1-reviewed /path/to/public-build-context
docker run --rm --network none --cap-add SYS_PTRACE --security-opt seccomp=unconfined --entrypoint /usr/local/bin/node youmi-snowluma:v1.10.1-reviewed --check /app/snowluma/index.mjs
```

Dockerfile会严格核验archive SHA256 `b01c081e1e05411fa192ff58623251569e519d4b5c3ad67c4582538fb9c68f2b`。若使用主机已有镜像镜像源，构建参数 `SNOWLUMA_BASE_IMAGE` 也必须保留固定 `@sha256:5680ab02064204668330685548829f12586ad4b5e51d3cc8274461d2c854b749`，不可只用可变tag；不要执行pull来覆盖运行中镜像。原基础Node含capability，普通BuildKit RUN中执行会Operation not permitted，因此语法检查放在上述无网络临时容器中，使用与正式sidecar相同的ptrace权限。此临时检查不挂载生产卷。

```sh
cp deploy/astrbot-fusion/.env.example deploy/astrbot-fusion/.env
docker compose --env-file deploy/astrbot-fusion/.env.example -f deploy/astrbot-fusion/compose.yaml config --quiet
python3 deploy/astrbot-fusion/bootstrap.py
```

bootstrap 仅在新 AstrBot data 下准备固定 Meme 源码，已有不同 checkout 会拒绝覆盖。Meme 初始没有图片：从原 Meme WebUI 导入用户选定的包并保存使用规则，不复制 Youmi 数据库或创建第二个选图库。

`.env` 中 `SNOWLUMA_VNC_PASSWORD` 必须替换为本次新生成的随机密码，不能保留占位符或镜像默认值。固定 SnowLuma 镜像真实数据目录是 `/app/snowluma-data`，Compose 已按实物修正；`/app/data` 不是本版本的持久化位置。首次启动前，使用原镜像的 Node 初始化该新卷中的 runtime 文件所有权，避免启动脚本以 root 新建后 daemon 无权写入：

```sh
docker compose --env-file deploy/astrbot-fusion/.env -f deploy/astrbot-fusion/compose.yaml run --rm --no-deps --entrypoint node snowluma -e 'const fs=require("fs"); const p="/app/snowluma-data/config/runtime.json"; fs.mkdirSync(require("path").dirname(p),{recursive:true}); if(!fs.existsSync(p)){fs.writeFileSync(p,"{}\n",{flag:"wx",mode:0o600}); fs.chownSync(p,1000,1000);}'
```

该命令只新建不存在的文件，不覆盖已有 runtime/QQ 配置，不启动 QQ。若已有文件报 EACCES，先核对固定镜像 UID/GID=1000 和文件所有者，再只修正该文件并启动 supervisor 的 snowluma 子进程，避免中断用户正在操作的 QQ。

按用户最新要求用本地 act 运行工作流，见 [本地验证脚本](../../scripts/ci/run_astrbot_act.sh)。本机 runner 会执行真实测试，模型/QQ fixture 明确标注；它不是 GitHub 托管 runner 结果。

## 现有后端候选制品

1. 再次核查运行镜像 ID、六份 Compose 顺序、挂载、JAR/补丁/static hash、当前模型和音色。保存镜像/Compose 来源清单。不要用一个干净上游 JAR 替换带既有定制的生产 JAR。
2. 在生产主机本地，以现有备份策略创建一致 SQLite backup（使用 SQLite backup API，包含 WAL 当前已提交数据），记录 quick_check、表计数/指纹及 V7；配置/JAR/静态文件/卷快照留在受控原主机，不导出任何私钥/凭据到仓库。没有这一步，不开始迁移。当前审计仅只读，没有创建备份。
3. 对固定干净 XiaoZhi v0.9.7 可运行现有 `python -m server.companion.install_youmi <checkout> --apply` 和 `install_identity`；新模块/SQL 会随该安装器复制。先在隔离测试目录验证。
4. 对审计到的已安装 V7 镜像，先导出**源码候选目录**到隔离构建上下文，然后运行 `python -m server.companion.install_astrbot <candidate-runtime>` 检查；`--apply` 仅用于离线候选目录。严格匹配三个生产 preimage，保留 `xiaozhi_adapter` 入口，不运行全量 clean installer 覆盖定制。实际 hash 改变就重新审计，不跳过检查。
5. 在隔离数据副本启动候选并校验 V7→V8、旧事实/向量/角色/情绪保持；完成回归后才能请求生产切换。V8 增量只加回合账本；不改原记忆表。回滚必须保留 V8 loader，见 rollback。
6. Manager 以同一固定上游 + 现有身份安装器构建；新虚拟注册/bridge-config 位于原授权 Controller/Service。必须通过 Java 权限回归；生产 JAR 只替换经过差异核对的新增/修改 class，保持其他 ZIP 项原样。

## 注册独立虚拟客户端

由现有智能体的登录所有者通过 HTTPS 调用 `/device/mia-auth/virtual`，正文仅 `agent_id,bot_uin`。返回新的 `astrbot-...` device_id、client_id 和一次性授权码；使用原 `/device/mia-auth/exchange` 换取独立凭据。不借用 iOS/硬件 ID 或 credential。续期/撤销仍走原 renew/revoke；凭据轮换后旧在途 Scope 失效，不能继续提交。

新的虚拟 credential 仅留在后端 secret 文件。另生成独立随机 service token，放在后端和 AstrBot 的 Docker secret 中；不要复用 manager secret，也不要记录到 WebUI 文本配置。示例均无真实密钥。

将 `bridge-config.example.json` 复制到受控配置目录，填入实际 bot/conversation 与虚拟 grant 文件路径。填写 AstrBot **实际选择模型**的容量、输出限额和可靠来源；占位符无法准备回合。审计时生产是 deepseek-flash，不回写旧 GLM；插件的模型选择由用户当前 AstrBot 配置决定，不擅自更换。

现有语音 Compose 的最后一个新 override 只对原服务增加：

```yaml
services:
  xiaozhi-esp32-server: # 必须核对原 Compose 的真实 service key
    environment:
      YOUMI_ASTRBOT_CONFIG_FILE: /etc/youmi-bridge/config.json
    volumes:
      - ./bridge/config.json:/etc/youmi-bridge/config.json:ro
      - ./bridge/service-token:/run/secrets/youmi_bridge_token:ro
      - ./bridge/virtual-credential:/run/secrets/youmi_astrbot_virtual_credential:ro
```

沿用现有 Docker 网络即可，无需临时 network connect。确认后端 8003 内网 DNS 可达。若复用已审过的可信 Docker 网络明文 HTTP，**两端**显式设置 allow_internal_http=true；其他网络使用真实 TLS/可信 CA，不关闭证书校验。宿主机回环端口不替代凭据验证。该 override 会要求重建语音容器，当前未执行。

## AstrBot / SnowLuma

只新增两个 sidecar。准备 secret 和实际 `.env` 后，由已获授权的操作员运行：

```sh
docker compose --env-file deploy/astrbot-fusion/.env -f deploy/astrbot-fusion/compose.yaml up -d
```

按本项目已确认选择，将AstrBot原生 `platform_settings.enable_id_white_list` 设为 `false`；不建立QQ好友/会话白名单。不可将裸QQ号填入原生会话白名单来代替Youmi授权，原生这里匹配的是统一会话ID。Youmi独立服务Token、binding和当前Scope校验始终保留。

首次启动保持 OneBot adapter/LLM 接收关闭，先确认 Connector 成功加载、后端 health/worker 可用、独立 grant 和模型容量已配置，再启用 QQ 接入。通过 SSH 隧道访问 AstrBot 6185、SnowLuma 5099/6081；均只绑定宿主机 127.0.0.1。OneBot 6199 不发布到宿主机。

AstrBot 添加 OneBot v11 adapter，监听 `0.0.0.0:6199`，Token 两端一致。SnowLuma WebUI 的 wsClients 配 `ws://astrbot:6199/ws`，填同一 Token；QQ 扫码由账号所有者操作。桌面随机密码留在受控主机，勿发布。

在 Connector 配置填真实后端 DNS URL、secret 路径、adapter platform ID、主动调度用 bot UIN。AstrBot Persona 不放另一套人物故事，保留原授权工具/插件/MCP；同一 QQ 配置关闭长期事实提取、其他自动 TTS、流式和分段输出/中间工具推理发送。Youmi 不禁用 tools，也不修改第三方源码。

语音先沿用已配置的原生 STT；若仍有单个 Record，Connector 经现有 OneBot 客户端调用固定 SnowLuma 的 `fetch_ptt_text(message_id)`，复用 QQ 原生转写，不需要新增 ASR Key 或基础设施。默认可保持 provider_stt_settings.enable=false 使用这个路径；未做手机实测前不能宣称转写可用。QQ 图片/GIF 走 AstrBot 的视觉或 image caption provider；若聊天模型不支持图片，必须配置原生视觉提供者，不能用文本 fixture 宣称识图。当前 TTS adapter 支持生产 Huoshan 双流模块，取 manager 当下 voice/参数生成有界 WAV；其他 TTS type 返回不支持并回退文字。Record 最终由 SnowLuma 编码，手机可播放必须实测。

主动聊天沿用 AstrBot cron 的静默/限频/工具权限设置；Connector 不自建调度器。原生 cron 在 `on_agent_begin` 注入 scheduled 上下文，禁止把唤醒提示提交为用户事实。Meme 的候选/辅助模型/独立插件输出范围按其原配置逐项验收，不能说所有第三方插件自动兼容。

健康路由需 service token，只证明 bridge/worker 摘要；不等于 QQ 在线、供应商可用或手机收到。固定 AstrBot 的 STT 会在 INFO 输出转写，生产将其 `log_level` 设为 `WARNING`，关闭包含正文的调试/trace导出。后端桥接可独立保留 action/turn_key/耗时日志，避免开启完整请求正文。STT失败由Connector发一次有claim保护的文字提示，不保存假转写；后端或授权不可用时停止私有生成。

## 当前主机的 HTTPS 管理入口

AstrBot 为 https://43.143.230.174:6186/ ，保持原生登录/JWT。`astrbot-gateway.conf` 在现有Manager Nginx的http块中include，复用主机已有TLS只读挂载，新增6186端口映射；原443/8002 server保留。不要将两个后台放在同一origin，否则它们的localStorage token会冲突。当前生产override同时只读挂载追加include的nginx.conf与本文件；Nginx -t和Compose config通过后仅重建web容器。证书路径是当前主机实际路径，其他主机需提供自己的证书。后台初始密码不进入仓库，也不覆盖用户修改后的密码。
