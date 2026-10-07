# 知办 · NotiDo：PRD 重写记录

日期：2026-10-07。版本：v1.5；替代 v1.4。当前需求基线为 [PRD.md](PRD.md)，原文与此前修订保存在本地 Git 历史。

本轮按用户明确要求重新定义兼容边界：NotiDo 只需兼容 AstrBot；下游连接、登录、协议和消息收发由 AstrBot 负责。文档完整重写，不沿用旧版逐渠道接入、能力矩阵和发布验收要求。

## 主要调整

| 旧版问题 | 新版边界 | 对应位置 |
| --- | --- | --- |
| 接入规格深入下游协议和账号 | 仅使用 AstrBot 公开事件、组件、材料及回复接口 | PRD 3 |
| 插件重复描述连接、登录和取件协议 | AstrBot 提供接入和材料能力，NotiDo 持久/读取真实交付内容 | PRD 3–4 |
| 身份/会话规则绑定具体渠道 | 基于框架真实 actor/session/message 标识授权与去重 | PRD 3.2–3.3、7 |
| 后台出现下游连接就绪矩阵 | 只显示 AstrBot bridge、业务依赖、DB/worker | PRD 10 |
| 阶段零要求逐渠道账号/协议联调 | G0 只验证稳定 AstrBot 插件公共契约 | PRD 13 |
| 材料/回执不可用需深入下游补偿 | 通用输入失败/回复失败处理，补材料或后台查看，不补写协议 | PRD 4、9 |
| 业务/技术规则多处重复覆盖 | 压缩为 14 节，集中定义契约与执行规则 | PRD 全文 |
| 旧文件名绑定早期接入方案 | 改为 docs/PRD.md 唯一当前基线，配套链接同步 | README |

业务要求保持：通知读取、身份判断、必做/自愿分流、真实日期与提交要求、原生附件、增查改完、明确延期、网页配置、部分成功、未知核查和重启恢复。

原有动作身份/不可变版本、账号作用域、操作去重、原件 hash、队列边界、条件 claim、执行前权限校验和 outbox 设计仍保留，但按 AstrBot 抽象统一表达。没有因去掉下游兼容要求而降低业务真实性。

## 验收与外部门槛

验收重构为 55 项：任务/框架 A18，通知 N14，附件 B08，禁用来源 E03，技术 T12。它们不是旧 83 项的顺序子集，编号含义已重新定义；实现与测试须使用 v1.5 基线，不能把旧编号结果自动搬过来。当前全部 not_run。

[AstrBot 事件](https://docs.astrbot.app/dev/star/guides/listen-message-event.html)、[回复](https://docs.astrbot.app/dev/star/guides/send-message.html)、[Pages](https://docs.astrbot.app/dev/star/guides/plugin-pages.html)及[Provider](https://docs.astrbot.app/dev/star/guides/ai.html)作为框架依据，具体稳定发行版和实现仍待锁定。没有运行框架或真实滴答联调。

尚需验证：AstrBot 插件公共契约、滴答任务 CLI 的机器输出/原生字段、中国版原生附件上传/查询/下载核验以及构建恢复。中国版原生附件仍是完整首版的发布门槛；不再把任何下游渠道的独立兼容验证列为 NotiDo 的责任。
