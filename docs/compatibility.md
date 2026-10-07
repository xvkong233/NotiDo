# 知办 · NotiDo：兼容性与业务依赖基线

版本：v1.5｜日期：2026-10-07。兼容对象为 AstrBot 的公开插件接口；不创建下游渠道矩阵或要求逐渠道账号联调。滴答 CLI、Provider 和文档读取组件是 NotiDo 自身业务依赖，仍需验证。

当前未实现插件、未锁定构建、未运行真实测试。unknown 表示实际能力未确定；not_run 表示尚未执行验证；文档依据不等于生产就绪。

| 门槛/能力 | 候选或依据 | 实现版本 | 实际能力 | 验证 | 就绪 | 必要证据 |
| --- | --- | --- | --- | --- | --- | --- |
| G0 插件生命周期 | AstrBot 稳定发行版 | 未锁定 | unknown | not_run | false | 安装、加载、传播控制、卸载、重载与资源释放 |
| G0 消息/身份/会话 | AstrMessageEvent 公共接口 | 未锁定 | unknown | not_run | false | 稳定标识、授权、去重及代表性事件夹具 |
| G0 材料交付 | AstrBot 消息组件与材料能力 | 未锁定 | unknown | not_run | false | 已交付文本/图片/文件、不可用组件、真实原字节持久 |
| G0 回复 | 框架原会话回复接口 | 未锁定 | unknown | not_run | false | 成功、失败、未知、补回执不重做业务 |
| G0 Provider/Pages | 框架公开 Provider 与 Pages bridge | 未锁定 | unknown | not_run | false | 调用、超时、后台鉴权、文件上传下载 |
| G1 中国版 CLI 授权 | @suibiji/dida-cli 优先 | 未锁定 | unknown | not_run | false | help、账号作用域、认证路径/重启复用 |
| G1 任务与原生字段 | 受控任务 CLI | 未锁定 | unknown | not_run | false | argv/JSON、清单/任务读取、增改完、日期/全天/备注 |
| G2 原生附件 | 已验证 CLI 或受控扩展 | 未锁定 | unknown | not_run | false | 中国版域名/授权、上传登记/查询、客户端可见下载/hash |
| G2 附件未知核查 | 可靠 ID/查询/内容匹配契约 | 未锁定 | unknown | not_run | false | 超时/重启/重复输入，无盲目上传 |
| G3 读取/构建/恢复 | 锁定组件、SQLite、Docker | 未锁定 | unknown | not_run | false | PDF/DOCX/OCR范围、依赖锁、数据根、迁移备份与故障 |
| 可选提醒 | 实测 CLI 原生提醒 | 未锁定 | unknown | not_run | false | 写/回读；不支持时按 PRD 询问只记待办 |
| 禁用官网 | SourceAdapter 与 DisabledWebsiteSource 计划 | 未实现 | unsupported | not_run | disabled | E01–E03，仅禁用和内存转换，无实际采集 |

代表性 AstrBot 事件用框架公共事件/组件契约构造，不为每个下游重新建设测试和授权体系。实际材料缺失仍以通用 MATERIAL_UNAVAILABLE 处理，不能把占位组件当完整输入。

## 证据记录字段

执行后逐能力填写 implemented_version、tested_at、接口/依赖版本、stdout_schema 或框架契约、field_mapping、limits、verification_evidence、failure_classification、restart_result、acceptance_ids、supported 和 readiness。滴答项目另记录 account_region、脱敏 account_ref、endpoint_domains、auth_method 类型；不记录凭据值。

真实原件和个人通知不默认进公开证据；使用脱敏样本和必要 hash/结果。文档阅读、框架夹具或 mock Gateway 不算 G1/G2 的真实滴答验证。发布规则见 [PRD 13](PRD.md#13-阶段零工作包与完成定义)。
