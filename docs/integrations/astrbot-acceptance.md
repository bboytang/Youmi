# T01–T30 执行与验收记录

日期：2026-10-25。当前是离线实现/自动化测试交付；**没有生产部署或 QQ/iOS 真机验收**。本报告区分固定源码测试、依赖fixture、只读生产审计与真实设备，任何离线通过均不得替换手机验收。

代码分支 `feature/astrbot-fusion`；基线 `739c509eb3f4bc231d879367b33a15a8a94c38f5`。最终精确提交的本地 act 结果将在完成运行后追加；当前工作区专项结果为39项后端、15项Connector通过。已有较早 act backend438+98、manager190/JAR成功、Meme1045+1skip/85subtests成功，不能把旧计数冒称最终提交结果。

证据代号：B=`server/tests/test_astrbot_bridge.py`（34项实际aiohttp/SQLite/RAG/JobStore，Manager/LLM/worker消费是fixture）；I=`test_astrbot_install.py`（2项离线更新/真实SQLite迁移）；A=`test_astrbot_tts_protocol.py`（3项固定Huoshan源码协议、WSfixture）；C=`integrations/tests/test_astrbot_compatibility.py`（15项实际上游类，QQ/模型网络fixture）；M=Meme原生suite；J=隔离MySQL/Redis真实Java完整suite；R=原有Python回归。

| ID | 执行方式及现有结果 | 真机/生产结论与剩余验证 |
|---|---|---|
| T01 | B单次/并发prepare、C原始OneBot序列化/无二次send通过 | NOT VERIFIED ON DEVICE：未执行QQ连续10轮 |
| T02 | C真实ToolLoopAgentRunner两次续答，保留工具ID/参数/结果通过 | 真实web search/模型调用未验 |
| T03 | C使用MCP CallToolResult格式、工具钩子保留；J内部权限通过 | 外部MCP成功/异常/拒权服务未连接，不宣称全项通过 |
| T04 | C实际Meme parser/response/decorate/after，无控制标签出站通过 | 真实表情包选择与手机显示未验 |
| T05 | M完整原生测试通过；联动仍调用Meme公开API | 真实向量/辅助模型和用户图片包未验 |
| T06 | C单GIF/仅图/文字GIF及非混合补图通过；非Plain保留 | 手机多图/动图播放未验 |
| T07 | B媒体不提炼；C图片字段保留、caption不进用户证据通过 | 真实QQ图片理解未验 |
| T08 | C STT失败停止；B ASR来源机制通过 | 真实STT质量/供应商/QQ语音未验 |
| T09 | A当前配置voice/private_voice及参数保留、真实协议helpers→WAV通过 | 未调用生产TTS；音色/格式手机播放未验 |
| T10 | C实际Record+Plain序列化，仅一次complete通过 | 手机两种组件实际接收未验 |
| T11 | B后台角色变化使旧快照失效；R character多Scope共用通过 | 真实后台修改后QQ下一轮未验，生产角色未改 |
| T12 | B向原JobStore admission与status真实pending；R抽取worker回归通过 | 未从QQ保存真实偏好再用iOS检索，不能将accepted当已存事实 |
| T13 | B同owner既有App来源事实召回/删除通过 | fixture中的App来源不等于真实iOS交互 |
| T14 | B原MemoryStore管理和删除使旧快照失效；R纠错/派生证据失效通过 | 双端真实纠正/删除未验 |
| T15 | B disabled/unavailable/no_match区分通过 | DB/授权完全不可用时拒绝私有生成；无可信角色缓存，不承诺此时继续个性化回复 |
| T16 | B伪owner/错误映射/撤销/版本变化拒绝；J新增虚拟设备授权16项子suite通过 | 生产虚拟grant尚未注册；未增加好友白名单 |
| T17 | B情绪投影/仅一次commit、R衰减/恢复/跨Scope状态通过 | 真实模型“生气→道歉”表达及手机重启未验 |
| T18 | B非法JSON保留旧状态；R情绪/原生模型超时回归通过 | 供应商超时率及正文体验未验；本轮评价有界等待 |
| T19 | B人格事件checkpoint与故障恢复通过；R成长/证据删除回归通过 | 生产没有为验证伪造成长证据 |
| T20 | B8个并发同消息prepare仅1个成功；重复complete不重复Job/effects通过 | 真实OneBot重传未验 |
| T21 | C真实两次tool续答+重复同/新req注入一次通过 | 真实供应商tool_call协议未验 |
| T22 | C重复after及原RespondStage无二次发送/complete通过 | 不能据after单独认定QQ送达 |
| T23 | B入队后故障、队列满/恢复；C短期outbox只重试complete通过 | 手机发送后崩溃仍可能delivery uncertain，无重放承诺 |
| T24 | B准备后删事实/改角色/撤销权限拒绝提交通过 | 生产竞态演练未做 |
| T25 | B重放键保留；Compose有重启与持久卷配置 | NOT VERIFIED ON DEVICE：未登录QQ、未演练断线/风控 |
| T26 | B重开账本/过期清除、outbox重开；I真实迁移失败回滚通过 | 两个实际sidecar重启、QQ登录态/图库卷保留未验 |
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

工作流本次为手动dispatch定义，开发中没有调用GitHub workflow_dispatch。测试源码SHA由每个job输出的source_sha给出；有未提交改动的旧日志只证明当时工作区。最终日志路径、计数、哈希和提交见后续记录。

## 真实阻塞和授权边界

已可SSH只读核查生产，因此不是“无服务器访问”。当前缺的是生产写入/切换授权、QQ账号扫码及实机操作、AstrBot实际模型容量与STT/MCP配置、用户选择的Meme图片包，以及本地macOS/Xcode。未复制生产密钥、未写数据库、未注册虚拟设备、未更改模型/角色/音色、未发布端口或重建现有服务。上述条件满足后按 [smoke-test](../../deploy/astrbot-fusion/smoke-test.md) 逐项记录，不需要重写本次代码才能开始验收。

QQ原生实时语音电话：**NOT SUPPORTED / NOT VERIFIED**。不以Record语音消息替代电话验收。
