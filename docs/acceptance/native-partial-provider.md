# 当前原生模型部分失败与仅补失败附件（2026-10-09）

唯一notido-native-v7、deepseek/deepseek-flash及已授权中国版专用清单。安装代码SHA-256：`14f6a23478e9a73ebfffc9d794e9e408fba7804ad2611f9687cde9e6ad1e76ed`。实际AstrBot原生会话/工具循环，不调用插件自有Provider或解析回复控制执行。

两个独立无日期任务首次创建各一次；两份TXT原件经AstrBot上传、公共材料工具保存并实际读取，各只关联对应任务。临时CLI进程包装器只对专用清单和乙原件的精确SHA-256，在外部附件CLI执行前返回机器契约ATTACHMENT_QUOTA、side_effect=none。甲实际上传、登记、下载hash核验成功；乙failed_safe、attempt=1、未调用外部上传。这是受控安全失败，不能冒称真实账号配额耗尽。

实际模型分项回执明确甲原件已挂、乙原件未挂和失败原因，两任务已经保存；原组登记task_saved_attachments_pending，仅乙在pending_attachments。管理员通过实际Pages重试接口只补乙的既有失败操作：乙最终attempt=2，两个创建及甲上传仍attempt=1，全部原件下载hash与输入一致。随后原生模型只核查四个操作、更新原材料组completed，无未决附件、无额外远端写入。

保留一次验收辅助器问题：初轮包装器在大stdout管道排空前退出，真实清单查询JSON被截断，两个任务已创建但上传均未执行。实际模型未冒称附件成功。只读对比普通runner正确返回425项、包装器CLI_OUTPUT_INVALID；修正包装器等待stdout写入回调后退出，再继续同一任务和原件的挂接，未重发创建或覆盖初轮输出。该问题不归因于滴答索引，不计为预定配额故障证据。

| 操作 | 初次结果 | 恢复后结果 | 尝试次数 |
| --- | --- | --- | ---: |
| 甲任务创建 | succeeded | 保留 | 1 |
| 乙任务创建 | succeeded | 保留 | 1 |
| 甲原件上传 | succeeded，真实下载hash一致 | 保留 | 1 |
| 乙原件上传 | failed_safe，side_effect=none | succeeded，真实下载hash一致 | 2 |

原始与后续回执、工具输出、每次发送前状态、失败和恢复操作均在runtime-data/native-partial-provider-results.json。工具native_partial_provider_smoke.py、native_boundary_node.mjs；finally恢复正常Node配置、重载同一实例。未改AstrBot核心、增加容器或重传已成功附件。
