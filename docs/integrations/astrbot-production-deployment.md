# 2026-10-25 生产部署记录（进行中）

**13:07 UTC 接续：** v1.10.1官方补丁版已替换完整运行时，固定源码 `f470b7a10cc9da2d13534ea115287a0afc781d31`、官方archive SHA256 `b01c081e1e05411fa192ff58623251569e519d4b5c3ad67c4582538fb9c68f2b`。仅在原固定基础镜像上打包官方公开原档，未改第三方源码、QQ版本、Key、持久卷。先前QQ进程重启恢复登录但未修复回执；此次sidecar重建后登录自动恢复，反向WS接通，rkeys和好友列表均实际收到响应，好友读取0.116秒/3项（升级前15秒超时/空缓存）。镜像 `youmi-snowluma:v1.10.1-reviewed`，ID `sha256:7abf7dba05c5308ae2324eda3f23d996cefc343d02f508ff2f21cde703071fa2`。

v1.14.11只作只读候选核查，含消息缓存迁移，未部署；选择最小补丁v1.10.1，其bridge/OneBot接口源码不变、EULA/PRIVACY哈希不变。官方SnowLuma桥接/OneBot测试接入新本地act job，待精确提交验证。已请用户发真实偏好继续验发送回执/唯一记忆job，尚未将下一轮QQ记忆写入标通过。


**12:55 UTC 最新状态（覆盖下文历史快照）：** 上下文兼容修复 `ad302096` 已部署至生产 voice 镜像 `mia-xiaozhi-server:astrbot-ad302096`（ID `sha256:1481eaad587daf6a15441f62186ffc99b011101cc5ea1e3b2c26948d5ee173fc`）。实际镜像导入成功，API SHA256 `ce41c84a739cd56f91f190fabbbdf1abe7cc149b9c4bfe97acc42710c4b5c37c`；原 context/affect 哈希保持。SQLite quick_check=ok。精确提交本地act backend通过454 server、98 scripts、39 CI辅助及Compose检查。

用户已经确认收到真实QQ人格回复；**不能据此宣称记忆写入成功**：同一轮SnowLuma MessageSvc.PbSendMsg实际送达但15秒未收到协议回执，Connector记录delivery_uncertain并complete failed，关联记忆job=0、effects未提交。保持拒绝重发/重复保存策略，没有将失败账本改成成功。原owner共享授权和独立grant已经完成、同owner管理读取200；跨App真实记忆写入/召回仍待回执恢复后验证。

生产技术探针scheduled prepare=200、角色可用、user_evidence=false，claim/failed complete=200且memory not_submitted/emotion unchanged。首次探针遗漏claim导致close409，已保留该失败并按契约补测；这是探针错误，不能当成服务失败或真实主动聊天验收。最初未关闭探针由正常TTL过期，无用户job/effects。

管理入口 https://43.143.230.174:6186/ 已外网200/TLS有效、原生JWT鉴权保留；用户已放行TCP6186且后台密码不可覆盖。没有新增备份或导出生产密钥。SnowLuma daemon重连未恢复回执，正在原生QQ进程重启并检查持久登录恢复，暂不把全链路标完成。


**07:03 UTC 最新：** Connector代码`55e9428e2da9b40bacc72ab36ad09122de4a7efc`已通过本地act：29项集成、Meme1045通过/1跳过/85子测试；只更新已核对旧hash的插件main.py并原生reload，未重启QQ。后端/Java文件与e875四job版本相同。

官方表情包通过Meme原生import/stage→import/apply导入398张/19分类，并通过原生settings/rules保存默认规则。素材固定为[官方包提交77f9768](https://github.com/anka-afk/astrbot-meme-pack-official-01/tree/77f976826f058e566b7a84035774192871b6baac)，ZIP SHA256=`54744f3ae5d518d70d8fd701c878a1877d587d662f222c3eb3dc6a4e4443e0dc`。首次原生community下载加速源403/GitHub超时失败；随后从同提交codeload取得原档，未自己管理图片或修改第三方源码。overwrite=false，原库只有空builtin-default占位包，原生导入器移除该空占位，不涉及原有用户图片；未生成生产备份。

已实际重启**AstrBot**容器：新PID/StartedAt成立，两个插件activated、398张图片/默认规则保留、SnowLuma原生get_status返回online=true，反向WS自动重连。首次重启探针超时，待服务就绪后复核通过，未冒称首次通过。**SnowLuma/QQ完整冷重启尚未测试**。07:02:55 UTC原生好友列表仍返回0；虚拟grant仍未签发，owner会话复用许可仍待用户选择，未读取token。上述运行验证不等同QQ手机收到回复或共享记忆已接通。

**06:43 UTC 更新：** 用户已完成QQ登录。固定SnowLuma原生API与日志均确认反向WS已连接AstrBot，native平台运行/错误数0。临时HTTPS桌面已关闭、htpasswd已移除、Nginx原哈希已恢复，QQ进程仍为原PID75，登录数据保留。原生好友列表当前返回0；已通知用户通过日常QQ加专用账号。独立虚拟grant仍未签发，原App数据未接入QQ；已询问是否可仅同机复用现有有效owner后台会话，尚待用户选择，未读取其token、未绕过权限。

补充开发：固定SnowLuma已有原生`fetch_ptt_text`，Connector waiting钩子可据原message_id取得真实QQ转写，再交原Agent；不新增ASR服务/Key。29项固定源码集成直接pytest通过，其中8项新增；初次旧代码5失败的日志保留。精确提交本地act与上线结果随后追加，当前不能把pytest fixture当手机语音通过。

本记录更新至 06:12 UTC，覆盖早期文档中“尚无生产写入权限/未部署”的历史状态。用户已明确授权实际生产部署，随后取消新增备份，并允许在生产主机内部复用现有模型 Key。没有新增生产数据、JAR 或配置备份，没有将模型 Key 回传本机或写入仓库。

## 已实际执行和核验

- 发布目录：生产主机 `/opt/xiaozhi-server/astrbot-fusion-e87596a6`，root 管理。运行代码提交 `e87596a630d33f6aec47df429ee717ff67c8c4c8`；构建配方提交 `30ab5114`。后续 Compose 修订见 git log。
- 现有六份 Compose 的顺序完整保留，最后添加 `backend-override.json`。只重建 Manager 和语音容器，未重启 MySQL/Redis、未执行 `down -v`，保留原镜像和全部数据卷。
- Manager 使用实际生产原 JAR 加严格三 class 补丁，未全量替换定制 JAR。公开 API 对比没有删除成员；补丁验证其他 ZIP 项内容/元数据、前缀和注释不变。
- 语音服务迁移实际 SQLite V7→V8；`mia_schema_migrations` 返回 1–8，`mia_astrbot_turns` 存在，`PRAGMA quick_check=ok`。升级前后所核对的全部 memories/traits/memory_search/mia_memory_meta/mia_affect 表指纹一致。`PRAGMA user_version` 原为 0，不能用它代替项目迁移表。
- 实际 bridge `/internal/astrbot/health` 带独立服务凭据返回 HTTP 200、schema=8、worker_available=true。Manager 原 HTTP 路由可访问，Spring 已启动、当时日志 ERROR 数为 0。
- AstrBot、SnowLuma 均已启动。通过 AstrBot 原生账号登录后的插件 API 核对：Youmi Connector v1.0.0、Meme Manager 5.2.5 均 activated=true；保留原生内置插件。AstrBot 6185、SnowLuma 5099/6081 页面已实际返回 200。
- 模型保持 `deepseek-flash`、原小何音色/角色未改。Key 仅从原主机配置复用到该主机 root0600 env 文件；AstrBot 配置使用原生 `$YOUMI_ASTRBOT_LLM_API_KEY` 引用。旧容量 profile 标着 GLM，未用于新桥接。桥接采用经[官方文档](https://api-docs.deepseek.com/quick_start/pricing/)核对的保守 32K 窗口、24K 输入、2048 输出预算，不修改原 App 配置。
- DockerHub 直连超时、GHCR 拒绝后，使用腾讯镜像分发相同的 amd64 digest，没有变更第三方版本。原始失败日志保留。
- AstrBot 原生 provider `check_one` 于 06:10:44 UTC 实际调用 `text_chat("REPLY PONG ONLY")`，deepseek-flash 返回 available，耗时 0.67 秒；不是 fixture，也未发送 QQ 或写入 Youmi 记忆。首次使用错误的原生路由返回 405，核对固定源码后改为真实 GET `/api/config/provider/check_one`；该失败未冒称通过。
- Compose 修订提交 `14390134daf413002675a673b53dcf3ccc2ba54f` 已再次通过本地act backend：452 server、98 scripts、39 CI辅助及Compose config。运行代码未再改变，Connector/两种Java沿用e875精确提交四job证据；日志与hash已分开记入证据JSON，没有把旧日志改标成新SHA。

| 实际镜像 | ID / digest |
|---|---|
| `mia-xiaozhi-server:astrbot-e87596a6` | `sha256:4337ad91a255f16f14066c88e28aea427c54089f5f091adfb1bfa142bc695efe` |
| `mia-xiaozhi-web:astrbot-e87596a6` | `sha256:3f2ad3bff39d164b95f79f743a787037990c033a337a11f6aea25ea63c1d2e1c` |
| AstrBot v4.29.3 | `sha256:d6f6508af78b9d0e427c0959a2fdd1016411720aad67cfcde8719870a89ee832` |
| SnowLuma v1.10.0 | `sha256:5680ab02064204668330685548829f12586ad4b5e51d3cc8274461d2c854b749` |

## 启动中发现并处理的问题

1. 旧 Docker builder 在 COPY 停滞，精确停止本次构建客户端后改用现有 BuildKit，两候选构建成功；未重启 Docker daemon 或其他项目。
2. 固定 SnowLuma 镜像真实工作目录/数据目录为 `/app/snowluma-data`，原模板 `/app/data` 错误。已修订持久卷目标并在首次启动前采用正确挂载。
3. 原镜像启动脚本先 chown，再以 root 新建 `config/runtime.json`，导致非 root daemon 写入 EACCES。仅将这个新文件所有权修正为 1000:1000，再启动 supervisor 的 snowluma 子进程，QQ 进程未重启。runtime 文件保留在正确持久卷；复现须先初始化文件所有权，见 setup。
4. 模板新增必填的随机桌面密码，生产未使用原镜像默认 VNC 密码。AstrBot 日志级别 WARNING、原生遥测关闭、QQ ID 白名单关闭；QQ adapter 暂未启用，bridge bindings 为空。

## 手机先登录 QQ

用户要求先绑定 QQ，且目前只有手机。已在现有 HTTPS Nginx 内添加临时、随机路径的 noVNC 代理，HTTP Basic + 原生 VNC 两层验证，不新增公网端口。未认证实际返回 401、认证后返回 noVNC 200；实际 WebSocket Upgrade=101，并收到真实 RFB greeting。TLS 证书验证开启，未忽略证书错误。

临时 URL/密码只交给用户，不写本仓库。Nginx 临时 location 关闭 access log，页面 no-store/noindex；原有接口路由不改。主机 `youmi-qq-login-expiry.timer` 设置一小时自动移除入口。登录确认后应提前运行：

```sh
python3 /opt/xiaozhi-server/astrbot-fusion-e87596a6/tools/close-mobile-login.py
```

清理器精确移除临时块，并要求移除后的 Nginx 哈希等于修改前哈希，语法通过再 reload；删除临时 htpasswd，并用原生 x11vnc `disconnect:all` 断开已经建立的桌面连接。已只读核验清理后的预期哈希一致、脚本语法通过。QQ 登录数据/进程/持久卷均保留。手机可在桌面底部点“账号登录”，QQ 密码由用户直接填入，不发送到聊天。

## 尚未完成、不得冒称通过

- QQ 登录与手机确认正在等待用户实际操作；尚未得到已登录 QQ 的运行证据。
- 独立虚拟 grant 尚未通过现有 owner 登录会话注册，内部授权检查保留；没有借用 iOS/实体设备凭据或伪造 Scope。
- 实际 QQ 收发、原生 Agent 工具续答、Meme 图片包、语音识别/播放、主动消息、QQ 重连和 App 双端记忆/情绪仍需后续逐项验收。
- Linux 环境没有 macOS/Xcode；原 App 编译/UI与设备硬件仍未验证。QQ 实时电话 NOT SUPPORTED / NOT VERIFIED。
- 未进行生产回滚演练。V8 数据库须使用当前 V8-aware 镜像移除 bridge 环境变量回退，不能直接启动原 V7 loader。默认备份步骤已由用户本次取消；现有镜像/数据卷保留不等于新增备份。

临时入口的到期时刻以主机 systemd timer 为准，勿在文档中保存或续用临时凭据。完整任务仍进行中。
