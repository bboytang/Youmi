# AstrBot 融合审计

## 13:07 UTC SnowLuma补丁实证

**13:07 UTC 接续：** v1.10.1官方补丁版已替换完整运行时，固定源码 `f470b7a10cc9da2d13534ea115287a0afc781d31`、官方archive SHA256 `b01c081e1e05411fa192ff58623251569e519d4b5c3ad67c4582538fb9c68f2b`。仅在原固定基础镜像上打包官方公开原档，未改第三方源码、QQ版本、Key、持久卷。先前QQ进程重启恢复登录但未修复回执；此次sidecar重建后登录自动恢复，反向WS接通，rkeys和好友列表均实际收到响应，好友读取0.116秒/3项（升级前15秒超时/空缓存）。镜像 `youmi-snowluma:v1.10.1-reviewed`，ID `sha256:7abf7dba05c5308ae2324eda3f23d996cefc343d02f508ff2f21cde703071fa2`。

v1.14.11只作只读候选核查，含消息缓存迁移，未部署；选择最小补丁v1.10.1，其bridge/OneBot接口源码不变、EULA/PRIVACY哈希不变。官方SnowLuma桥接/OneBot测试接入新本地act job，待精确提交验证。已请用户发真实偏好继续验发送回执/唯一记忆job，尚未将下一轮QQ记忆写入标通过。

实际v1.10.0镜像native hash匹配审计源码，问题非错装；QQ成功送达但HookPacketClient缺sendReply，守护进程重连及QQ重启均未恢复。v1.10.1只改native产物、系统信息展示与highway重试；bridge/OneBot TS源码逐文件对比无变化。官方补丁包替换后原生请求回执恢复。此前仅online/反向WS不能证明发送回执或好友列表成功，此处补足运行边界。

## 2026-10-25 12:40 UTC：真实 QQ 无回复故障补审

生产 context.py 的 SHA256 为 `d789354029972261d2ec934d242ee639edd3dd4511e9e7578b03293c865e0214`，与公开提交 `b51be7e770161fe759a192a2996031ebba5e54dc` 完全一致，实际输出 `mia_context/mia_affect`。桥接仅识别当前仓库 `youmi_context/youmi_affect`，导致 prepare 返回400，且会漏掉旧版情绪投影。此前入口补丁测试未覆盖这一生产者契约，是本次审计遗漏。

修复仅桥接读取：接受唯一的已审计上下文族并配对情绪字段，未知/混合契约拒绝。新增测试加载该提交未修改的真实 ContextBuilder 并核验SHA；修复前两项均400，修复后38项桥接测试通过，含旧版情绪只提交一次。不能将这一回归结果称为真实QQ成功；待本地act及候选镜像部署后继续验证。

原owner重新登录并明确授权后，已在主机内经正常virtual/exchange签发独立grant；同owner读取功能/记忆接口200，未映射身份403、错误服务Bearer401。模型凭据仅同机复用。AstrBot HTTPS管理入口6186已实测外网200且TLS有效，原生JWT鉴权保留；没有覆盖用户修改后的密码。

审计日期：2026-10-25。此文件先于实现形成，验收结果另见 acceptance；源码核查不等于 QQ 真机验证。

部署时补充实物核查：固定 SnowLuma digest 的 `/root/start.sh` 与镜像 WorkingDir 均指向 `/app/snowluma-data`，不是任务书示例中的 `/app/data`；Compose 已据此修正。原启动脚本先chown再以root创建runtime.json，实际触发daemon EACCES；只修新runtime文件1000:1000并重启daemon子进程即可，QQ进程不受影响。生产已获授权切换到V8及两个sidecar，实际镜像/数据校验/插件与真实模型证明见[部署记录](astrbot-production-deployment.md)；下文“未部署”和V7为最初只读审计快照。

QQ登录后补充音频核查：固定SnowLuma的`actions/extended.ts`真实注册`fetch_ptt_text/get_ptt_text/get_record_text`，`modules/media-actions.ts`按messageStore/mediaStore查原始PTT，`core/src/bridge/apis/extras.ts`调用QQ的TransC2CPttReq并等异步Event0x210/61，内部等待20秒。AstrBot原生PreProcess先STT，随后OnWaitingLLMRequest，再构建包含音频附件的Agent请求。因此Connector可在原waiting钩子、Meme之前使用已有OneBot客户端取得转写，替换临时Record并保留raw，避免新造ASR服务或复制ASR Key；不是根据示例假定的接口。

## 基线与线上只读核查

- `git fetch https://github.com/bboytang/Youmi.git feature/mia-persona-memory-v1.2`：HEAD `739c509eb3f4bc231d879367b33a15a8a94c38f5`；从该提交建立 `feature/astrbot-fusion`。原分支 `feature/YOUMI_ASTRBOT`，未跟踪任务书保留。原 origin 指向更名前的 Mia，未修改。
- SSH 只读访问现有主机成功。语音镜像 `mia-xiaozhi-server:dialogue-b51be7e-capacity-93b7172`，image ID/digest `sha256:42de95432d34fa93988f72807a8b80407222c2f3b0edfe1d200431c7f3272dfa`；web `mia-xiaozhi-web:ota-discovery-c1f03ce`，`sha256:48b4ceb66f0bbe80d693befbc029a846aa355ecdfa7cec6c0fc7868aaefb81e5`。镜像不是一个可直接等同 Git HEAD 的完整源码制品。
- 已安装模块目录 `/opt/xiaozhi-esp32-server/mia_companion`，生产仍使用 `xiaozhi_adapter.py` 名称；仓库使用 `youmi_adapter.py`。不能直接复制目录后假定旧入口自动更新。
- 现有 `core/http_server.py` SHA256 `4328d0acde360c619f0f8bf91927923dc9e927806a43bebe4bc3ae6f3df74d24`；`SimpleHttpServer.start()` 创建 aiohttp app 并注册 `/mia/management`，HTTP 8003。该端口宿主机仅回环，但容器网络仍可达，不能仅凭宿主机绑定认定内部 API 已受保护。
- 当前 Docker 网络 `xiaozhi-server_default`；保留六份现有 Compose 叠加配置；数据挂载为现有 `data` 目录，另有 TLS、容量 profile、音乐插件只读挂载。没有重建、停止容器或复制密钥。
- 使用 SQLite URI `mode=ro` 读取：schema V7，quick_check `ok`；owners=2，memories=0，traits=0，jobs=196，affect_state=1，character_profile=1。这是当前计数，不能沿用旧文档的 47 条事实来宣称仍存在，也没有为验收补造事实。
- MySQL 只读白名单字段查询：当前 agent 实际模型 `deepseek-flash`，TTS 模块 `TTS_HuoshanDoubleStreamTTS`，voice `zh_female_xiaohe_uranus_bigtts`，角色 prompt 长度 0，音量/语速/音调覆盖为 NULL。任务书所引历史 GLM 已非现状；不回写为 GLM、不修改模型或音色。本次没有调用模型/语音供应商。

## 固定源码

| 组件 | 固定引用 | 实际 checkout SHA |
|---|---|---|
| AstrBot | v4.29.3 | f61a7f42696da6df25dc7a249ec9b8d54ed9da18 |
| Meme Manager | metadata v5.2.5 | cf18e81c84fb3a713320addd57e56014d499f65c |
| SnowLuma | v1.10.0 | 7f391e065edb0a138b25c0456ed4151afc247ceb |
| XiaoZhi 干净安装/CI preimage | v0.9.7 | 87c6df77b0220ddbd8fc804f984f83f69f3225e5 |

源码通过独立 checkout 检查，不放入项目 vendor 目录，不修改第三方源码。固定 tag 的容器是否存在、digest 及本地运行结果在部署材料/验收中独立记录。

## 接口核查

| 项目 | 结论 | 证据与处理 |
|---|---|---|
| 真实服务与路由 | CONFIRMED / ADAPT | `server/README.md`；线上 `app.py` 在 HTTP 启动前调用 `start_worker(ws_server)`；`install_youmi.py:patch_http` 是 clean upstream 安装入口。增加同进程路由，复用该 worker，禁止第三个服务。 |
| QQ 虚拟客户端 | ADAPT | `manager_identity/YoumiDeviceAuthorizationService.java:binding/requireOwner/issue/checkScope` 依赖 ai_device 和现有 owner/agent；无注册虚拟客户端 API。新增受登录所有者权限保护的独立虚拟设备注册，沿用发码、交换、续期、撤销，不借用 iOS 凭据，不新增授权数据库。 |
| 真实角色与预算 | CONFIRMED / ADAPT | `context.py:ContextLimits/ContextBuilder.build/_assemble/validate`；`character.py:active`；角色来自 manager 模型配置中的 prompt，character 来自现有 SQLite。客户端不能指定 owner 或可信容量；服务端登记 AstrBot 实际模型预算，扣除现有工具/历史估计预算。只注入 system 和临时 youmi_context。 |
| 情绪与回合完成 | CONFIRMED / ADAPT | `affect.py:request_appraisal/parse_appraisal/commit` 依赖现有 OpenAI 兼容 provider；`commit` 调用者拥有事务；`context.py:complete` 自己开事务；`jobs.py:JobStore.submit` 同样自己开事务。不能声称三者天然整体原子。新增回合账本分别记录进度，并复用原生唯一键。 |
| 单一 worker | CONFIRMED | `youmi_adapter.py:start_worker` 创建 service-owned MemoryWorker；`MemoryWorker.submit` 当前重新验权并唤醒队列。API 获取该实例，不启动额外消费者。 |
| AstrBot 请求 | CONFIRMED | `core/provider/entities.py:95`：工具字段实际是 `func_tool`，不是示意中的 tools；`extra_user_content_parts` 为 ContentPart 列表，`TextPart.mark_as_temp()` 可用。`internal.py:307` 在 runner reset 前执行 request hook；正常工具循环复用 runner.req，不逐步重新发该 hook；新请求对象仍需逐对象去重。 |
| 钩子排序 | CONFIRMED | `core/star/star_handler.py:26` 按 `-priority` 排序；大数先执行。Meme request/response/decorate=99999。Youmi request 先于 Meme 让它读到语义/情绪，最终 Plain 检查后于 Meme。 |
| Agent done payload | CONFIRMED | `core/astr_agent_hooks.py:23`：response hook 后 done hook，参数为 `(event, run_context, llm_response)`；中断也可到 done，不能仅见 hook 就保存。 |
| 发送确认 | ADAPT | `core/pipeline/respond/stage.py:350-406` 捕获发送异常仍可能执行 after hook。Connector 在最终 decoration 中通过原生 event.send 发送一次同一消息链，成功才发完成信号，并清空 result 防止 RespondStage 重发；保留公共 after hooks。发送前持久 claim；不确定投递不自动重放。无 QQ 已读/严格送达保证。 |
| Meme 表情链 | CONFIRMED | `main.py:344-362`；`mixins/event_handlers.py:1076/1299/1470/1642`。response 清标签，decorate 加图，after 可补 pending images；`compat_prepare_message` 为联动接口。Youmi 不清 Meme 标签、不改图片库、不覆盖非 Plain。 |
| 原生主动调度 | ADAPT | `core/cron/manager.py:529-551` 直接 build_main_agent 并执行 runner，绕过普通 request hook；`on_agent_begin` 可修改 ContextWrapper.messages。必须显式 scheduled 来源；不能把唤醒提示当作用户原话提取。 |
| STT | CONFIRMED / 环境未验 | `core/pipeline/preprocess_stage/stage.py:221` 原生 STT 将 Record 转为 Plain；保留原实现，记录 ASR 来源；必须配置并验证实际 STT provider。 |
| TTS | ADAPT / 生产调用未验 | 旧独立网关常量不可信。`huoshan_double_stream.py:text_to_speak` 依赖连接/已有 monitor，不能伪造 ConnectionHandler。复用现有 provider 配置和二进制协议 helpers，单个收集 PCM→WAV 适配器；当前只适配生产使用的 huoshan_double_stream，其他 type 拒绝并降级文字。只从 manager 获取当前授权 agent 的音色，不从插件接受 speaker。 |
| OneBot 媒体 | CONFIRMED / QQ 未验 | SnowLuma `packages/onebot/src/message-parser.ts:154`、`packages/sdk/src/types/messages.ts:RecordSegment`；reverse ws 和持久化要求由官方 Docker/接入文档确认。手机播放、QQ 登录/重连/风控仍须真机验证。 |
| QQ 原生电话 | BLOCKED | 未取得可调用且已实测的 AV 契约，不实现/不宣称电话能力。 |

## 开发与验证顺序

1. 本审计 → 冻结真实 HTTP 契约。
2. 同库 V8 增量回合账本、现有 manager 虚拟授权、HTTP 与现有 worker/情绪/事件适配。
3. 固定 AstrBot Connector、媒体与原生调度适配、仅两个 sidecar。
4. 故障与权限测试、固定源码钩子集成、已有回归；按用户后续指令改用本地 nektos/act 精确提交验证，不启动 GitHub 托管运行；逐项填写 T01–T30。

生产变更不在当前授权内；需要生产安装/注册/QQ 扫码的项目明确未验证。新分支不覆盖角色、音色或原数据库，不执行 down -v。

参考：[SnowLuma 官方 Docker](https://snowluma.github.io/zh/docs/guide/deploy/docker)、[OneBot 接入](https://snowluma.github.io/zh/docs/guide/connect-bot)、[AstrBot 固定源码](https://github.com/AstrBotDevs/AstrBot/tree/f61a7f42696da6df25dc7a249ec9b8d54ed9da18)、[Meme 固定源码](https://github.com/anka-afk/astrbot_plugin_meme_manager/tree/cf18e81c84fb3a713320addd57e56014d499f65c)。
