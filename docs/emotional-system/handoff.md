# 情感系统交接

## 需求与授权

用户已确认plan.md，并要求开始实施及持续记录。独立入口名称为「情感系统」。全部已列基础情绪、复杂情感、需要、性格和原则均可调。同一智能体所有绑定设备共用，不同智能体分开管理。

## 仓库与接入位置

- /root/projects/Youmi，分支feature/astrbot-fusion；保留前序AstrBot改动和YOUMI_ASTRBOT附件。
- server/companion现有affect/character/context/reply及AstrBot账本负责状态和提交；management.py与Manager控制器负责所有者授权。
- server/admin_web为前端源码补丁与组件，使用已校验上游重建，不能覆盖原有生产定制。
- 文档plan.md为当前确认方案；worklog.md记录实际验证，不推断通过。

## 当前进度

已完成初始实现：V9迁移、51维度、状态计算、管理API、App/设备和QQ对话接入、独立Vue2页面及入口。本地后端471、scripts98、CI辅助39项通过；Vue模板与脚本解析通过。工作流、完整浏览器构建与交互、Java测试和部署尚待验证，不能将本地机制测试称为真机通过。生产数据尚未变动。

新增源码：server/companion/emotional_system.py、schema-v9.sql；前端EmotionSystem.vue和源码安装器emotional_system.py。重点复核旧字段兼容、跨设备版本控制和独立智能体权限。

本地源码候选：/tmp/emotional-admin-JN8Pnt/xiaozhi-esp32-server-87c6df77b0220ddbd8fc804f984f83f69f3225e5；已应用前端补丁。模板校验依赖在同目录validator。磁盘仅约0.5GiB余量，不宜盲目安装完整依赖或清理用户资源。

## 验证约束

使用/root/tmp/youmi-astrbot-venv执行实际测试。自动工作流按用户项目规则验证并给出精确提交证据，真机QQ/App另验。部署前核对当前镜像、旧命名运行源码与增量迁移；旧二进制不能直接接升级后的SQLite。
