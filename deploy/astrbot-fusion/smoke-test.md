# 授权后的真机冒烟记录

执行前记录 Git SHA、三个固定源码 SHA/镜像 digest、实际配置 revision 与 trace_id；仅保留脱敏截图/组件类型/时间，不导出完整私聊、音频或密钥。

1. health 验证无 token 为 401，有 token 返回当前 worker。撤销虚拟 grant 后 prepare/complete 应为 403；其他 App 的 grant 仍有效。
2. 文字连续十轮、web search 两次工具续答、MCP 成功/异常/拒绝权限，检查 OneBot 实际出站消息和唯一完成账本。
3. Meme 原生分类/辅助模型/语义模式分别测试纯图、单 GIF、图文、多图；不得见控制标签，不得丢图或重复回复。未导入图片不能把“没报错”记为通过。
4. 手机发图片/GIF和语音，核验视觉/STT来源；手机播放 Youmi voice，确认当前生产音色/语速；图文+语音只完成一次。模拟 WAV serializer 结果不替代此项。
5. QQ 保存真实新偏好，等原 job 进入 committed，再在原 App 用不同措辞检索；反向操作。删除/纠正/关闭检索后两端均不得继续引用旧事实。
6. 情绪冲突→道歉→恢复，重启看同一持久状态；不向生产写模拟情绪来制造证明。成长/事件只能用真实足够证据，删证据后失效。
7. 重复同一 message_id、prepare 后删记忆、complete 网络中断、重启两个 sidecar、QQ 断线/登录失效。核对卷和配置保留；不确定投递不能自动重发。
8. 原生 cron 触发一次主动聊天，核对人格与静默/限频规则，不产生用户 memory job。分别记录独立命令插件和自调 LLM 插件行为及 Meme scope。

逐项填 [T01–T30](../../docs/integrations/astrbot-acceptance.md)。尚无该真机执行证据，默认全部标记 `NOT VERIFIED ON DEVICE`。原生实时电话不在已支持列表。
