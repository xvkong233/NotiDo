# 知办 · NotiDo：兼容性与业务依赖

[同组读取预算](acceptance/native-read-budget.md)已按用户确认的口径实现并安装到唯一验收实例：288项本地回归、8项真实框架契约、5项生命周期、6项实际文件解码、10项后台检查和30条未登录路由拒绝通过，本轮零大模型调用。[完整57项核对](acceptance/current-requirements.md)全部pass。此前V14冻结50条、复杂材料V5 9项、性能V5 30／10样本及完整228条历史恢复的真实证据保留原版本；本轮仅对材料读取、缓存保留、迁移及新增设置执行针对性复验，未冒称重新调用模型或重新计量既有性能。

基线 PRD v1.7；实现 0.1.0；实测更新 2026-10-09。只验证 AstrBot 公共契约，不建立下游渠道矩阵。下表区分真实外部验证、公共类型夹具和本地故障测试；PRD v1.7 开发验收 readiness=true（明确不实现的能力仍为false）。

| 能力 | 锁定实现 | supported | 验证结果与就绪边界 |
| --- | --- | --- | --- |
| 插件生命周期 | AstrBot v4.28.2 / commit 3c7adafa1397e182d60b1016bf88759265113c8a | true | 实际容器安装/加载/卸载/重载通过；owner 锁释放、第二 owner 拒绝 |
| 消息/身份/会话 | 同上公开 AstrMessageEvent、标准组件 | true | 公开类型 opaque ID/顺序/原时间通过；native_contract_smoke验证11工具注册、指令参数、未授权只隐藏NotiDo工具及保留人格/历史；无普通消息拦截 |
| 材料交付 | File.get_file / Image.convert_to_file_path | true | 实际 File 原字节持久与 hash 通过；媒体不可用/重启策略有本地测试 |
| 回复 | Context.send_message / MessageChain | true | 原 origin 发送、failed/unknown 与补回执有领域测试；框架调用签名已核对 |
| AI 与记忆 | AstrBot 原生 Agent/Provider/ToolSet/MCP ImageContent | true | deepseek/deepseek-flash / deepseek-flash继承原生会话身份，调用11工具；当前冻结语料V14语义50/50、行动28/28、日期21/21，零额外/不确定误写。复杂材料V5 9/9字段／原件／语义通过。性能V5 30／10样本p95为8.037／21.920秒。插件不直接调用模型、不选择Provider、不维护身份；上述真实模型批次保留原版本；最后预算变更无模型复验，完整57项已通过 |
| Pages | Plugin Pages、AstrBotPluginPage bridge | true | 最新实际浏览器10项（含180秒组预算控件）：五组设置单独切换、无手工身份/Provider控件、实际通知与操作历史可见；30个API读取/变更/下载未登录均401/403。multipart/query、hash、请求去重/版本冲突与纯文本安全另有契约/回归 |
| 中国版任务授权 | @suibiji/dida-cli 0.1.14，官方 OAuth PKCE | true | 专用测试账号、专用 NotiDo 验收清单；持久隔离配置授权通过；不公开账号/凭据 |
| 任务与字段 | 官方 CLI + tools/task-extension.mjs | true | 真正增/改/完及回读通过；原生 dateOnly/timed、isAllDay=false、dueDate=null、中文备注、连字符开头标题通过 |
| 周期与确认删除 | 官方 CLI 重复字段 + 受控删除/只读核查 | true | 真实每周/次数/首次时刻回读；用户后续二次确认后单次删除，原生 deleted=1 回读。Open API 仍可读回收站记录，不能仅按详情可读判断存活；同账号网页会话用于该情况的删除标志核验，失败保持待核查。日/月/年、月底/闰日、结束日期及三种起算模式的真实组合也已通过 |
| 本地操作取消 | notido_cancel / /notido 取消 / 页面共享状态转换 | true | 真实暂停的零尝试操作取消、无远端写入，既有真实任务保留；当前账号/用户/会话、开始/未知拒绝、范围截断与 claim 竞争回归通过。不操作 AstrBot 定时计划，不删除滴答任务 |
| 原生附件 | tools/attachment-cli.mjs，受控独立进程 | true | 同账号网页会话核验；TXT/PNG/PDF/ZIP 上传、登记、目标关联、下载 SHA-256 全通过；TXT 客户端可见并可下载获用户确认 |
| 附件未知核查 | 预分配 ID、目标登记查询与下载 hash | true | 探针先记录尝试，重复启动仅 inspect；无可靠登记/hash 时保持待核查；真实登记后阻断CLI返回、超时终止进程组及只读目标/download hash恢复已通过，attempt=1，无盲目重传（live_upload_timeout_smoke）；不将任务kill证据当作附件kill证据 |
| 文档读取 | pypdf 6.19.0 / python-docx 1.2.0 / Pillow 12.3.0 | true | 当前复杂材料V5：第6页唯一期限、31页未读范围、DOCX表格/嵌图、UTF8/编码未知、模糊图像、缺损独立项均实际核验；多任务共用/专属原件正确。180秒同组解码／渲染累计预算已实现，8项新增回归及6项实际文件复验通过，零模型调用 |
| 构建/恢复 | Python 3.12.14、Node 24.18.0、Poppler 25.03.0-5+deb13u4 | true | 锁定镜像构建成功；迁移/checksum/高 schema 拒绝、备份 DB/blob 与受保护恢复有测试；真实中断保护和完整228操作／522组结论恢复5项通过；迁移10及新增预算实际备份恢复通过 |
| 原生提醒 | 当前 Gateway 未暴露提醒写入 | false | readiness=false；明确询问是否只记待办，不把 dueDate 称为提醒 |
| 官网来源 | DisabledWebsiteSource | false | disabled，读取/启用明确 SOURCE_NOT_IMPLEMENTED，无网站请求 |

## 精确构建版本

- AstrBot 镜像：`soulter/astrbot:v4.28.2@sha256:2215f337de16535953df936adaf34a36663d062d85b7ecffe06cdaed74f7b299`。
- Node 镜像：`node:24.18.0-bookworm-slim@sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d`。
- Poppler/libpoppler147：`25.03.0-5+deb13u4`；npm package-lock 和 uv.lock/requirements.txt 锁定依赖。
- 插件无 AstrBot 核心补丁，无独立登录/服务端口；源码目录必须位于框架插件根内，外部符号链接不能通过 Pages 路径校验。

## 任务 CLI 契约

| 领域字段/动作 | 远端映射 | 已验证行为 |
| --- | --- | --- |
| 标题/要求 | title / content | Unicode 纯文本；以 `--` 开头的标题仍作数据 |
| 日期级期限 | dueDate + isAllDay=true + timeZone | 到期字段 midnight 是远端全天编码；本地 local_time/instant 为 null |
| 指定时刻 | dueDate + isAllDay=false + timeZone | Asia/Shanghai 指定时刻回读一致 |
| 清除日期 | dueDate=null | 回读无日期，不创建替代任务 |
| 完成 | complete 固定扩展 | 成功后 get 回读 status=2 |
| 错误/未知 | contract_version、side_effect、remote_id | 不依据退出码猜成功；可靠 ID 先落账再核查 |

官方 update 对布尔 false 的参数表达不充分，官方 complete 不是稳定机器输出，因此用固定 task-extension 调用官方 CLI 库提供机器封装。业务 Python 只执行固定 argv，不直接请求滴答 API。

## 中国版附件契约

account_region=cn；task API 为官方中国版 Open API；附件扩展只访问 `api.dida365.com` 的固定端点。任务 OAuth 与网页会话分别授权，并通过同一真实任务核验账号可见性；维护者必须明确同账号重授权或更换账号，token hash 不充当身份。

原件上传与任务附件登记是两个步骤；只有登记查询和目标附件下载 hash 一致才 succeeded。现有任务先核验，再逐原件执行，保留已有附件和任务字段。未解析 ZIP 上传时保留完整字节。

本地原件限制 20 MiB，上传保守限制十进制 10 MB。已实测名称含中文以及 TXT/PNG/PDF/ZIP；尚未压测账号额度或所有文件类型。额度、网络和超时失败保留目标与原件；不降级为链接，不盲目重复上传。远端缺乏 CAS，回读与登记仍存在外部同时编辑的竞争窗口。

## 证据与来源

公开可复现脚本：`tools/framework_contract_smoke.py`、`handler_contract_smoke.py`、`container_smoke.py`、`container_pages_smoke.py`、`container_recovery_smoke.py`、`container_lifecycle_smoke.py`、`live_smoke.py`、`live_update_smoke.py`、`live_formats_smoke.py`、`live_same_name_smoke.py`、`live_upload_timeout_smoke.py`、`live_supplement_smoke.py`。真实结果和目标 ID 仅保存在被 Git/Docker 排除的 runtime-data。首次 live_smoke 会新增任务，不应自动重跑；后续原件探针使用持久尝试记录，只核查已尝试的上传。跨会话补件探针导入前次已核验真实目标，使用真实任务/附件 CLI 与 Service，公共取件/事件契约另行验证。

参考并核对了 AstrBot 的上述固定源码、官方 dida-cli 0.1.14 包，以及 MIT 的 DeliciousBuding/dida-cli commit c2cfa86d32017650543aadc20a711a06427d7cd3 网页授权路径。候选 CLI 只用于本机授权研究，没有复制源码或二进制到交付物；附件扩展为本仓库自有实现。

v1.6 用户架构纠偏后，模型重试与时限由 AstrBot 原生设置管理；旧版 Provider 调用层计数仅作历史记录，NotiDo 不再调用 Provider 或自行修复模型输出。CLI 读取保留受控重试，写入未知时只读核查。

当前完整本地回归288项通过、静态检查无错误；迁移1–10及11个原生工具注册有回归，当前安装版8项真实框架契约／5项生命周期通过。此前基线V14冻结50条、复杂材料V5 9项、性能V5 40次及228条历史／522组结论恢复通过，保留原采集版本；最新后台10项及未登录API30路由通过。周期、回执断连、真实写后退出与未知只读复核按各自版本保留，旧AI回执失败没有删除。180秒读取预算已按用户确认口径补齐，完整57项通过，见[当前范围核对](acceptance/current-requirements.md)。
