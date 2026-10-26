# Youmi 与 AstrBot 自主管理

2026-10-26 已上线：两套后台已启用可撤销的长期管理委托；每小时原生 Agent 巡检已开启。真实 Agent 已独立调用两端管理工具，读写/读回/清理及重启后的权限、任务恢复均已通过。见[部署证据](../verification/youmi-autonomous-admin-20261026.json)。

所有者已在本次会话明确授权：Youmi 可自主判断、主动调整两套后台的全部配置，无需逐项确认；管理授权长期有效，直至主动撤销。

实现复用原生管理 API。`youmi_admin_catalog` 实时读取 OpenAPI 文档，列出接口并展开参数及引用的请求结构；`youmi_admin_request` 支持 GET/POST/PUT/PATCH/DELETE、查询参数和 JSON 正文。配置读取、修改、原生重载及读回检查均由同一 Agent 调用。没有 API 的宿主机设置、二进制上传接口不属于此工具的能力，不能据此宣称任意操作系统管理已开放。

普通对话和原生 cron Agent 都能调用。仅服务器清单指定的 bot/私聊身份可用；每次实际操作先经独立 bridge 服务凭据和当前虚拟设备 Scope 验证。其他好友不因本次授权获得后台权限。

## 长期授权与会话续期

管理授权不设置截止时间。Youmi manager 的 `sys_params` 参数 `youmi.autonomous_admin.owners` 保存获准 owner 的 JSON 数组，默认为未授权。新增 `/config/youmi-auth/admin-session`（兼容 `/config/mia-auth/admin-session`）仍由原 manager server secret 认证，并再次检查当前虚拟设备快照、绑定/凭据版本、账号启用状态和超级管理员身份。普通用户及真实设备不能兑换管理会话。

桥接新增 `/internal/astrbot/admin-session`，要求独立服务凭据、当前 `memory:manage`、bridge 配置中的 `autonomous_admin_owners` 和上述 manager 委托。返回的原生管理 token 仅供 Connector 内存使用，不交给模型。会话仍按原生 12 小时周期签发，每次管理调用都可凭长期委托重新取得，不依赖所有者再次登录。签发沿用原账号的单会话机制，可能续期该账号原有会话；退出后台登录不会撤销这份独立委托。

AstrBot 的 `native_delegation` 在明确本机授权和当前 manager 证明均通过后，调用同进程原生 `AuthService.generate_jwt` 签发标记为 `youmi_delegation` 的短期管理 JWT，仅在内存传递。这是一条新增的服务身份认证路径，不使用浏览器登录、不自动识别/输入验证码，也不改变人工登录的密码或 TOTP。所有者已通过正常管理员登录证明账号可用；此后长期委托不依赖交互验证码或短期 JWT 的自然到期。

已有虚拟设备凭据另有原生有效期。部署长期授权时须同步核对该凭据的到期策略；不能只延长后台会话就宣称整条授权链永久有效。撤销、解绑、禁用账号和凭据版本变更始终生效。

## 配置与停用

以 [服务器清单示例](../../deploy/astrbot-fusion/admin-manifest.example.json) 为模板，在本机创建 root0600 文件，并将路径填入 Connector 的 `admin_manifest_file`。此文件仅含 bot/私聊身份、固定目标服务、接口文档路径和服务委托标志。可信 Docker 网络明文需显式 `allow_internal_http=true`，其他环境使用验证证书的 HTTPS。

bridge 的 `autonomous_admin_owners` 与 manager 的 `youmi.autonomous_admin.owners` 均需包含当前所有者。可清空任一服务端委托列表、禁用清单、撤销虚拟设备或禁用账号以停止后续操作。已发生的修改不会因撤销自动恢复。

主动巡检通过原生 cron 创建，不新增第二套 Agent 或调度器。任务应基于实际故障和明确收益自行决定是否修改，并在修改后读回、按需要执行原生重载。任务间隔及任务本身也由原生配置管理。

工具返回原生 HTTP 状态和业务结果，HTTP 200 不等于业务成功或已热更新。断连/超时的写操作标为结果未知，禁用 aiohttp 对 PUT/DELETE 的隐式重试，先查询实际结果再决定下一步。重启可能中断当前回合，不能将其当作已完成回复。

服务委托免除日常管理对交互登录的依赖；原生业务接口对修改账户安全设置等操作的额外校验仍然有效，工具不会把验证失败当作已修改，也不提供图片验证码识别功能。

敏感配置值用内存占位符返回，可原样提交以保留原值；进程重启、跨目标或缓存淘汰后的占位符不可写回，需重新读取。日志 `admin-audit.jsonl` 仅保存请求 ID、目标、方法、路径摘要、时间和传输结果，不保存配置正文、密钥或新增备份。此日志不承担配置回滚功能。

## 验证

隔离 HTTP 测试覆盖真实读写、保留密钥、凭据轮换、服务会话签发、撤销、重定向拒绝以及写操作断连不重试。原生 Connector 测试覆盖用户/cron 身份及撤销拒绝；后端与 Java 集成验证明确委托、当前 Scope、超管要求、过期会话重新签发和撤销。最终提交、Actions 及实际部署结果在完成后追加，测试夹具不代表生产已经启用。

2026-10-26 上线前状态（历史）：尚未部署。63 项 Connector 本地测试通过；759 项后端测试完成且通过（57 项跳过），98 项 host tooling 及 39 项 CI 辅助测试通过；新 Java Controller 及授权测试编译通过，Java 运行验证尚待完成。首次直接后端测试因缺少固定版本夹具失败，补齐规范环境后通过；首次 host tooling 检查缺少 `python` 命令，按 CI 设置 PATH 后重跑。GitHub 推送被自动审批以缺少明确源码发布授权为由拒绝，已向所有者请求授权。现有未提交变更保留在原分支，单独验证分支将原部署基线与本次变更分为不同提交。

部署前核对发现：当前生产 Connector 与已记录的分段回复基线完全相同，但生产 `astrbot_api.py` 与本地已有的输入预算修改基线不同。上线必须以生产原文件为基础仅应用本次管理授权补丁，并记录最终运行源码指纹；不能把当前工作树整份覆盖到生产。既有输入预算、health schema 等差异不在本次授权管理变更中处理。永久虚拟凭据、两端委托开关、实际 API 写入读回及主动 cron 任务尚未在生产配置。

所有者随后明确授权推送验证分支并上线。`394afe34` 的 [GitHub 集成验证](https://github.com/bboytang/Youmi/actions/runs/40384902564) 五项全部通过：backend、connector、manager、manager-legacy、snowluma。首轮 `ebe7a81d` 保留失败记录：缺少 libopus、复制的 truststore 继承只读权限、公共 ECR 拉取限流；分别补原生依赖、仅修改隔离 truststore 为0600、使用官方 Docker Hub 同版本镜像后通过。生产 Manager 当前实际为 Youmi 类名，发布仅提取 CI 的当前类名 Controller；不把历史 Mia 类名补丁或整个新 JAR 覆盖现有定制。AstrBot 容器使用原生证书校验访问生产 Manager HTTPS 已返回200、151项 API 路径，无需关闭 TLS 验证。最小生产 API 补丁另通过授权/撤销/无记忆副作用专项。


## 实际上线与最终核对

生产实现来自 `5ad2a240dfe405fe7c43074eb2fdf0413eed85a3`，[该提交五项 GitHub 集成验证](https://github.com/bboytang/Youmi/actions/runs/40386166049) 全过。随后 `0bfdc4dd` 仅将身份报告的严格测试数量从16改为17，并明确要求新增永久委托测试存在；[最终五项集成](https://github.com/bboytang/Youmi/actions/runs/40388102194)、[身份与窄类范围验证](https://github.com/bboytang/Youmi/actions/runs/40388102367) 均通过，运行源码未改变。末尾交付记录提交只更新文档和证据，不将其冒称为不同运行源码的测试结果。

发布目录为生产 `/opt/xiaozhi-server/youmi-autonomous-admin-20261026`。Manager 仅替换已验证 Controller，1010 个原 JAR 项逐项核验，其余1009项内容保留。最终 voice 镜像在原生产镜像上仅替换实际运行的 `youmi_companion/astrbot_api.py`，与已验证提交逐字节一致。首次发布错误地将补丁放入旧 `mia_companion` 兼容目录，线上只读检查及时发现400；随后核对真实导入入口、修正镜像并恢复旧兼容模块原内容，再完成全部线上验证。上文据兼容目录作出的生产预算差异判断不代表实际运行入口；真实入口与原工作树既有基线相同，没有借此部署额外预算改动。

现有虚拟凭据有效期设为数据库支持的 `9999-12-31`，凭据值、绑定设备和版本未轮换，撤销/解绑/禁用校验继续有效；两端委托无自动到期时间。角色、原生后台密码、模型、Key、音色及数据卷保留。AstrBot 原模型仅补 `modalities` 的 `tool_use` 标记，原文字/图片能力保留，其他 provider 字段逐项确认相同。首次只读 Agent 任务因原模型只声明文字/图片能力而没有真实工具调用，不计为成功；补标记后，真实原生 Agent 任务 completed，并在该回合审计中出现两端预期 GET 的成功记录。

真实工具完成 Youmi 临时参数创建、修改、读回和删除，所有者 HTTPS 管理状态查询，以及 AstrBot 禁用任务创建、修改、读回和删除；临时项均已清理。正式任务 `youmi-autonomous-admin-20261026` 按 `0 * * * *` 运行，可自行根据实际情况调整两套后台配置及自身间隔，无需逐项确认；例行任务不主动发 QQ/语音/图片消息，也不把巡检当作用户事实。首次 Agent 验证只读，之后启用正式自主判断指令。最后重启 AstrBot，未重新登录、未输入验证码，委托工具、模型工具能力和启用的小时任务均恢复，下一次运行时间存在。

最终 Manager/voice/AstrBot/SnowLuma 均 running、无 OOM，两个 HTTPS 页面200；所核对权威表的行数及内容指纹在部署及全部检查后保持一致。没有新增备份或导出密钥、私聊和事实正文。QQ 实机收发、设备端和硬件本轮未做验收；本次验证范围是后台控制和原生主动 Agent 任务。

独立的[真实向量/合成记忆容量工作流](https://github.com/bboytang/Youmi/actions/runs/40384382066) 在一万条规模的容量性能门槛未通过，10/100/1000及条数/字节边界通过；记忆实现未在本次修改，此失败保留记录，不称为全仓所有检查均通过，也不以权限功能验证代替该性能验收。撤销方法仍为清空任一服务端委托列表、禁用清单、撤销虚拟设备或禁用账号。
