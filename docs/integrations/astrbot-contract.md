# AstrBot bridge HTTP v1

这些是本次新增接口，注册在已有 XiaoZhi aiohttp 服务。配置仅由服务端 `YOUMI_ASTRBOT_CONFIG_FILE` 提供；默认不开启。所有路由要求独立 `Authorization: Bearer <service token>`，TLS（容器内明文必须显式启用受控 Docker 网络配置）。不得复用 manager secret 或 iOS credential。

服务端保存 `(bot_uin, conversation_key) → 独立虚拟 device_id/client_id/credential_file` 映射，每次请求通过原 `PrivateAuthorization.verify/restore/require` 验证 manager 当前状态。QQ sender 仅核对来源一致性，不推导 owner。没有好友白名单配置。虚拟设备由登录所有者调用新增 `/device/mia-auth/virtual` 注册，再用原 code/exchange/renew/revoke。bridge-config 仅服务器密钥可调用且检查虚拟设备类型。

JSON 最大 64KiB。正常响应 JSON；失败为 `{"error":{"code":"..."}}`，不含原始异常/私有内容。400 invalid_request，401 service_unauthorized，403 authorization_required，409 duplicate_turn/context_changed/completion_conflict，410 turn_expired，413 payload_too_large，503 dependency_unavailable，504 dependency_timeout。永久冲突不自动重试。

## transcribe

`POST /internal/astrbot/transcribe` 仅接受 `protocol_version,bot_uin,conversation_key,sender_uin,source_message_id,audio_wav_base64`。同服务Bearer/TLS和BridgeIdentity当前Scope验证；前后均检查memory:read。仅此路由body上限4MiB，其他接口保持64KiB；base64严格校验，WAV最多3MiB/30秒、单声道PCM16、采样率8/16/24/48kHz，标准库有界重采样16k。复用原服务配置的doubao_stream ASR auth/framing/parser及现有资源，不向AstrBot暴露ASR Key，不触发回复模型或写任何事实/情绪。30秒总超时、共用媒体并发上限2；返回 `{text,source_kind:"asr"}`，文本1–4096字符。Connector仅对QQ原生转写失败的单个原始Record调用原生get_record转WAV，再调用此接口；保持原OneBot ID/raw，成功后走唯一prepare/claim/complete，失败走既有media-only提示。没有可信转写时不创建用户事实。

## prepare

`POST /internal/astrbot/prepare` 必填且只接受：`protocol_version:1, bot_uin, conversation_key:"qq:private:<peer>", sender_uin:<peer>, source_message_id, attempt_id, query_text, model, external_input_bytes, origin, source_kind`。ID 为 1–128 字符 `[A-Za-z0-9_:.-]`，bot/peer 为 5–20 位非零开头数字；model 是服务端登记的 1–128 字符名称（允许 provider/model）。origin 为 `user` 或 `scheduled`。source_message_id 直接来自 OneBot（cron 为原生事件 UUID），不从正文生成；attempt_id 为本次处理实例随机 UUID，同一 HTTP 重试不变。`query_text` 为 1–4096 字符。`source_kind` 为 `text/asr/media/scheduled`，scheduled 必须与 origin 一致；只允许 text/asr 建记忆 job。图文混合仅取原始 OneBot text 段作为用户证据，Record+Image 无法区分转写和描述时按 media，不把图片描述作用户自述。

`turn_key = sha256(canonical_json(["qq",bot_uin,conversation_key,origin,source_message_id]))`；session_id 是 bot+conversation 的散列。模型必须匹配服务端登记的 profile。external_input_bytes 保留为兼容已有 Connector 的非负整数元数据，不参与上下文拒绝或裁剪。桥接层不设输入预算，也不使用 profile.input_tokens 限制私有人设和上下文；历史图片的 Base64 字节数不能当作模型 token 数。聊天历史、媒体和模型上下文容量由原生 AstrBot/供应商处理；桥接层保留完整人设。模型回复配置保持不变，不从 QQ 消息取得。未知模型返回409 model_capacity_unverified。

返回 `turn_key,turn_token,status:"prepared",system_prompt,dynamic_context,persona_version,expires_at,affect_status`。system 取自真实 ContextBundle；dynamic_context 取第二条 user 消息中唯一的 youmi_context 或生产旧版 mia_context，保留所有证据字段。相应情绪输入必须使用同族 youmi_affect/mia_affect；不改原 App 的上下文格式。缺失或同时出现两族上下文时拒绝未知契约。既有 AstrBot 请求的 history/tools/image/audio 不覆盖；临时上下文不进入其永久历史。同一 attempt 重试返回冻结内容，另一 attempt 对同一回合返回 duplicate_turn 并停止第二次生成。已完成回合也不重发回复。

`dynamic_context` 另外明确 `input_origin:user|scheduled`、`source_kind` 和 `user_evidence`。定时system上下文明确它不是用户新发言。Manager未配置角色返回的prompt:null规范为空字符串，既有角色字符串原样保留；每次配置校验使用相同规范化后的角色hash。

STT失败不伪造ASR原话：Connector以media来源prepare，再check/claim发送固定文字提示并complete，不运行回复模型、不创建用户事实/情绪/事件；同一回合不会因重复钩子再发送。后端/授权不可用时仍按下述拒绝策略处理。

入站语音在原生waiting钩子、构建Agent请求前适配：若AstrBot原生STT尚未替换Record，则用现有OneBot客户端调用已核对的SnowLuma扩展 `fetch_ptt_text(message_id)`，返回`{text}`。SnowLuma按原始消息/PTT缓存关联QQ异步结果；调用限时25秒，接受非空且不超过4096字符的转写。只替换临时链内单个主消息Record，raw OneBot和稳定message_id保留；多个Record拒绝猜测，独立插件请求保持原样。此功能新增测试的QQ/API传输为fixture，真实手机转写另验。

冻结派生上下文仅在同一 SQLite 的新增回合表中暂存，最长 300 秒；用户来源摘录最多 512 字，完成/过期/失败立即或定期擦除。已终态只保留散列、状态、授权版本和完成结果，不做第二套事实表。为保证重放去重，终态键不随短期 payload 清理删除。

## check / claim

`POST /internal/astrbot/check`：`protocol_version,turn_token,final_text`，使用原 PersonaAgent 输出校验，只返回 `checked_text,replaced`，不改 Image/Record、不清 Meme 标签。

`POST /internal/astrbot/claim`：同上并含 `completion_id`。最终发送前重新验证权限、角色和 revision，再持久转为 sending；返回 checked_text。相同 claim 不能授权第二次发送（409 duplicate_send）；网络超时无法确认是否 claim 成功时停止发送，以免重复。必须接受分布式系统的投递不确定性：不自动重放有副作用工具或 QQ 消息。

## complete

`POST /internal/astrbot/complete`：`protocol_version,turn_token,completion_id,result:"adapter_accepted"|"failed",final_text,message_components:["plain","image","record",...],agent_tool_summary:[tool_name,...]`。两个列表各最多64个有界标识符；final_text 最大16000字符。提交成功 event.send 的本次逻辑最终回复；不是 QQ 已读/手机送达。failed 不入记忆/情绪/事件；已有成功 effects checkpoint 不允许改报 failed。claim 已固定 completion_id/文本散列，其他 ID/正文返回冲突。

权限每次恢复；SQLite 事务核对准备时 scope/revision/character/日界快照。情绪、原 events.complete 和本步骤账本同事务；随后调用唯一 service-owned worker.submit(TurnCompleted)。原 JobStore 幂等键保证崩溃后不会重复 admission；queue_full 保留 sending 可重试，成功 admission 后终态擦除 payload。completed 重试返回原结果，仍先验当前权限。授权撤销/删除/角色更改返回冲突，旧上下文不能入库。

返回 `turn_key,status,memory_job,emotion,events`；memory_job accepted 仅表示入队，不能声称事实已保存。scheduled/media 无真实用户原话，不创建用户 job；scheduled 不更新用户情绪证据。

原生 send_message_to_user 保留原权限和路径检查；调用前 check，只有原工具返回精确当前会话成功结果才登记。cron/无最终正文的工具投递，在 done 中 claim/complete 一次。其持久事件去重依靠 prepare，不能把这一工具路径称作“所有物理投递前均有 claim”；网络和进程不确定性下绝不重放原工具。普通最终链则始终先 claim 再 event.send。

## manage / status

`POST /internal/astrbot/manage` 只接受 `protocol_version,bot_uin,sender_uin,conversation_key,request`。重新映射 Scope 并要求 `memory:manage`，不从调用者接受 owner。request.action 仅 `list,update,delete,pause,resume,feature_settings,events,emotion_status`；除 resume 外字段/结果沿用原 `MemoryStore.handle`：update 需 id/content/confirmed，delete 需 id，pause 需 paused(bool)。不允许此接口编辑角色。pause 作用于同 owner 全部客户端。

用户明确“这段别记”时，当前 QQ session 的 memory_suppressed 跨重启持续；不提炼新事实、不提交情绪/事件，不删除已有事实，不改变 App 的全局 paused。`request:{"action":"resume"}` 仅清该 QQ 标志，返回 `{"qq_memory_suppressed":false}`；如果全局仍暂停，需另外 pause off。含糊“忘掉这件事”不删除；明确主题必须唯一匹配才调用现有删除，否则提示用户选择。该隐私开关是事实提炼开关，不等于 AstrBot 原生短会话历史清空。

`POST /internal/astrbot/status`：`protocol_version,turn_token`；仍要求当前有效 Scope，允许查询 expired。返回 `turn_key,status,completion,memory_job`。memory_job 是原 mia_jobs 的 id/state/error_category，未创建则 `{"state":"not_submitted"}`；没有原文/来源载荷。completed 与 job committed 是不同状态。不会因已完成回合而绕过撤销检查。

## tts / health

`POST /internal/astrbot/tts`：`protocol_version,turn_token,text`，权限与快照复核，原输出检查；从 manager bridge-config 的当前 TTS 配置生成 WAV（mono PCM16，24kHz，最多 60 秒/3MiB），二进制 `audio/wav` 返回。不会接收自选 voice、URL 或路径。复用 huoshan provider 协议 helpers；不运行 XiaoZhi 的流式 TTS queue。HTTP 不自动重试 TTS；本地临时文件在发送后删除。SnowLuma 负责 Record→QQ 编码，真机播放另验。

`GET /internal/astrbot/health`：同样需服务 token；仅返回 protocol/schema/worker 可用状态，不返回 owner、模型密钥或私有事实。不是供应商/QQ 在线健康证明。

## 超时和恢复

prepare 15秒（内含 manager 单次有界验权、RAG、情绪评价）；complete 10秒；tts 30秒。插件对 prepare/complete 最多 3 次有界退避，只重试网络/503/504；claim、tts 和消息发送不重试。complete body 在 AstrBot data 中以单独原子 JSON outbox 暂存，重启只重试 complete，不重新发送 QQ 或重放工具；成功/永久冲突删除敏感 outbox。过期账本定时清理。

后台每30秒清理超过300秒的临时 payload（因此空闲时清除延迟最多约30秒）；outbox 每5秒重试/过期清理，文件权限0600。情绪评价复用原 model_requests 有界槽，7秒外层等待和 provider 原生5秒请求超时；失败保留原状态。DB/授权整体不可用时停止 QQ 私有人格生成，避免使用未经验证的角色缓存；仅检索停用或向量不可用则按原 ContextBuilder 降级，返回明确 memory_status。后端日志仅 action/turn_key/outcome/耗时，插件可启用组件和工具名称日志；没有生产延迟/成功率测量结果。
