# 情感系统交接

后续更新：2026-10-25已部署49090fbe人设编辑改造，并按用户原文启用苏晓筱；现用镜像改为youmi-voice:persona-49090fbe、youmi-manager:persona-49090fbe。最新验证、原文指纹、发布路径和后续编辑说明见[人设设定改造记录](../persona-settings.md)。下文27601ab5为上一轮情感系统发布记录。

截至2026-10-25 18:27 UTC：实现、精确提交CI与工作环境部署已完成；QQ/App/实体设备的真实对话及用户登录后保存参数仍需终端验收。

## 已确认需求

独立管理入口只叫「情感系统」。前后端运行包、类型、接口、新配置和新存储统一youmi/Youmi。51项维度完整覆盖基础情绪、复杂情感、六种需要、三项性格和六项调节原则，均可调。每个智能体独立；同一智能体的设备、App、QQ共用。已有身份、凭据和存储按兼容契约保留，不能盲目全局替换历史mia字段。

## 仓库状态

- 工作区/root/projects/Youmi，实施分支feature/emotional-system，从feature/astrbot-fusion分出。
- 已部署运行源码提交27601ab5f5dfed6dddd0f2d1d7bbc7b39571b901；后续文档提交不改变该运行源码。
- integrations/astrbot_plugin_youmi下README.md、_conf_schema.json、main.py及integrations/tests/test_astrbot_compatibility.py这4个既有未提交修改，以及YOUMI_ASTRBOT附件，都保留且不属于本次提交。
- plan.md为确认方案，worklog.md记录过程及失败，deployment.md记录精确工作流与实际发布。不要把早期旧命名候选当成当前发布物。

## 关键实现

- server/companion/emotional_system.py：51维度、固定/动态/临时锁定、恢复、限幅、版本冲突和幂等回合提交、90天/10000条变化历史。
- schema-v9.sql保持不可变；schema-v10.sql将新情感表迁移为youmi_affect_system系列。历史mia表/身份字段保持原值。
- affect/context/reply与astrbot_api复用状态计算，在完整成功回合后提交；旧持续情绪在新页首次保存前继续沿用。
- Manager的affect-system和youmi-settings复用登录与所有者核验。运行包youmi_companion；Java自有类型Youmi命名；旧管理/授权路径继续兼容。
- server/admin_web/YoumiEmotionSystem.vue及emotional_system.py提供页面和入口，支持智能体切换、全部配置、当前值调整、确认重置、历史、草稿及移动布局。

## 验证结果

27601ab5精确提交：后端477项、Java191项与JAR、前端112项及构建、管理基线、真实向量/检索/上下文/回复/提取均通过。仓库检查98+10+39+16项、S3两板/C3/P4四个代表固件构建通过。运行链接见deployment.md。

保留两项过程记录：上一轮Firefox旧情绪刷新偶发未发请求，当前相同前端完整112项通过，未声称已经修复偶发问题；真实向量首轮1次触发原有embedding_busy上限，未放宽阈值，原条件复跑全流程通过。后续若复现，应按独立问题定位，不能隐藏或直接放宽断言。

## 当前工作环境

- 发布目录/opt/xiaozhi-server/youmi-emotional-system-27601ab5。
- 语音镜像youmi-voice:emotional-27601ab5，ID sha256:f25c380c66b001153d88c91d54375b0d29fbf6a19aedd5a829e07c6bb8dacf11。
- 管理镜像youmi-manager:emotional-27601ab5，ID sha256:4e40ac28c99dc74d97e43be9bdf8d8dbefc53bde40da763b3e496cc3794a6758。
- 只重建语音与管理服务；现有7份Compose继续保留并追加compose.youmi.json。新环境变量采用YOUMI前缀，数据/证书目录保持历史路径，QQ侧车未重建。
- 实际只读检查通过：SQLite V10/完整性ok、Worker可用、实际已授权智能体管理入口返回51维度、未登录请求被拒绝、index与CI一致及入口JS/CSS可用。两个容器重启次数0。
- 未改变智能体参数，未导出生产数据库/密钥，未新增生产备份。旧生产镜像保留。V10后不能直接使用仅支持V8的旧语音二进制，恢复须用V10兼容运行包和Youmi授权路由。

## 部署工具和继续工作

normalize_runtime.py严格核对9个原生源码preimage并生成新youmi_companion覆盖目录；normalize_manager.py严格核对替换类型，保留其余JAR内容，只转换明确的自有类型引用。最终5个替换class与27601ab5 CI JAR逐字节相同；451个前端文件核对清单。

本地临时目录/tmp/emotional-admin-JN8Pnt保存离线候选和合成检查，不包含生产数据库或配置密钥。早期runtime/mia_companion、manager旧命名候选已作废，不部署。可用测试环境为/root/tmp/youmi-astrbot-venv；本地磁盘余量有限，勿清理用户文件来腾空间。

下一步是用户登录后台→情感系统→选择智能体→保存配置，再用QQ、App及实体设备对话观察共享状态和表达。不能把fixture浏览器检查、编译或容器健康称为真机验收；不要未经选择替所有智能体启用系统或覆盖参数。
