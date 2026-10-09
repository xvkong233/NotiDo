# 知办 · NotiDo：验收清单

0.1.1 起仅以 AstrBot 插件交付。下表的历史验收证据保留原脚本名称及环境；旧部署和容器联调脚本可在 [v0.1.0 源码](https://github.com/xvkong233/NotiDo/tree/v0.1.0)查阅，不作为当前安装依赖。

[同组读取预算](acceptance/native-read-budget.md)已按用户确认的口径实现并安装到唯一验收实例：288项本地回归、8项真实框架契约、5项生命周期、6项实际文件解码、10项后台检查和30条未登录路由拒绝通过，本轮零大模型调用。[完整57项核对](acceptance/current-requirements.md)全部pass。此前V14冻结50条、复杂材料V5 9项、性能V5 30／10样本及完整228条历史恢复的真实证据保留原版本；本轮仅对材料读取、缓存保留、迁移及新增设置执行针对性复验，未冒称重新调用模型或重新计量既有性能。

当前基线：[PRD v1.7](PRD.md)，进度更新2026-10-09。A20 + N14 + B08 + E03 + T12，共57项。实现版本0.1.0；当前完整回归288项通过，真实新增接口、同名原件、跨会话补件与超时核查证据见 IMPLEMENTATION.md。完整57项开发验收已通过，最终预算改动无模型复验范围见文首。

历史查询名称修正版：[部分附件失败与仅补失败](acceptance/native-partial-provider.md)、[119条历史/131组结论恢复](acceptance/native-outcome-restore-v7-r2.md)、[配置变更保护](acceptance/native-scope-change.md)、8项真实框架契约与5项生命周期通过。[未知回执只读复核](acceptance/native-unknown-receipt-recheck.md)名称和未知口径正确，原退出失败仍保留。[V8冻结50条](acceptance/native-corpus-v8.md)28/28行动、21/21日期、零额外/不确定误写，语义46通过/4失败，涉及多余确认和跨会话复用后的部分状态遗漏。当时编号核对51项pass、2项fail、4项not_run；该历史批次没有通过完整发布，当前结果见文首。

本版编号重新定义，不能沿用旧版相同编号的结果。验收只覆盖 AstrBot 公共插件契约和 NotiDo 自身业务，不要求逐下游渠道联调。滴答核心字段、原生附件及 deepseek/deepseek-flash 正文/清晰图文已用中国版专用清单实测；其余故障和视觉组合按各自独立报告的范围核验。

pass 只对应右列所述边界和证据；blocked 为外部联调条件未具备，not_run 为尚需补齐的实现或测试。已通过的 Provider 样本不表示整版就绪。下方旧表初次核验日期为2026-10-07、插件0.1.0；当前逐项审核日期见文首及独立报告。AstrBot 4.28.2、任务 CLI 0.1.14，详见 compatibility.md。发布时不能保留 not_run，核心项目不能以 not_applicable 绕过。

## v1.7 原生架构验收

完整57项的当前完成审核见[全范围核对](acceptance/current-requirements.md)。下方批次叙述与历史表不是整版通过证据；每项仍按当前PRD的完整条件核查。

历史[V7完整语料](acceptance/native-corpus-v7.md)50条已全部采集并逐条复核：28项首次行动、21项日期全部匹配，零额外或不确定误写，语义50通过。N01原文命名、直接缺条件结论登记、D10自然二次确认后的原/新组结论关闭均通过，实际API持久回读一致；N29零写入参数拒绝后的同键修正和解释性冗余保留在备注。该批次不替代复杂材料、性能或其他故障组合。

同一V7基线安装版本的[性能V4](acceptance/native-performance-v4.md)30条文字和10条文件全部正确，文字/文件p95为10.140/18.979秒；P14同一请求内的标题自修正保留原操作和完整计时。[真实后台核验](acceptance/native-pages-v7.md)9项通过，五组设置独立显示、无手工身份/Provider控件，实际通知和操作历史可见，页面变更为0。复杂材料与剩余故障组合继续单独验收。

[复杂材料V4](acceptance/native-complex-material-v4.md)9条字段/原件/语义全部通过，来源、未读范围及分项回执正确。[多次通知延期](acceptance/native-notice-v7.md)7轮原生对话：重复通知零写，两次延期同task/action版本1/2，原任务完成回读status=2，完成后新延期零写；外部编辑与来源冲突的实际组合仍需补齐。

[V5](acceptance/native-corpus-v5.md)50条及真实人工二次确认删除完成：28/28行动、21/21日期、零额外/不确定误写，完整语义复核保留2条非必要追问失败。[复杂材料V3](acceptance/native-complex-material-v3.md)9条字段/原件通过，语义8通过/1数量回执失败。[性能V3](acceptance/native-performance-v3.md)30/10全正确，p95为6.916/22.120秒；这些均绑定原安装版本，不能替代后续状态/命名修正的验收。

用户已确认原生结论登记方案；11个工具与迁移009、状态/实际材料和挂件保护进入源码，259项完整回归与8项真实框架契约通过。[V6](acceptance/native-corpus-v6.md)50条完整采集，28/28行动、21/21日期、零额外/不确定误写；6项失败为文件命名1项、未准入组的结论登记4项、删除原预览组未关闭1项。源码已修正并返回关联原组引用；不能将其计入原批次通过。[未完成结论恢复](acceptance/native-outcome-restore.md)34条实际历史、50组结论、21个恢复hold通过；零模型/远端写入。3秒反馈已对齐复用AstrBot原生接收／处理中提示，见[反馈核对](acceptance/native-feedback.md)；180秒读取边界仍待对齐。整版57项仍未完成，最新版性能已另批V4通过，目标保持完整范围。

[日期转换](acceptance/native-date-transition.md)8项真实检查通过，5次成功操作始终作用于同一任务，3次矛盾/缺时刻参数零写入；标题、备注和优先级保留。新增7项回归防止静默丢时刻。[原生进程中断](acceptance/native-process-kill.md)3个OS强制终止边界通过，未知保持未知，持久ID只读核验，恢复零写入；仍需AstrBot容器退出中的实际写入验收。

[V4](acceptance/native-corpus-v4.md)49条采集完成：28项行动/21项日期匹配，但未知原发布时间误写1项、通知来源缺失与多余追问仍未通过。当前240项完整回归、8项框架契约通过；DTO嵌套约束已在原生请求schema中暴露，V5重新采集中，不能以V4字段总分标整版通过。

复杂材料[V1](acceptance/native-complex-material-v1.md)/[V2](acceptance/native-complex-material-v2.md)分别逐条绑定实际输出hash。第6页期限与31页未读范围、未知编码、模糊时间、独立项与缺损附件、混合角色和四条共用/专属附件关系已真实采集；来源/回执失败仍保留。当前复杂材料16条操作的离线恢复与4组可视缓存重建通过，零写入/模型调用；当前新版性能与剩余57项仍须继续。

[空账本V3](acceptance/native-corpus-v3.md)49条已采集，28项行动首次创建、无额外或不确定误写；日期20/21。N26的24:00被降全天为真实失败，D16创建时间回执缺时区；D10因历史夹具同名先选择后预览，不计完整两轮通过。修正后233项完整回归通过，尚需当前Provider复核；失败不替换为调试成功，也不据此宣布整版通过。

后续[完整语料V2](acceptance/native-corpus-v2.md)已采集49条，本次行动/日期分别28/28、21/21匹配，新增20项、复用/关联查询8项，额外新增及不确定输入写尝试均为0。D19已在正式批次以真实暂停操作与已保存任务核验；D10等待本次新目标确认。逐条语义阅读发现来源、时间标签和多余追问问题，不能用字段匹配将准确率门槛标通过。已修正的代码须另批验证；旧V1失败与旧性能样本保留。

原生故障补充：test_native_failure_recovery 5项通过；live_native_partial_smoke 4项真实专用账号检查通过，涵盖成功项不重放、失败任务与原件分别恢复、实际下载hash与原生工具路径。失败是远端调用前受控注入，不代表远端真实限额耗尽，也不替代模型形成部分成功回执的验收。

用户纠偏后，旧 Provider 与事件接管结果不作为新架构发布证据。已新增 NativeTools 回归与 native_contract_smoke；真实 AstrBot 对话→前轮身份记忆→原生工具→滴答任务已通过；PNG/扫描 PDF/DOCX 嵌图的原生视觉和附件下载 hash，以及有来源的通知创建/去重/延期/完成均通过；包含确认删除的 26 条真实历史、三组图文原件的离线恢复已通过，零远端写入/模型调用。50 条参考答案已获用户认可并冻结，正式 V1 全部采集完成；precision 90.32%，三项不确定输入误写，**准确率准入未通过**，完整语义复核尚未完成（详见 [语料 V1 报告](acceptance/native-corpus-v1.md)）。D10 的正常二次确认删除已真实通过。独立 V2 性能 30/10 全部核验通过，文字/文件 p95 为 5.928/95.103 秒（详见 [性能报告](acceptance/native-performance.md)）；插件准入反馈、长文、其余故障组合仍需补齐，整版仍未完成。

| 编号 | 新增场景 | 状态 | 当前证据与边界 |
| --- | --- | --- | --- |
| A19 | 新建周期任务 | pass | native_recurrence_delete_smoke + native_recurrence_matrix：真实日/周/月/年、间隔、月底/闰日、次数/结束日、三种起算模式及精确首次时刻回读；矛盾规则本地阻断 |
| A20 | 二次确认删除 | pass | 真实预览不写，用户后续确认后单次删除，原生墓碑回读；过期/变化/同消息/非确认/网页重试/旧引用/崩溃/恢复/保留保护均有回归或真实证据 |

D19 用户已明确指 NotiDo 账本。新增原生本地取消工具与指令后，native_cancel_smoke 验证真实暂停操作 cancelled/attempt=0、零远端写、已保存任务保留；十项保护回归及完整 219 项通过。六条工具说明调试集亦已独立复查，但不替换正式 V1。工具说明/能力变更后的完整 50 条与性能仍须新批次验收；旧性能报告保留其 source hash 和环境边界。

## 历史 v1.5 记录

下表保留既有 CLI、账本、恢复和旧模型路径证据；AI、身份、追问、材料理解和默认会话项须按 v1.6 重新验证。

| 编号 | 场景 | 通过条件 | 状态 | 版本/日期/证据 |
| --- | --- | --- | --- | --- |
| A01 | 插件加载 | 锁定 AstrBot 稳定版安装/加载/卸载成功，不改核心、不要求下游适配 | pass | 实际容器安装/卸载/重载、锁释放与第二 owner 拒绝：container_lifecycle_smoke |
| A02 | 事件接管 | 授权会话只处理一次；默认聊天不重复回复/执行；框架管理指令保留 | pass | 实际公开事件与 handler：未授权不接管、管理指令保留、stop_event、重复消息只调用一次夹具模型：handler_contract_smoke |
| A03 | 框架身份授权 | 未绑定 actor/session 不调用模型或滴答；昵称和转发作者不能授权 | pass | 授权先于归一化；撤销 actor 不能借同会话其他绑定：test_service + 公共事件夹具 |
| A04 | 明确新增 | 真实任务标题、清单、指定日期正确，无多余确认 | pass | 用户指定真实 deepseek/deepseek-flash→实际 worker→CLI：明确标题/专用清单/2027-12-23 16:20/要求回读，attempt=1、无澄清：container_provider_smoke |
| A05 | 无日期新增 | 没日期就无日期，不造提醒；有但不明期限不能退化无日期 | pass | 无日期/未知期限政策；真实 CLI 清除 dueDate：test_service、live_update_smoke |
| A06 | 多项与部分成功 | 各项记账和回执准确，失败恢复不重复成功项 | pass | 领域故障夹具：两项新增一成功一 failed_safe，API 幂等重试只执行失败项，计划/成功 ID 保留，三阶段回执列清单/要求且不重放写入：test_partial_results。真实多项 Provider 样本仍属 A04 门槛 |
| A07 | 清单匹配 | 默认有效，未知/重名追问，不创建或悄悄换清单 | pass | 真实清单读取；唯一匹配/未知拒绝：test_domain、live_smoke |
| A08 | 日期边界 | 相对日期按固定锚点，跨午夜/周日/24:00/全天不漂移 | pass | 冻结锚点、周日/跨午夜/24:00/全天：test_domain |
| A09 | 过期期限 | 新写前发现已过去先问是否补记逾期，不重新解析相对日期 | pass | 解析及 claim 前过期阻塞；端点显式回答：test_service |
| A10 | 查询范围 | 实时读取，部分失败标范围/未知总数，逾期与无日期正确 | pass | 实时范围、失败清单与未知总数：test_service、query.py 领域场景 |
| A11 | 查询编号与分页 | 真 ID 绑定，过期/新页失效；快照改变刷新，不错选/漏页 | pass | 快照改变刷新、旧 selection 拒绝：test_service |
| A12 | 修改与完成 | 唯一目标回读，仅改指定字段，完成可核验，否则待核查 | pass | 真实改期、布尔 false、清除日期、完成 status=2：live_update_smoke；领域回读保护 |
| A13 | 草稿与取消 | 只补有效问题，独立请求不串草稿，取消不撤销已执行项 | pass | 独立明确请求完成不填旧问题；第二歧义草稿保留且不替换活动问题，过期续办生成新引用；取消仅尚未开始项并回显已写真实 ID：test_question_isolation/test_plan_recovery |
| A14 | 重复交付 | 同 AstrBot 消息键返回已有计划，不重复 create/upload | pass | 同消息/操作键并发 claim 不重复；框架 opaque ID：test_service、framework_contract_smoke |
| A15 | 框架回复失败 | 写结果保留，补回执不重复副作用，不假称送达 | pass | 发送失败保留账本；补回执只发送：test_service |
| A16 | 提醒与范围外 | 原生提醒按能力处理，周期/删除等明确拒绝不近似执行 | pass | 真实 Provider 删除/周期请求给范围外摘要且无操作；未验证提醒暂停且无操作：container_policy_smoke unsupported/reminder |
| A17 | 后台安全 | 未登录拒绝所有读取/变更/下载，纯文本展示且不泄露密钥 | pass | 实际 GET/全部 POST 未登录拒绝；下载 hash、五视图纯文本与移动端测试 |
| A18 | 部署与恢复 | 锁定构建可部署，数据/认证持久，备份恢复与退出有效 | not_run | 锁定构建、重载、备份 manifest/hash 通过；退出中写入与完整恢复验收待补 |
| N01 | 本人必做通知 | 无额外命令，生成有证据的本人任务、原生日期和要求 | pass | 真实截图/PDF/DOCX 嵌图新夹具自动提炼本人必做行动、精确日期/要求并挂原件；初次日期格式暂停的夹具续办通过。单页清晰范围：container_vision_smoke，复杂材料另验 |
| N02 | 知晓通知 | 摘要并说明无任务，不创建空事项 | pass | 真实图书馆闭馆通知完成摘要，操作数=0；领域 informational 过滤：container_policy_smoke information/test_service |
| N03 | 身份与角色 | 明确条件过滤；未知身份只追问受影响项 | not_run | 真实未知班长暂停无操作、其他学校明确不适用摘要无操作通过；同通知身份混合项组合仍需实测 |
| N04 | 自愿报名 | 未表达参与意愿不创建报名任务 | pass | 真实未决定参与的自愿报名暂停且无操作；专项参与同意与逐事项领域政策：container_policy_smoke optional/test_clarification_rounds |
| N05 | 原通知相对日期 | 原时间未知先问，不用转发接收时间代替 | pass | 转发原时间未知阻塞；公共 Node 原时间保留：test_service、framework_contract_smoke |
| N06 | 多时间节点 | 报名/提交/活动分别提炼，事件不冒称 deadline | not_run | 契约有 event/deadline；真实多时间节点提炼仍需补测 |
| N07 | 日期级与比较关系 | 原生全天，保留端点原文，不造 23:59 或提前工期 | pass | 真实全天回读；比较端点不减时刻、不能被模型省略：test_domain/test_service |
| N08 | 截图模糊 | 关键数字/日期不确定不写，保留实际图像证据 | not_run | 真实清晰截图 OCR/任务/原件通过；无效视觉格式暂停保护通过，实际模糊关键数字样本仍需补 |
| N09 | PDF 全范围 | 文本/扫描按页读取，后页期限与漏读范围可见 | not_run | PDF 页码/超30页读取、真实单页扫描 OCR/任务/原件通过；关键图表后页与全范围组合待补 |
| N10 | DOCX 与 TXT | 表格/关键嵌图/文本实际读取，编码/外链/损坏不猜 | not_run | 真实段落/表格/TXT、DOCX 关键嵌图 OCR/任务/原件通过；完整编码/外链/损坏组合待补 |
| N11 | 缺关键材料 | 依赖失败材料的行动暂停，独立明确项可保存 | pass | “详见附件”且未收到原件会阻塞依赖项；独立明确项保存，原件/未知位置保护：test_material_relations。真实模型识别依赖仍须 N01/N08 门槛 |
| N12 | 重复与延期 | 唯一既有任务复用/更新，外部编辑/已完成/来源冲突不静默覆盖 | pass | 同 action/task 延期、来源顺序、完成/外部备注冲突：test_notice_revisions |
| N13 | 行动与备注 | 不乱拆格式任务；提交要求完整，用户备注区保留 | not_run | 受管理备注区/用户区保留通过；真实提交要求完整性仍需 Provider 验收 |
| N14 | 来源伪指令与超限 | 不改变权限或执行工具，不以截断/不可见内容作依据 | not_run | 严格 DTO/证据与预算阻塞通过；真实注入/超限多材料组合待补 |
| B01 | 框架原件交付 | 使用 AstrBot 材料能力保存真实字节，无渠道专用取件实现 | pass | 实际公共 File.get_file 原字节/hash；不读 raw_message：framework_contract_smoke |
| B02 | 原生上传 | 中国版真实任务附件区可见可下载，原件 hash 一致 | pass | cn 真实 TXT/PNG/PDF/ZIP 登记下载 hash；用户确认 TXT 客户端原生可见可下载 |
| B03 | 连续与晚到材料 | 正确归组、关联补全，不重建任务、不乱挂最近任务 | pass | 页面晚到原件明确选择任务；真实 Service 跨会话“补充任务 ID”复用已核验导入目标，原生下载 hash/同消息与同 hash 去重，无新任务/模型调用：live_supplement_smoke。取件公共契约另见 B01；账号/目标变化/转发伪指令 9 项保护回归 |
| B04 | 文件去重 | 同目标同 hash 不重传，同名异字节均可辨认 | pass | 真实 Service 同原名两份不同字节：原资产名保留、远端名称区分、各自下载 hash 一致，同 hash 不新增操作：live_same_name_smoke + test_storage_api |
| B05 | 多任务附件关系 | 共用/专属文件正确分配，归属不明追问 | pass | 领域计划样本：两任务共用一原件、各有专属原件，精确四个目标链接；归属不明项暂停且原件不任意挂到已存项：test_material_relations。真实模型关系识别仍须 Provider 门槛 |
| B06 | 不解析原件 | DWG/ZIP 等保持字节可上传，不执行不猜正文 | pass | ZIP 读取为 attachment-only，不解压；真实 ZIP 上传/download hash |
| B07 | 附件失败与额度 | 分项部分成功，保留原件/目标，只补上传，无链接替代 | not_run | 失败安全分类与保留已实现；真实额度/分文件部分成功验收待补 |
| B08 | 上传未知 | 可靠 ID/查询先核查，重启不盲目 upload；无证据保持未知 | pass | 真实登记后阻断 CLI 返回，真实超时并终止进程组；重开 DB 只读候选 ID/目标/download hash 恢复，attempt=1。查不到/目标或 hash 不符不伪成功：live_upload_timeout_smoke + test_upload_recovery |
| E01 | 官网禁用 | 启动/重启/配置无采集请求或自动任务 | pass | 容器启动/重启无网站作业；生产 registry 只有 disabled |
| E02 | 来源契约 | 内存样本转统一通知，生产仅注册禁用实现 | pass | SourceEnvelope/MemorySourceAdapter 样本一次交付统一 intake；不进入生产 registry：test_storage_api |
| E03 | 未实现入口 | 检查/启用官网明确拒绝，无网络、无同名任务 | pass | SOURCE_NOT_IMPLEMENTED，无网络行为：test_storage_api |
| T01 | AstrBot 契约 | 公共事件/组件/回复/Provider/Pages 可用；业务不读 raw_message 分渠道 | pass | 实际公开事件/组件/handler/Pages，真实 Context.llm_generate 正文与视觉输出通过；夹具回执无适配器记 unknown，不冒称渠道送达 |
| T02 | 严格 DTO 与证据 | 额外字段、非法日期/ID/布尔、无依据拒绝，未进入写 Gateway | pass | 严格 union/extra/date/evidence 校验、无依据无 write：test_domain/test_service |
| T03 | 约束与并发 | 唯一消息/操作/附件键与 FK 有效，claim 唯一、同账号写并发 1 | pass | FK/active 账号/消息/操作/附件唯一，条件 claim 与单写锁：test_storage_api/test_service |
| T04 | 动作版本 | 多次延期更新同 task；新直接修改各有 plan；重复解析不重排 ID | not_run | 同 action 延期/重复解析复用已测；多次延期、新直接修改与重排组合待补 |
| T05 | 账号与配置变化 | 相同裸 ID 不串账号，撤权/清单/身份变更暂停旧计划 | not_run | 裸 ID 账号隔离/撤 actor 已测；配置与实际账号切换组合待补 |
| T06 | 材料持久恢复 | pending/取件/读取可恢复或明确请补发，无伪有效 blob | not_run | pending 重启变 unavailable、blob 一致性已有实现；损坏/补发端到端待补 |
| T07 | 队列与预算 | 活动作业/inbox/字节/视觉范围有界，超限不伪已接收/全读 | not_run | 200 并发准入：90 接收/110 明确暂未接收，保留槽位扩至100；50作业压力中老普通作业先获机会、模型并发2，带偏移分块/独立预算与依赖/outbox 保留通过。真实 Provider 性能 p95 样本仍未验 |
| T08 | CLI 契约与故障 | 固定 argv、机器输出、超时/截断/矛盾按副作用归类，可靠 ID 保留 | pass | 真实固定 argv/JSON 与字段；超时、非 JSON、矛盾、可靠 ID 保留：test_cli + live probes |
| T09 | 状态与核查 | 查不到不变成功，成功仅触发一次依赖/outbox；cancel 与 claim 竞争正确 | not_run | 未知查不到不成功、cancel 条件竞争有测试；依赖/outbox 容量与崩溃组合待补 |
| T10 | API 去重与版本 | 所有变更同 request 幂等，不同 payload/旧 revision 拒绝，授权明文不入库 | pass | 实际文件 POST 去重先于 revision；payload 冲突；授权 commit 故障持久维护：container_pages_smoke/test_storage_api |
| T11 | 迁移备份退出 | checksum/高 schema/失败维护有效；DB/blob 一致，热重载/kill 恢复不重放 | not_run | 新迁移容器启动/热重载、backup restore、复核人工确认/草稿保护/标记提交崩溃测试通过；完整实际历史恢复与插件 kill 中写入待补 |
| T12 | 脱敏与注入 | 标题/通知/文件为数据，密钥不入模型/日志/页面，零官网采集 | not_run | argv/纯文本/秘密只写、脱敏诊断包与官网禁用已测；保留策略 10 项已进完整回归，长期并发及真实 Provider 注入待验 |
