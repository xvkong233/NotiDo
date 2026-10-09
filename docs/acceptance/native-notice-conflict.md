# 原生来源冲突与外部编辑保护（2026-10-09）

真实中国版滴答专用“NotiDo 验收”清单，锁定版 AstrBot 公开 Node／AstrMessageEvent／AstrBotBridge 与 NotiDo 原生工具，5项通过。安装代码规范路径SHA-256：`e5369a81c156f70f5004d605e85250b41266cac7d01e93df20d7460b72e49a92`。

本批只创建一个带独立编号的合成任务，网关硬性限制清单、标题和任务ID。显式构造工具参数和框架来源发布时间，0 Provider调用，不声称模型自行判断了来源先后。自然会话的重复通知、两次延期与完成另保留V7证据。

| 场景 | 实际结果 |
| --- | --- |
| 较旧原发布时间 | 原通知10-07，修改来源10-06；SOURCE_ORDER_CONFLICT，零新账本操作、零网关写入。 |
| 来源先后未知 | 来源没有框架发布时间；SOURCE_ORDER_UNKNOWN，零操作与写入，保留明确核对要求。 |
| 用户外部编辑 | 绕过NotiDo账本仅修改这个测试任务的真实日期，并追加用户备注；较新通知请求被TARGET_CHANGED阻断，外部日期与备注未被覆盖。 |
| 明确较新来源更新 | 验收控制程序恢复这个测试任务的原日期，保留新旧用户备注；来自10-08的通知更新同task_id/action_id，item_revision=1。真实回读日期正确，用户原有和另加备注均保留。 |
| 完成后的新延期 | 真实完成回读status=2；后续延期被TARGET_CHANGED阻断，零重开、零替代任务创建，status仍为2。 |

共5次受控网关写入：原生创建／通知更新／完成各一次，外部模拟编辑和恢复测试日期各一次。三个拒绝边界没有增加写调用或操作记录。没有删除其他任务，也没有改变实际验收实例的身份、配置或账本；故障账本位于独立数据根。

脚本：`tools/native_notice_conflict_contract.py`。私有原始结果：`runtime-data/native-notice-conflict-contract-raw.txt`、`native-notice-conflict-contract-results.json`。真实任务标题与引用、各步骤结果和独立数据根保留在私有记录；操作失败或中断不会自动重放。
