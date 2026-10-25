# 情感系统工作记录

## 2026-10-25 开始实施

- 用户确认完整方案，并要求方案、工作记录与交接落盘。
- 分支：feature/astrbot-fusion。已有AstrBot插件与测试4个文件改动及YOUMI_ASTRBOT附件，属于前序工作，保留。
- 已核对管理授权：owner包含user及agent，适用于同智能体跨设备共享、不同智能体隔离。
- 现有SQLite迁移至V8；性格和情绪各按owner存储。新功能需要增量迁移、兼容API和统一对话状态。
- 管理前端采用已校验上游源码补丁（Vue2/Element UI），不得直接修改生产压缩JS。
- 已找到可用测试环境/root/tmp/youmi-astrbot-venv，不沿用此前“无pytest”的未检查结论。
- 本轮尚未部署；后续测试、提交和部署结果按实际追加。

## 第一轮实现与本地验证

- 新增V9增量表、51维度目录、独立配置/状态/历史、固定/动态/临时锁定、限幅和恢复。继续使用已验证owner隔离。
- Manager新增智能体级affect-system接口；语音管理服务前后核验所有者。App/设备和AstrBot的评价、临时上下文与成功提交均接入通用模块。
- 新增独立Vue2管理页、导航和卡片入口，支持智能体切换、草稿、全维度配置、当前值调整、确认重置和历史分页。
- 旧开关、角色及重置入口与新系统兼容，旧系统未配置时保留原行为。原控制台登录、记忆和服务认证继续复用。
- 首轮全量测试发现旧迁移夹具未移除V9表、版本断言仍为8；已修正。另一次修改产生的测试缩进错误已被全量测试发现并修正。
- 对话集成测试发现全量状态注入超出8192预算，改为紧凑格式后通过，未放宽预算。
- 本地后端471项通过；构建脚本98项通过；CI辅助39项通过。98项首次运行因PATH缺少python失败，显式使用已有虚拟环境PATH后全部通过。
- Vue模板和脚本解析通过；完整浏览器测试已补充，尚待工作流执行。源安装器成功接入新页面；当前本地磁盘余量约0.5GiB，尚未在本机安装完整前端构建依赖。
- /root/projects/Youmi/:memory:为本轮检查产生的空测试文件，删除命令被自动审批拒绝后已移至/tmp/emotional-admin-JN8Pnt/unintended-check.sqlite3；用户数据未删除。
- GitHub服务端工作流补充既有真实ASR/TTS及旧Context夹具下载，避免全量测试因缺环境变量失败。
- 生产未变更。完整浏览器构建、Java权限测试、精确提交工作流与生产候选审计尚待完成。

## 独立分支和GitHub工作流

- 本次实现独立分支feature/emotional-system，首个实现提交f918d703；前序AstrBot四个未提交文件与用户附件未提交、未覆盖。
- 首轮后端工作流因浅克隆无法读取冻结V7 schema失败；880e2838修正fetch-depth后，https://github.com/bboytang/Youmi/actions/runs/40216809937 全量471项通过。
- f918d703管理基线 https://github.com/bboytang/Youmi/actions/runs/40216627862 与绑定授权 https://github.com/bboytang/Youmi/actions/runs/40216627840 通过；语义向量 https://github.com/bboytang/Youmi/actions/runs/40216627842 通过。
- 前端工作流 https://github.com/bboytang/Youmi/actions/runs/40216627844 已完成源码应用、原有前端回归及构建，浏览器依赖安装仍在进行；不记为浏览器通过。
- 新增V8→V9失败回滚测试、旧摘要与新状态一致性测试、严格源码preimage的离线更新器。专项目前12项通过。
- 只读核对现网Python9模块：与冻结cb6c1388源码经已审计Mia名称转换后匹配（角色常量YOUMI_V1保留）。导出仅源代码到临时候选目录，未导出密钥、配置或生产数据库。
- 旧命名候选已在合成SQLite通过V9迁移、51维度及跨智能体隔离，尚未替换现网。
