# 记忆系统生产部署与验收记录

2026-10-26。产品源码为 `8048536bc8c40dcc7d8dcf3ff817d4339cfa6025`；后续仅记录文档的提交不替代这个发布 SHA。生产已部署并启用，真实 QQ/App/设备聊天验收仍单独待做。

## 准确提交验证

| 项目 | GitHub Actions | 结果 |
| --- | --- | --- |
| 四浏览器管理页 | [40326620348](https://github.com/bboytang/Youmi/actions/runs/40326620348) | 212通过，0失败/跳过/波动 |
| 双Python兼容矩阵 | [40326689994](https://github.com/bboytang/Youmi/actions/runs/40326689994) | 通过 |
| 完整后端 | [40326700111](https://github.com/bboytang/Youmi/actions/runs/40326700111) | 通过；同SHA自动40326619930也通过 |
| Java、绑定授权与JAR | [40326674093](https://github.com/bboytang/Youmi/actions/runs/40326674093) | 通过 |
| 管理端基线 | [40326682523](https://github.com/bboytang/Youmi/actions/runs/40326682523) | 通过 |
| 真实BGE、安装、质量、容量、切换回退 | [40326706949](https://github.com/bboytang/Youmi/actions/runs/40326706949) | 8份报告passed，质量/隔离/数据不变/容量门槛全true |

真实提供者另由 `3046b54e04abd57153616db32b0013dcef474d28` 候选的隔离探针通过，发布版本67个运行文件与其逐项字节一致：两条记忆反思各1次HTTP，时间变化保留旧历史；有据情感评价joy增加且51维配置保留；删除清理完成后实际回答承认未知。共4次请求，预算6次。报告 `youmi-memory-takeover-3046b54e/live-probe-attempt-01/live-model-probe.json`。此项不冒充真实QQ对话，也不将另一个聊天提供者工作流的success单独当作在线模型证据。

质量：冻结回归集48正例召回/首条与旧算法同为97.92%，16负例空召回100%；独立32正例召回/首条100%，24负例空召回100%。这些检索集未执行提供者回答，回答未知的证据仅来自上述隔离探针。容量10000条受控暖读p95 310.98ms、旧192.55ms，满足400ms门槛，不能宣称新算法更快。

## 发布产物与保留项

前端制品 `12652550533`，ZIP SHA256 `6ffca46de263b1a89417720acd348ab5b22b9646614080ab1b8ee5f63381a6a7`。454个文件均与090候选逐字节相同，最终镜像继承原候选并逐项重新校验；HTTP 8002实际返回的index也与本次制品匹配，保留历史hash资源。

Java制品 `12652280007`，ZIP SHA256 `040bdd5da1240cc424b4cbb4fa8bfae3c7e28ba2f8ecc26a973259afecdbad95`。替换管理类SHA256 `4dfac990db39b5f2c11fa7bae7ef663a931e14eb541b2861369bc267d925cbe6`，与090候选相同；JAR其余1009条不变。生产JAR SHA256 `a90389e5f3f09b611e679199abd62fd8601d49f388e6a5a616cce054fc2bc8da`，已在运行容器核对，无生产JAR导出。

| 服务 | 镜像 | 运行镜像ID |
| --- | --- | --- |
| 语音后端 | `youmi-voice:memory-8048536b` | `sha256:98de108b2ac35ef2b2fbdff9a070f66d45b297a69f2c32675d5bbe281ff02081` |
| 后端管理页 | `youmi-manager:memory-8048536b` | `sha256:fa6288f6f141058fd7e5ce153769dac81c65c51860259642f562bc09816472a5` |

两服务启动时间为2026-10-26 13:12:33 UTC，最终核查均running、restarts=0。继承当前生产定制，67个运行文件、2个线上无输入裁剪定制文件和9个原生入口已再次核验。未覆盖工作区并行QQ改动。Compose只改两image，原10/9份父配置、其他服务、卷、证书、凭据、端口及音乐绑定保持；AstrBot与SnowLuma的启动时间未变，未重启侧车。未新增数据库/密钥备份，未向QQ外发测试消息。

## 迁移和在线切换

首次停服后的只读审计发生SQLite打开失败，尚未执行迁移，立即启动原V10服务并再次核验。原因与WAL模式下缺失/重建共享内存文件的只读访问有关；最终离线审计仅在确认后端已停止、WAL不存在或大小0时使用immutable只读连接，非空WAL明确拒绝，不能忽略未合并日志。全程数据目录仍只读挂载用于审计，未为核查创建副本。

第二次停服建立准确静止边界，随后新程序离线事务迁移V10→V18。迁移前后 `memories/sources/traits/checkpoints/mia_memory_meta/mia_source_meta/mia_feature_settings/mia_companion_profile/youmi_affect_system/mia_emotion_consent/owner_flags` 的行数及全行SHA256全部一致。实际切换前已有23条记忆及23条来源，不恢复此前只读审计时的20条旧状态。当前人设为原有v9，1677字/4576 UTF8字节，SHA256 `324c8e198b0f2d102eda47506d5a798628319476a11ce40eb9842291e5859c97`，没有覆写成更早版本。

通过既有虚拟设备凭据验证绑定智能体，再以真实管理端所有者证明调用 `/youmi/management`；不将任意数据库owner当作授权对象。首次启用请求被拒绝、未切换，保留 `target-activation.json`；该次脚本未采集状态码，不能推断具体原因。之后只读确认active jobs/native/unexpired QQ turns均0、重新读取当前版本，使用原CAS接口成功。未绕过权限或在途任务保护。反思与情感联动均为从未配置的version0，按已批准方案首次开启；已有51维和四维认知原值/模式保持，认知总开关原为关闭，继续保留关闭。

生产内部健康：schema18、worker_available=true、protocol_version1；无凭据的管理请求返回401。真实BGE对实际所有者当前事实召回成功，逐项核对canonical正文/版本且结果为正式引擎，受控缓存complete。没有向模型发送这次生产事实核查内容。

实际回退演练顺序为 `youmi@1 → legacy@2 → youmi@3`，同一V18运行包、无schema降级。两引擎均完成实际BGE/canonical召回；演练前后完整记忆/来源/元数据/人设/特性/情感状态与配置/认知配置与状态摘要全相同。最终保持 `youmi@3`、反思和情感联动开启、认知原开关关闭、缓存complete。

## 操作记录与后续交接

生产主机43.143.230.174，可复用现有root默认SSH身份；不要再次向用户索要密码或私钥。持久操作目录：`/opt/xiaozhi-server/youmi-memory-takeover-8048536b`。其中包括：

- `source-manifest.json / manager-class-manifest.json / frontend-manifest.json / ci-proof.json`：源码及制品来源。
- `compose-command.json / compose-proof.json / compose.youmi.json`：仅两镜像变化的Compose配置。
- `deploy.py / audit_db.py / target_control.py / state_boundary.py`：本次操作脚本，不含密钥正文。
- `quiesced-before-migration.json / quiesced-after-migration.json / migration-preservation-proof.json`：只读摘要及迁移数据不变证据。
- `target-activation.json / target-activation-02.json / target-rollback-legacy.json / target-legacy-recall.json / target-restore-youmi.json / target-youmi-recall-final.json`：失败、成功和实际引擎回退回执。
- `rollback-before.json / rollback-after.json / live-rollback-proof.json / served-frontend-proof.json`：回退不变与实际静态服务证据。

`deploy.py`是单次V10发布脚本，当前已V18，不能重复执行或启动旧V10镜像。后续回退仅用页面或同包管理接口按最新version切legacy；存在在途任务则等待，不清账本或强行跳过检查。

剩余真实终端验收：用户从QQ/App/设备发起实际对话，验证跨会话记忆、自然时间变更、情感联动、删除后未知；同智能体设备共享、不同智能体隔离的实际终端效果需单独记录。此前自动化已覆盖这些机制，但本次未代用户发送消息、没有物理硬件结果，不能把容器健康/内部召回称作真实终端验收完成。
