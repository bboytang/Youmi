# 情感系统发布与验证

## 发布对象

运行源码提交：`27601ab5f5dfed6dddd0f2d1d7bbc7b39571b901`。

发布目录：`/opt/xiaozhi-server/youmi-emotional-system-27601ab5`。候选镜像：`youmi-voice:emotional-27601ab5`、`youmi-manager:emotional-27601ab5`。

语音候选继承当前生产镜像，只覆盖已核对的9个原生入口和youmi_companion包。管理候选继承当前生产镜像，保留其他定制JAR条目和旧hash静态资源，覆盖经校验的类型、引用以及新前端。历史存储、证书目录和凭据保持原路径；新环境变量采用YOUMI前缀。

## 精确提交验证

- 后端477项：[40220154536](https://github.com/bboytang/Youmi/actions/runs/40220154536)。
- Java191项、绑定与私有授权及JAR构建：[40220154540](https://github.com/bboytang/Youmi/actions/runs/40220154540)。候选5个替换class与该轮JAR逐字节一致。
- 前端构建、原有回归及四浏览器112项：[40220166587](https://github.com/bboytang/Youmi/actions/runs/40220166587)。使用此轮制品12609423206，ZIP SHA256为`bcf2c149067eb9a48411a3e1a8ae284d2c57bfe4e60d8d040356e89868e60181`；451个前端文件逐项对照制品清单。
- 管理基线：[40220154541](https://github.com/bboytang/Youmi/actions/runs/40220154541)。
- 仓库检查（98构建脚本、10构建服务、39 CI辅助、16视频）及S3两种板型、C3、P4代表固件构建：[40220248444](https://github.com/bboytang/Youmi/actions/runs/40220248444)。
- 向量、检索、上下文及回复检查：[40220154544](https://github.com/bboytang/Youmi/actions/runs/40220154544)。首轮真实向量并发18次中1次触发既有embedding_busy上限；未改实现或阈值，按原条件复跑后全流程通过。

## 操作与恢复约束

`prepare_release.py`生成追加Compose覆盖，保留当前7份配置、项目名、卷、证书、端口和侧车设置。`compose-command.json`保存不含凭据的命令参数；`compose.youmi.json`权限0600，只覆盖两个服务镜像及命名相关环境。

切换前再次核对现网原生源码preimage、生产JAR与父镜像一致、候选源码及JAR清单、前端提交和所有相关工作流。只重建语音和管理两个服务，启动后检查实际镜像、SQLite完整性/V10、Worker、已授权智能体只读管理与静态资源。

V10是增量迁移，历史情绪、记忆及身份字段继续保留。迁移后不能直接启动仅理解V8的旧语音二进制；恢复必须使用理解V10且兼容Youmi授权路由的运行包。不要降级数据库或删除新表来恢复页面。保留原镜像，遵循用户不新增生产备份的要求，不导出生产数据库和密钥。

## 仍需实际终端验收

浏览器交互测试使用合成服务端数据；线上只读管理检查不替代用户登录后保存配置。QQ真实消息、App与实体设备上的情绪表达、跨设备连续对话和语音中断，需要实际终端验收。新页首次保存前沿用已有持续情绪；不会自动替用户选择或覆盖51项配置。

## 实际发布结果

2026-10-25 18:25 UTC切换两个服务，18:26 UTC完成检查。首次只读检查发生在语音服务等待管理API启动期间，数据库尚为V8；服务完成启动后再次检查为V10，两个服务均未重启循环。

- 语音镜像运行名称为youmi-voice:emotional-27601ab5；管理镜像为youmi-manager:emotional-27601ab5。
- SQLite quick_check为ok，schema为10，AstrBot内部健康接口返回worker_available=true。
- 使用容器内既有服务认证，通过/youmi/management对实际已授权智能体执行只读status，Manager前后复核成功，目录/配置/当前值均51项。未修改任何智能体配置，不输出owner或密钥。
- Nginx提供的index.html与通过CI的制品完全相同，两个入口JS/CSS可访问，未登录的affect-system和youmi-settings请求被拒绝。
- 保留原生产镜像和原始配置文件，没有新增生产数据库/密钥备份。只重建语音与管理服务，QQ侧车未重建。
