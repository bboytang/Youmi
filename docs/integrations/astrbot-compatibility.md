# 固定版本兼容性与消息链证据

版本锁见 [versions.json](../../deploy/astrbot-fusion/versions.json)，完整审计见 [audit](astrbot-audit.md)。本文件的通过仅指固定源码与明确 fixture 的集成结果；没有 QQ 手机、真实 LLM/MCP/STT/TTS 或已部署 sidecar 证据。

## 实际执行顺序

固定 AstrBot 的 registry 按 priority 降序执行，测试使用真实 registry、MainAgentHooks、ToolLoopAgentRunner、RespondStage、AiocqhttpMessageEvent 和 OneBot serializer。

| 事件 | 实际策略及证据 |
|---|---|
| 普通消息入口 | Youmi priority100000 设置非流式、隐藏 reasoning；普通命令保持原 AstrBot 分发。 |
| waiting/request | Youmi100000 先标识原生主对话并注入核心/临时 TextPart；Meme99999 后处理。其他插件预设 provider_request 不被误当新用户回合。 |
| 工具循环 | 固定原生 runner 复用 req；测试执行两次工具续答，保留 call_id、参数、结果、ToolSet 和原 history。重复同对象/新对象 request hook 都只留一份 Youmi 块。 |
| response/done | Meme response99999 先处理标签；done -100000 只标记有效完成；原生中断文本 Output stopped. 不保存。done 本身不证明发送。 |
| decorate | Meme99999 决定混合链/待发送图片；Youmi -100000 只合并校验 Plain，保留非 Plain，通过 claim 后使用原 event.send。 |
| after | 成功 event.send 后调用公共 after；Meme0 发送 pending 图片，Youmi -100000 提交一次。清 result 后原 RespondStage 不会再发送。重复 after 不重复 complete。 |
| 原生 cron | 实际 CronMessageEvent + on_agent_begin 注入无保存 system/临时动态上下文；不伪造用户自述。原生 send_message_to_user 保留权限检查和默认会话语义。 |
| 发送工具 | check 聚合 Plain；用 Meme 的公开 compat_prepare_message 保留标签清理与选图。选中的单个图片临时放到 AstrBot 允许的 temp root，原工具仍检查文件权限，投递后删除副本；不复制图片库、不放宽路径权限。 |

## 已执行的兼容集成

`integrations/tests/test_astrbot_compatibility.py` 当前15项通过：4种真实 GIF/仅图/混合与非混合组合；文字+Record；真实 send 异常；请求对象复用；两次工具续答；独立命令/自调 LLM；complete 网络中断；原生 cron 工具+Meme；发送工具校验失败；拆分 Plain 的整体检查；图文输入用户来源分离；STT失败停止生成。

测试读取真实 GIF 文件并解码实际 OneBot 序列化结果；验证 Record base64 与临时 WAV 清理。LLM provider、HTTP客户端与 bot 网络 transport 明确为 fixture，未连接 QQ。原生数据库池在当前测试事件循环结束前释放，已修复首轮夹具的 event loop closed 警告。

Meme Manager 原生完整 suite：1045 passed、1 skipped、85 subtests passed。原生测试覆盖分类、语义候选、自动收集、图片管理和联动，但不构成实际供应商识别/QQ发送证明。最终 act 日志记录 skip 原因和精确 SHA。固定依赖仍出现 audioop/websockets 的弃用警告，未修改第三方源码来隐藏它们。

## 支持范围与限制

| 能力 | 代码支持 | 实际边界 |
|---|---|---|
| 普通文字、GIF、Image、多媒体链 | 保留原生链；Youmi只检查Plain | 手机收发、QQ客户端版本、风控未验 |
| 表情语义/辅助模型 | 原 Meme 能力和配置继续生效 | 未装入用户真实图片包、未调用其语义模型；不可称生产通过 |
| 工具、MCP、搜索 | 保留 ToolSet 和工具链；无另一个回复模型 | 真实 MCP 服务/权限与联网搜索未测；异常原生处理，不自动重放副作用 |
| 语音输入 | 原 AstrBot STT；标记ASR来源；失败不伪造转写 | 实际 STT provider 和手机音频格式需配置并验证 |
| 语音输出 | 当前 Huoshan 音色/config→PCM16 24k WAV→原 Record | 原协议构帧/解析与 WAV通过；供应商、手机播放和SnowLuma转码未验 |
| 主动消息 | 原生 cron 上下文、Meme公开联动和发送工具 | 真实调度时钟、静默/限频配置、手机送达未验；无用户事实job |
| 第三方命令/独立LLM插件 | 不误占主对话、保持其原权限和Meme scope | 私有 direct send 不一定经过本Connector；不能保证任意插件的私有回复都由Youmi审核 |
| 流式/分段 | 此QQ集成使用完整最终链 | 需关闭该adapter的分段和其他自动TTS；保留MCP/工具，不承诺流式融合 |
| QQ实时电话 | 无实现 | NOT SUPPORTED / NOT VERIFIED；Record不等于电话 |

普通链的持久 claim 在发送前；原生发送工具靠 prepare 阻止重复事件，工具成功后再 claim/complete。进程在发送边界崩溃可能造成 delivery uncertain，选择不自动重放；不能宣传分布式严格 exactly-once 或手机已读。已发现的 RespondStage 吞发送错误行为已由该适配显式处理。

复现与最终证据见 [验收](astrbot-acceptance.md)。
