# 情感系统交接

## 需求与授权

用户已确认plan.md，并要求开始实施及持续记录。独立入口名称为「情感系统」。全部已列基础情绪、复杂情感、需要、性格和原则均可调。同一智能体所有绑定设备共用，不同智能体分开管理。

最新追加：前后端统一youmi/Youmi。不要部署已生成的旧Mia命名候选；该候选验证只证明原兼容方案可行，现需改为统一命名迁移。历史数据和旧凭据必须兼容。

## 仓库与接入位置

- /root/projects/Youmi，实施分支feature/emotional-system（从feature/astrbot-fusion分出）；保留前序AstrBot改动和YOUMI_ASTRBOT附件。
- server/companion现有affect/character/context/reply及AstrBot账本负责状态和提交；management.py与Manager控制器负责所有者授权。
- server/admin_web为前端源码补丁与组件，使用已校验上游重建，不能覆盖原有生产定制。
- 文档plan.md为当前确认方案；worklog.md记录实际验证，不推断通过。

## 当前进度

已完成初始实现：V9迁移、51维度、状态计算、管理API、App/设备和QQ对话接入、独立Vue2页面及入口。本地后端471、scripts98、CI辅助39项通过；Vue模板与脚本解析通过。工作流、完整浏览器构建与交互、Java测试和部署尚待验证，不能将本地机制测试称为真机通过。生产数据尚未变动。

新增源码：server/companion/emotional_system.py、schema-v9.sql/schema-v10.sql；前端YoumiEmotionSystem.vue和源码安装器emotional_system.py。重点复核旧字段兼容、跨设备版本控制和独立智能体权限。

本地源码候选：/tmp/emotional-admin-JN8Pnt/xiaozhi-esp32-server-87c6df77b0220ddbd8fc804f984f83f69f3225e5；已应用前端补丁。模板校验依赖在同目录validator。磁盘仅约0.5GiB余量，不宜盲目安装完整依赖或清理用户资源。

## 验证约束

使用/root/tmp/youmi-astrbot-venv执行实际测试。自动工作流按用户项目规则验证并给出精确提交证据，真机QQ/App另验。部署前核对当前镜像、旧命名运行源码与增量迁移；旧二进制不能直接接升级后的SQLite。

## 分支与线上验证

实现已在feature/emotional-system提交f918d703，CI修订880e2838。GitHub后端、管理基线、绑定授权、语义向量已通过，链接见worklog；前端仍在浏览器依赖安装步骤。最新运行代码的精确提交验证需在后续修订后补齐。

离线更新器install_emotional.py只对冻结基线匹配的源码候选操作；支持legacy名称。/tmp/emotional-admin-JN8Pnt/runtime/mia_companion为已导出的生产源码候选，已应用第一轮更新并用合成库验证；后续源码修改须重建候选，不对已安装候选重复应用。生产Manager实际类型MiaCompanionManagementController，JAR路径/app/xiaozhi-esp32-api.jar，发布只能替换已核对的该class并保留其他条目。

上述单class/旧命名发布是用户追加命名前的历史方案，现已暂停。新发布需要统一youmi_companion包和Youmi Java类型，并迁移原生入口引用，不能直接套用旧Mia候选。

当前命名改动：安装器、验证脚本、前端请求统一youmi；管理和私有授权API增加youmi主入口并保留旧别名；新增状态表通过V10迁移为youmi_affect_system系列，旧SQL迁移保持不可变。身份persona_id及既有授权载荷中的历史字段仍兼容，避免现有设备失效。命名调整后本地474后端和39 CI辅助测试通过。前端新工作流使用匹配Playwright1.51.1的官方容器，避免上轮Azure APT镜像长时间下载。

1b53fd4d的前端结果104通过/8失败，已定位并补总开关aria-label；Java191项中3个旧字段白名单断言失败，已加新旧youmi_scope一致性检查待重跑。不要将这两轮记为全部通过。

统一命名部署工具：normalize_runtime.py（9个原生文件严格preimage，生成新youmi_companion覆盖目录）；normalize_manager.py（生产自有4类型+ShiroConfig严格preimage，保留JAR其他条目及启动前缀）。/tmp/emotional-admin-JN8Pnt/native为只读导出的原生源码，youmi-overlay为首个统一命名候选；后续源码修改需生成新目录。旧Mia单class候选已作废，不部署。生产仍为astrbot-494f0bab语音和astrbot-e87596a6管理镜像。

3d922939后端40219250974、绑定授权40219251038、管理基线40219250968通过。前端40219250969进行中。最新UI补充legacy_enabled提示，测试域名/构建辅助变量亦统一youmi，后续提交仍需精确工作流验证。
