# 0.1.1 插件发布

核对日期：2026-10-09。依据 [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)及[官方发布说明](https://docs.astrbot.app/dev/star/plugin-publish.html)。本文件用于发布前准备，未表示已提交插件市场。

## 插件信息

| 项目 | 当前值 |
| --- | --- |
| 注册名／包根目录 | `astrbot_plugin_notido` |
| 展示名 | 知办 · NotiDo |
| 版本 | `0.1.1`；Git 标签使用 `v0.1.1` |
| 作者 | `NotiDo contributors`（沿用现有署名） |
| 市场身份 | `NotiDo contributors/astrbot_plugin_notido`，由元数据 author/name 派生 |
| 仓库 | https://github.com/xvkong233/NotiDo |
| 许可证 | GPL-3.0-only，根目录 LICENSE |
| AstrBot 范围 | `>=4.28.2,<4.29`；实际验证版本 4.28.2 |
| Python | `>=3.12,<3.15`；以 pyproject.toml 为准 |
| Logo | 根目录 logo.png，256×256；设计源文件 assets/logo.svg |
| 市场简介与标签 | metadata.yaml 的 short_desc、desc、tags |

注册名保持小写且使用推荐的 `astrbot_plugin_` 前缀。仓库名称沿用已有 NotiDo，发布包内根目录使用注册名；作者与仓库地址未被自动改成新的身份。`support_platforms` 为可选项，本版不列出未经实测的具体适配器；使用框架公共组件不等于已经逐平台验收。选择新适配器的发布声明前应先实测该适配器。

## 安装与依赖

本仓库仅以 AstrBot 插件交付，通过已有 AstrBot 的插件管理安装仓库或导入发布 ZIP。Python 依赖由根目录 requirements.txt 提供，Node.js 24、`@suibiji/dida-cli@0.1.14`、扫描 PDF 使用的 Poppler 由宿主机准备。插件目录执行 npm ci 安装锁定的任务 CLI；插件不自动安装系统级或 npm 依赖。CLI 配置留空时从宿主 PATH 查找 Node.js，并使用插件目录的 dida-cli 入口；自定义绝对路径仍可使用。

启动配置 `_conf_schema.json` 只含数据目录、稳定实例标识及固定 CLI 路径。任务／附件授权、允许清单、真实会话授权、材料和保留预算在插件 Pages 中分组设置。默认持久化目录位于 AstrBot 的 data/plugin_data，而不是插件源码目录。

## 发布包

从仓库工作区生成候选包，目标文件必须尚不存在：

```sh
python -m tools.build_release --output dist
```

输出 `astrbot_plugin_notido-0.1.1.zip` 及 `.zip.sha256`。包根目录为 `astrbot_plugin_notido`，包含 main.py、元数据、Logo、配置、Python/Node依赖清单、notido、Pages、迁移、备份和诊断工具、GPL许可证及公开文档。按白名单读取文件，拒绝链接和越界路径；不包含独立部署文件、开发探针、测试运行数据、账号授权、数据库、node_modules、虚拟环境或Git目录。

脚本校验 ZIP CRC 并拒绝超过 16,000,000 字节的包，满足官方发布说明的16MB上限。`.gitattributes` 同时设置 Git 仓库归档时的 export-ignore。GitHub下载和发布提交发生在推送后，不能用本机ZIP的成功代替远端仓库已发布。

开发回归与框架契约测试保留在源码仓库，市场 ZIP 排除测试探针。旧容器联调和部署脚本在 v0.1.0 标签保留；当前分支不再包含这些文件。公开验收报告保留每次真实批次所属版本与环境，原失败样本不覆盖。

## 提交前步骤

1. 核对本机候选包中的版本、作者、仓库、许可证及环境说明，确认必要依赖已在安装指南中列出。
2. 运行 `uv run ruff check .`，确认候选包能被框架解析；业务回归结果见[完整57项核对](acceptance/current-requirements.md)。信息与打包更新无需重新调用大模型或重复真实任务写入。
3. 将所需源码提交并推送到上述仓库，确认版本与更新日志已同步。
4. 给发布提交添加并推送 `v0.1.1` 标签，GitHub Actions 自动测试、打包并发布 GitHub Release；核对 Actions 结果和 Release 附件。
5. 在 [AstrBot Cloud 插件发布页](https://cloud.astrbot.app/)登录维护者账号，提交仓库地址并检查自动解析的信息和包大小，等待审核。市场记录的 author、name、version 必须与 metadata.yaml 一致；作者命名空间沿用本表，不能只因仓库所有者不同而自动替换。身份规则见[官方市场规范](https://docs.astrbot.app/dev/plugin-market/2026-06-27.html)。

## GitHub 自动发布

工作流见 `.github/workflows/release.yml`。推送 `v*` 标签后，在 Ubuntu 24.04 / Python 3.12 下使用 uv 0.11.28 和冻结的 uv.lock 安装依赖，运行 Ruff 与本地 pytest（含模拟页面），再生成白名单插件包与 SHA-256 文件。测试使用模拟 Provider／Gateway，不配置账号凭据、不调用真实大模型或滴答 API。

标签必须严格等于 `v` 加 pyproject.toml 的版本；metadata.yaml 的版本也必须一致，否则发布失败。版本对应的 CHANGELOG.md 段落作为发布说明。预发布标签包含 `-` 时标记为 prerelease。先创建草稿并上传附件，再公开发布；同一标签不会覆盖已有 Release。失败时先查看 Actions 日志；若已产生草稿，应由维护者检查草稿后再处理，不能覆盖公开附件。

发布工作流修正后，可在 Actions 的 Release 页面选择 Run workflow，并填入既有版本标签；工作流检出该标签源码，使用修正后的工作流运行，不需要移动原标签。命令行等价为 `gh workflow run release.yml --ref main -f tag=v0.1.0`。已经公开发布的版本不能用此入口覆盖。

后续发布时，更新 pyproject.toml、metadata.yaml、main.py 注册版本与 CHANGELOG.md，并同步 uv.lock，然后提交、推送源码。以 `0.1.1` 为例：

```sh
git tag -a v0.1.1 -m "NotiDo 0.1.1"
git push origin v0.1.1
```

GitHub Release 自动化使用仓库内置 GITHUB_TOKEN，只授予发布所需的 contents 权限，无需添加滴答或 Provider 密钥。AstrBot 插件市场首次上架仍按官方说明在 AstrBot Cloud 登录并提交；此工作流不代替市场审核。版本范围、平台支持及真实联调结果以验收记录注明的边界为准。
