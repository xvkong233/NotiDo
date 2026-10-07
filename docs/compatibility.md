# 知办 · NotiDo：外部兼容性基线

日期：2026-10-07。此表是阶段零工作底稿，未安装/运行 AstrBot 或滴答 CLI，未执行微信、QQ、滴答真实账号联调。unknown 表示尚未确认实际能力；not_run 表示尚未执行测试。所有版本锁定与生产就绪均待验证。

## 能力清单

| 能力 | 文档依据/候选 | 实现版本 | 实际能力 | 账号验证 | 生产就绪 | 必要证据 |
| --- | --- | --- | --- | --- | --- | --- |
| 微信官方私聊/稳定 ID | 原 PRD 引用 AstrBot weixin_oc | 未锁定 | unknown | not_run | false | 双向消息、重启、稳定标识、默认回复去重 |
| QQ 官方私聊/稳定 ID | QQ WebSocket 文档 | 未锁定 | unknown | not_run | false | 账号双向消息、授权身份、重启 |
| 两平台原文件 | 官方适配器候选 | 未锁定 | unknown | not_run | false | PDF/图像/其他原件、字节 hash、失效补发 |
| 延迟回执 | AstrBot send_message，平台资格待测 | 未锁定 | unknown | not_run | false | 30 秒归组与长文件处理、过期/重启 |
| Plugin Pages/API | Pages 官方文档 | 未锁定 | unknown | not_run | false | bridge、文件上传下载、认证/版本/去重 |
| 模型文字/视觉 | 可配置 AstrBot Provider | 未选定 | unknown | not_run | false | 严格输出、OCR、关键日期、失败分类 |
| 中国版 CLI 授权 | @suibiji/dida-cli 候选，沿用原调研 | 未锁定 | unknown | not_run | false | 帮助/机器输出、账号身份、认证根/重启 |
| 任务新增/查询/修改/完成 | 官方 CLI 候选 | 未锁定 | unknown | not_run | false | 准确 argv、JSON、完整性、完成回读 |
| 原生日期/全天/备注 | 具体映射未测试 | 未锁定 | unknown | not_run | false | 客户端实际字段、全天不漂移、备注保留 |
| 中国版原生附件 | 官方能力待核实，或受控 CLI 扩展 | 未锁定 | unknown | not_run | false | 中国版域名/授权、上传与登记、查询、下载 hash |
| 附件未知结果核查 | 尚无已验证查询契约 | 未锁定 | unknown | not_run | false | 超时、重复请求、可靠远端 ID/内容匹配 |
| 单次原生提醒 | 可选能力 | 未锁定 | unknown | not_run | false | 写/读提醒字段；不支持时按 PRD 追问降级 |
| 官网来源 | 契约计划，采集不在首版 | 未实现 | unsupported | not_run | disabled | 仅验 E01–E03 禁用与内存契约，无真实采集 |

## 每项测试记录

真实执行后逐项填写以下字段；凭据值、临时媒体令牌、真实通知及完整个人任务列表不得写入公开证据。

```text
capability:
implemented_version:
tested_at:
account_region: dida_cn
account_ref: <脱敏引用>
endpoint_domains:
auth_method: <类型，不含值>
stdout_schema:
field_mapping:
limits:
verification_evidence:
failure_classification:
restart_result:
acceptance_ids:
supported: unknown
readiness: false
next_step:
```

G0 入口与持久能力、G1 任务 CLI、G2 原生附件、G3 版本/恢复，门槛定义见 PRD 26.7。文档阅读不能把门槛标为 passed；核心能力 unsupported/unknown 时记录阻塞而非模拟通过。
