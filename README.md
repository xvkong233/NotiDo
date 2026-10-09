# 知办 · NotiDo

*From Notices to Action.* **让通知成为行动。**

<img src="logo.png" width="88" height="88" alt="知办 NotiDo 插件标志">

AstrBot 的个人通知待办插件：从已交付的正文、截图和文档中提炼本人行动，通过受控 CLI 写入中国版滴答清单，并把原件上传到任务原生附件区。下游连接、消息协议、Provider 和后台登录由 AstrBot 提供。

版本 **0.1.1** · 作者 **NotiDo contributors** · [GPL-3.0-only](LICENSE) · [更新日志](CHANGELOG.md) · [反馈问题](https://github.com/xvkong233/NotiDo/issues)

| 能力 | 使用方式 |
| --- | --- |
| 通知变待办 | 提供正文、截图、PDF 或 DOCX，沿用 AstrBot 的身份认知与记忆；缺关键条件时询问 |
| 滴答任务 | 查询、新建、修改、完成，以及日／周／月／年周期任务 |
| 删除保护 | 先预览具体任务，在后续消息二次确认后删除 |
| 原生附件 | 保存实际原件并挂到任务附件区，核查目标及下载 hash |
| 管理页面 | 五组设置、通知状态、操作账本、备份恢复和本地数据保留 |

**环境要求：** AstrBot `>=4.28.2,<4.29`、Python `>=3.12,<3.15`、Node.js 24、`@suibiji/dida-cli@0.1.14`；扫描 PDF 需要 Poppler 的 `pdftoppm`。已实测 AstrBot 4.28.2。插件采用框架公开消息组件，不额外声明未经实测的平台适配器；图片理解需要所用 AstrBot Provider 支持视觉输入。

本版聚焦中国版滴答与单个个人账号。官网采集、原生提醒、自动报名／提交、多人独立账号和批量删除不在本版范围内。

## 安装到 AstrBot

可以在 AstrBot 插件管理中使用仓库地址 `https://github.com/xvkong233/NotiDo` 安装，也可将发布 ZIP 导入。插件的注册名及解压目录名为 `astrbot_plugin_notido`。AstrBot 会安装根目录 `requirements.txt` 中的 Python 依赖；Node.js、任务 CLI 和 Poppler 需由宿主机准备。

在插件目录执行以下命令安装锁定的任务 CLI（不需要全局安装）：

```sh
cd /path/to/AstrBot/data/plugins/astrbot_plugin_notido
npm ci --ignore-scripts --no-audit --no-fund
node --version
pdftoppm -v
```

重载插件后，`cli_node` 留空时从 AstrBot 进程的 PATH 查找 Node.js，`cli_script` 留空时使用插件目录内的 `node_modules/@suibiji/dida-cli/dist/index.js`。也可填写实际绝对路径；已有配置中的旧路径需清空或修改。`data_root` 推荐留空，业务数据保存在 AstrBot 的 `data/plugin_data/astrbot_plugin_notido`，不放在插件源码目录；`instance_id` 安装后保持不变，同一数据目录仅运行一个插件实例。

### 授权与选择清单

在 AstrBot 中配置所用 Provider，再打开 NotiDo 的 dashboard 页面：

1. 在 AstrBot 中配置 Provider、人格与记忆；NotiDo 的“常规”分组仅设置业务时区。
2. 使用官方 dida-cli 的浏览器 OAuth 完成中国版任务授权，在本机写入页面的“任务授权”。明确选择新账号或同账号重新授权，凭据输入只写不读。
3. 刷新真实清单，选择默认清单和允许范围。插件运行时不会自行创建清单。
4. 原生附件另需同账号网页会话授权；在本机填入会话 `t`，选择允许清单中的一个真实任务核验身份。任务 OAuth 不含这项能力。
5. 在“会话授权”绑定真实 AstrBot platform ID、sender ID 和 unified message origin。只允许已启用会话调用滴答工具，普通聊天沿用 AstrBot 原生流程。

不要把密码、token 或网页 cookie 发到聊天或 Git。配置文件和运行数据只留在数据根；任务授权与附件授权分别保存在 `cli-home/.config/dida-cli/config.json` 和 `cli-home/.config/notido-attachments/config.json`。不要调用会输出 token 片段的 CLI auth status 来生成公开日志。

## 使用

直接在 AstrBot 对话中提供通知、图片或文件。AstrBot 沿用当前会话、人格和长期记忆理解身份、提炼本人行动、处理歧义，并选择 NotiDo 函数工具。插件不截断普通聊天，不维护独立身份档案，不直接调用模型或 OCR。

在 AstrBot 的函数工具列表中可见 `notido_projects/query/create/update/complete/delete/cancel/materials/attach/check/record_outcome`。先在插件页面授权滴答账号、清单及真实框架会话；未授权会话无法操作。图片和文档图像由 AstrBot 当前模型读取，需要框架 Provider 支持相应能力。

明确指令也可使用同一执行入口：

```text
/notido 清单
/notido 查询 {"scope":"today"}
/notido 新建 {"request_key":"报告1","title":"提交报告","date_text":"2027年12月20日","time_text":"17:40"}
/notido 修改 {"request_key":"改名1","selection_ref":"查询返回的引用","patch":{"title":"新版报告"}}
/notido 完成 {"request_key":"完成1","selection_ref":"查询返回的引用"}
/notido 新建 {"request_key":"周报1","title":"写周报","date_text":"2027年12月20日","time_text":"17:00","recurrence":{"frequency":"weekly","weekdays":["MO"],"count":5}}
/notido 删除 {"request_key":"删除1","selection_ref":"查询返回的引用"}
/notido 删除 {"request_key":"删除1","confirmation_ref":"预览返回的引用","confirm":true}
/notido 材料 {}
/notido 附件 {"request_key":"原件1","selection_ref":"查询返回的引用","asset_id":"材料返回的原件引用"}
/notido 核查 {"operation_id":"此前返回的真实操作ID"}
/notido 取消 {"query_only":true}
/notido 取消 {"operation_id":"账本返回的未开始操作ID"}
/notido 结论 {"group_id":"此前返回的材料组引用","state":"awaiting_clarification","pending_reason":"是否参加自愿活动尚未确定"}
```

同一行动重试保持 request_key 不变。工具只根据真实回读报告成功；结果未知时只核查，不重建任务或重传原件。修改只提交需变更字段；未提交字段保留。日期级任务写原生全天，不造时刻。周期支持日/周/月/年及明确首次日期。删除先展示唯一具体任务和范围，十分钟内由后续消息明确确认才执行；指令示例的两步必须分别发送。任务变化或确认过期须重新查询及确认。本地取消只处理本会话的未开始账本操作，多项先选目标，保留已写远端任务，不替代删除。提醒和自动提交尚未支持。

设置按常规、滴答与清单、会话授权、材料与运行、数据与维护分组。AI、人格、记忆和 Provider 均在 AstrBot 管理。旧独立 AI 草稿与未执行计划会暂停保留，须在原生对话核对后继续。

当前含十一个原生工具；通知状态由 AstrBot 登记，NotiDo 根据实际账本校验。当前安装版[冻结语料V14](docs/acceptance/native-corpus-v14.md)50条、[复杂材料V5](docs/acceptance/native-complex-material-v5.md)9项、[性能V5](docs/acceptance/native-performance-v5.md)30条文字／10条文件全部通过；[完整历史恢复](docs/acceptance/native-outcome-restore-v7-r3.md)核查228条操作及522组结论。180秒同组解码／渲染累计预算已实现，完整57项通过，见[当前57项核对](docs/acceptance/current-requirements.md)。历史失败保留在原批次报告中。

## 备份与恢复

先停止该数据根的 worker，再使用一致性备份工具。命令中的目录为示例，备份与恢复目标必须不存在：

```sh
python -m tools.backup backup /path/to/plugin-data /path/to/new-backup
python -m tools.backup restore /path/to/new-backup /path/to/new-plugin-data
python -m tools.diagnostics /path/to/plugin-data /path/to/new-diagnostic.zip
```

备份包含 SQLite 快照、引用 blob、两类 CLI 认证和授权 fingerprint key，因此应保护整个备份。恢复逐文件核对 manifest、hash、DB 外键和 blob 引用，拒绝越界、链接、夹带文件与覆盖活跃数据库。

诊断包仅含状态聚合、迁移版本和安全错误码，不包含身份、原通知、原件、origin、token 或完整日志。

恢复后保留 `restore-review.required` 并暂停新写。在“操作 / 恢复”页开始只读复核，按批读取历史目标，检查当前清单和未匹配项，再人工确认备份后的新增/修改及旧账号历史。复核过期或配置/账本变更需重做；回滚本地 DB 不撤销远端任务。确认只解除恢复维护，旧计划继续暂停，旧草稿在通知详情中另行确认重新处理。当前完整228条历史恢复已通过，原未知结果继续暂停；见恢复报告。

原件、CPU读取与保留预算可在插件设置中调整；默认同组解码／渲染累计180秒，首次读取固化组时限，配置调整应用于新组，分页、补件及重启不重置已用预算；AI能力在 AstrBot 配置。默认正文及可重建副本保留 30 天、完成摘要 90 天；远端未完成任务的证据与原件保留到首次观察完成后再过 30 天。未知、待上传、活动草稿及未送达回执保护引用，共享 hash 有有效引用时不删除。轻量幂等键当前长期保留，至少 180 天。清理只处理本地数据，不删除滴答任务或附件。

## 开发与证据

```sh
uv sync --frozen
npm ci --ignore-scripts
uv run playwright install chromium
uv run python -m pytest -q
uv run ruff check notido main.py tests tools
uv run python -m tools.export_schema
```

页面测试使用 Playwright Chromium，本地回归使用模拟 Provider 与 Gateway。仓库仅保留插件代码、运行维护工具与插件契约测试；旧联调脚本可在 `v0.1.0` 标签查阅，历史验收报告保留原批次与验证环境。

截至2026-10-09：当前完整回归288项、8项真实框架契约、5项生命周期、50条冻结语料、9项复杂材料、40次性能及228条历史恢复通过；[实际后台与预算](docs/acceptance/native-read-budget.md)10项页面检查和30路由未登录拒绝通过。同组读取预算已实现，完整57项全部通过；既有真实Provider批次仍保留其原始版本。详细证据：

- [PRD v1.7](docs/PRD.md)：唯一需求基线。
- [兼容性与外部能力](docs/compatibility.md)：精确版本、字段映射、能力边界。
- [57 项验收清单](docs/ACCEPTANCE.md)：每项状态与证据，未完成项不计通过。
- [实现与验收进度](docs/IMPLEMENTATION.md)：尚需完成的实现和验证。
- [严格工具参数 Schema](schemas/native-create-v1.json)：原生工具参数；AI、会话与记忆由 AstrBot 管理。
- [50 条参考答案](docs/acceptance/corpus-frozen-v1.json)：已获用户审核并冻结，失败样本不移除。

## 发布与来源说明

[发布准备](docs/RELEASE.md)记录元数据、外部依赖、发布包与市场提交步骤。仓库提供的 `tools/build_release.py` 只打包公开源码与文档，不访问滴答、不调用模型、不上传或提交市场。

推送与插件版本一致的 `v*` 标签后，GitHub Actions 自动运行本地测试并发布插件 ZIP 与 SHA-256：[版本下载](https://github.com/xvkong233/NotiDo/releases)。首次 AstrBot 市场上架需在 AstrBot Cloud 提交并审核，具体步骤见发布准备。

本插件的框架集成遵循 [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)，AI、人格、会话与记忆能力由 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 提供。任务接口使用 [@suibiji/dida-cli](https://www.npmjs.com/package/@suibiji/dida-cli)。附件授权与接口路径参考了 [DeliciousBuding/dida-cli](https://github.com/DeliciousBuding/dida-cli) 的设计；未复制其源码或二进制，附件扩展由本仓库实现。Logo 沿用本插件页面的绿色“知”字标识，源文件见 [assets/logo.svg](assets/logo.svg)。

删除后详情接口可能仍返回回收站记录；核查采用可靠不存在结果或同账号网页会话的原生删除标志。缺少可靠证据时保持待核查，不重复删除。

源码采用 [GPL-3.0-only](LICENSE)。公开仓库只保存源码、需求与脱敏样本；真实通知、原件、账号凭据及本机验收结果不会提交。
