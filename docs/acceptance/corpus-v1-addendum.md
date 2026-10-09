# 冻结集 V1 的运行条件补充

2026-10-08 用户明确答复，D19 的“本地计划”指 **NotiDo 账本里的未执行操作**，不指 AstrBot 原生定时计划。此补充不改写已冻结的 50 条输入或参考答案，也不替换正式 V1 的失败记录。

D19 必须在独立授权会话准备一项已保存远端任务，以及一项同账号、同用户、同会话的真实本地账本操作（validated、paused、attempt=0、remote_id=null）。暂停操作由验收夹具在单个事务内准备，没有启动 worker 或 Gateway；用户输入通过实际 AstrBot 原生会话和工具处理。验收须回读：本地状态 cancelled、尝试仍为零；此前真实滴答任务仍未完成；取消没有任何新增远端操作。

`tools/native_cancel_smoke.py` 已完成此专项，三项核验通过；原始证据在被 Git/Docker 排除的 runtime-data/native-cancel-results.json。该专项与六条工具说明调试集均单独保存，不能拼接进正式 V1 的准确率。更新后的工具说明与新增本地取消工具须用新的完整批次复测。
