# 原生材料恢复契约（2026-10-09）

锁定的 AstrBot v4.28.2 实际运行时、公开 AstrMessageEvent／File 类型与安装版 NotiDo 插件，7 项通过。代码规范路径 SHA-256：`e5369a81c156f70f5004d605e85250b41266cac7d01e93df20d7460b72e49a92`。

本批使用独立临时数据库与合成文件，不使用真实账号凭据。网关调用被硬性禁止；Provider、滴答调用和远端写入均为0。它检验框架取件与本地持久恢复，不能代表模型自行解读了损坏文件。真实 Provider 的材料解读与挂件另见复杂材料和迟到补件报告。

| 检查 | 实际结果 |
| --- | --- |
| 公共 File 取件中断 | File.get_file 已开始、尚未返回，取消工具请求；已持久 pending asset，hash为空、blob表为空，不报告完整原件。 |
| 重启后引用失效 | 关闭并重新初始化插件，pending标记为unavailable／MATERIAL_RESEND_REQUIRED；材料工具返回补发原因，completed声明被校验为awaiting_materials。 |
| 明确重新提供原件 | 新消息经真正 File.get_file 取得本地原件，保存精确hash并读取UTF-8正文；新组可completed。 |
| 损坏编码输入 | 不可读TXT明确ENCODING_UNKNOWN，声明completed仍为awaiting_materials，不猜正文。 |
| 新消息补发正确正文 | 重新提供可读UTF-8原件，新组读取完成；旧损坏组保持待材料及历史原因。 |
| 持久原件被破坏 | 仅在关闭后的隔离目录修改一个已知hash的合成blob；真实启动检查进入BLOB_CORRUPT维护，材料工具被MAINTENANCE阻断。 |
| 精确字节修复 | 校验保留的原字节hash，再恢复该隔离文件；重启与全部blob完整性检查通过。此为明确测试修复，不是自动覆盖损坏的用户原件。 |

所有临时数据经独立根目录校验后清理，不影响正在运行的唯一验收实例、授权或原件。首次脚本仅因外置脚本源码定位错误在加载阶段失败；定位修正后完整运行，初始诊断与最终输出分别保留，未把初始失败视为通过。

私有原始结果：`runtime-data/native-material-recovery-contract-raw.txt`、`native-material-recovery-contract-r2-raw.txt`、`native-material-recovery-contract-results.json`。实现脚本为 `tools/native_material_recovery_contract.py`；本批未新增第二个容器。
