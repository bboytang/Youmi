# Youmi 与 AstrBot 自主管理

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

2026-10-26 当前状态：尚未部署。63 项 Connector 本地测试通过；759 项后端测试完成且通过（57 项跳过），39 项 CI 辅助测试通过；新 Java Controller 及授权测试编译通过，Java 运行验证尚待完成。首次直接后端测试因缺少固定版本夹具失败，补齐规范环境后通过；首次 host tooling 检查缺少 `python` 命令，按 CI 设置 PATH 后重跑。GitHub 推送被自动审批以缺少明确源码发布授权为由拒绝，已向所有者请求授权。现有未提交变更保留在原分支，单独验证分支将原部署基线与本次变更分为不同提交。
