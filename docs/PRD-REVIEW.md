# 知办 · NotiDo：PRD 审阅记录

审阅日期：2026-10-07。基线：原 PRD v1.3；修订：v1.4。原文通过 Git 首次提交保留，当前 PRD 文件名保持不变。

结论：原文的业务范围、原文证据、真实日期、原件上传和未知副作用处理已比较完整；主要缺口集中在版本/数据关系与恢复边界。v1.4 修订这些契约后可以作为开发规格；是否能交付完整首版仍取决于中国版滴答原生附件等阶段零验证。本次没有实现产品或执行真实账号测试。

## 本轮修订

| 发现 | 实现后果 | v1.4 修订 | 位置 / 验收 |
| --- | --- | --- | --- |
| 产品与插件、部署名称未统一 | 仓库、安装目录和数据根难以一致维护 | 知办 · NotiDo，插件 astrbot_plugin_notido，服务/镜像 notido | 6、20.1、24 / T24 |
| action_items 同时承担稳定身份与单个通知版本 | 延期时旧证据可能覆盖，或生成新动作 | 稳定 action_items + 不可变 action_item_versions | 19.3、20.2 / T15 |
| create 键含 notice_revision，update 键不足以区分直接多次修改 | 修订通知可能重建，多次修改可能被误去重 | 新增按动作身份；修改/完成按固化 plan_id；全部含账号 | 11.1、22.4 / T15 |
| 多数远端关联缺 account_ref | 重新授权其他账号时 ID/附件/编号可能串用 | account_scopes；凭据 generation；切换暂停、重选清单 | 20.2、22.5、26.2 / T16 |
| jobs 未包含媒体取件/读取 | 重启后可能永久遗漏原件，闭环无法恢复 | media_acquisitions、pending asset、acquire_media/read_materials 作业 | 20.2–20.3、26.3 / T17 |
| 100 作业上限同时要求无限保留排队 | 不清楚拒绝边界，队列仍可能无限增长 | 活动作业 100、未准入 inbox 1000、组字节与磁盘预留 | 17.2、23.1、26.3 / T18 |
| 5 图片与 30 页 PDF 预算冲突 | 扫描 PDF 第 6 页可能被静默遗漏 | 截图、PDF 渲染页、DOCX 嵌图及组总量分层 | 19.4、23.1、26.4 / T19 |
| 固化后未定义排队/追问造成的过期日期 | 确认后可能写入已经过期的期限 | 每次启动新写入前检查；确认是否补记逾期，保留原锚点 | 22.7 / T20 |
| 部分 POST 缺 request_id/revision | 授权、补文件、回答等可能重复或覆盖 | 全部变更接口统一去重与条件版本；敏感指纹保护 | 21.4 / T21 |
| reconciled 作为终态但未说明核查结果 | 核查完成可能被当成功，附件依赖不触发 | 核查是动作；仅有证据才 succeeded/failed_safe；其余保持未知 | 11.2、22.7 / T22 |
| 修改/完成回读失败没有合适状态 | 被错误记成 created_unverified 或伪成功 | 新增 applied_unverified，区分明确成功回读失败与超时未知 | 11.2、20.3、21.5 / T22 |
| 查询全量、全天逾期、翻页重排未精确定义 | 部分数据伪全量；全天漂移；页项漏失 | per-project 完整性、local_date 语义、稳定排序、QUERY_CHANGED | 26.5 / T23 |
| 不可读节点不在合法 kind；意图全可空示例与严格 Schema 冲突 | 正常失败节点也可能无法入库，模型字段边界不明 | material_unavailable 合法分支；schema v4 联合意图契约 | 7、19.1、19.6 / T24 |
| 取消与 claim 竞争、各 TTL 及软预算不够统一 | 已执行操作可能显示取消，等待/超时语义混淆 | 仅未开始可 cancelled；按状态条件竞争；统一 TTL 与预算 | 22.7、26.6 / T25 |
| 固化计划“配置不变”容易覆盖权限撤销/身份变化 | 撤权后仍执行旧计划 | 授权、清单、身份、账号执行前重新核对，计划内容不就地改写 | 22.5 / T26 |

新增 T15–T26 共 12 项，原 57 项业务验收与 T01–T14 保留，总计 83 项。所有验收目前为 not_run，文档修订不算产品测试通过。

## 外部资料与剩余门槛

复核了 [AstrBot Pages 文档](https://docs.astrbot.app/dev/star/guides/plugin-pages.html)中的页面、bridge 与后端 API 边界，及 [QQ WebSocket 文档](https://docs.astrbot.app/platform/qqofficial/websockets.html)的消息类型与接入说明。它们提供文档依据，未证明本项目选定版本及真实账号可用。

[AstrBot pyproject](https://github.com/AstrBotDevs/AstrBot/blob/master/pyproject.toml)提供 Python 版本要求依据；[Node.js 版本表](https://nodejs.org/en/about/previous-releases)支持 Node.js 24 LTS 路线。阶段零仍须选择稳定发行版本并锁定完整构建，不能使用 master beta 作为生产基线。

[社区附件上传源码](https://github.com/liuboacean/ticktick-cli/blob/main/ticktick/commands/attach.py)仍显示国际版上传域名及会话 Cookie。这不足以验证中国版滴答原生附件链路，G2 仍是整版发布阻塞。

[滴答官方 CLI 帮助](https://help.dida365.com/articles/7464976698707017728)和[附件帮助](https://help.dida365.com/articles/6950670128287580160)本轮未读取到正文；PRD 的相关具体命令/口令说明沿用原调研，未作独立确认。既没有运行官方包，也没有验证凭据持久、字段映射、附件上传或失败分类。

其余参考项目、历史源码提交与候选学校网址未逐项复核，不将它们标为新增已验证依据。官网采集不在首版，当前 PRD 审阅也未尝试抓取学校网站。

## 开发判断

优先完成 G0/G1/G2 的最小证据，避免先建设完整页面再发现核心附件链路不可实现。可并行推进无账号依赖的领域/数据库/读取规则模块；这里的“并行”是工作包依赖说明，不要求启动多个开发代理。

当前最重要的下一项验证是：中国版滴答真实测试清单中，通过受控 CLI 完成原文件上传、可靠查询、客户端下载及 hash 核验，并记录额外授权与超时恢复。失败则留下具体证据和方案，整版保持未就绪。
