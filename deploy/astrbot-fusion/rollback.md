# 回滚（未在生产演练）

当前voice为 `mia-xiaozhi-server:astrbot-ad302096`，前一V8镜像 `mia-xiaozhi-server:astrbot-e87596a6` 保留，但含已确认的旧字段故障；回退优先保持V8-aware镜像并关闭bridge。后者可以止损，不能宣称恢复QQ回复。用户取消本次新增备份，未创建备份或执行回滚。

2026-10-25 已实际升级到 V8，且用户明确取消本次新增备份。当前可用的 V8-aware 镜像为 `mia-xiaozhi-server:astrbot-e87596a6`；回退时保留它并移除 bridge 环境入口，再按原六份 Compose 加最后 override 只重建对应服务。Manager 原镜像 `mia-xiaozhi-web:ota-discovery-c1f03ce` 已保留。不要把旧 V7 语音镜像直接接回现有数据卷。临时手机桌面另按[生产记录](../../docs/integrations/astrbot-production-deployment.md)先清理，不能留下临时公网入口。

1. 停止新增 sidecar：`docker compose --env-file deploy/astrbot-fusion/.env -f deploy/astrbot-fusion/compose.yaml stop astrbot snowluma`。保留 AstrBot data、Meme 资源及 SnowLuma 三个卷，不 down -v，不注销/删除 QQ 登录数据。
2. 在现有 manager 登录账号撤销新虚拟 device grant；不要撤销 iOS/实体硬件授权。只移除新服务凭据和新配置入口，保留原角色、音色、模型与账户。
3. 后端移除 `YOUMI_ASTRBOT_CONFIG_FILE` 即关闭 bridge 路由。使用预先构建的 **V8-aware、bridge disabled** 回退镜像，继续保留 V8 schema loader 和新表；原 V7 二进制的 checksum 检查会拒绝 V8 数据库。不能把旧镜像直接指向已迁移的新卷。
4. 管理 JAR 可用原 JAR 回退，虚拟 ai_device 记录/授权表无需 DROP；先撤销，原设备和用户记录不变。不要以全库备份覆盖当前 MySQL。
5. 正常回滚不需要恢复旧 SQLite backup。若数据库损坏需恢复，先停止写入并保留故障库+WAL，在隔离副本核对自备份以来新增事实、纠错、删除和设置；明确协调数据损失，再由授权操作员恢复。不要将 schema 降级脚本或 DROP 新表当常规回滚。
6. 复核原 App/硬件连接、现有 manager、角色、事实/向量/情绪指纹、生产音色；T01–T30 中 QQ 项仍按实际状态标记。回滚成功不等于硬件已验收。

发送在 claim 后进程崩溃属于 delivery uncertain；不重放 QQ 消息或有副作用工具。已提交 native JobStore 的 job 由原 worker 持续处理，complete 重试只补账本，不重复抽取任务。
