# 原生执行的真实进程中断验收（2026-10-08）

Windows 主机用真实解释器子进程执行 NativeTools → Service → 固定任务 CLI → 中国版专用清单；公共 Bridge 为本机事件夹具，不调用 Provider。仅创建两个唯一合成目标，不删除或清理历史任务。恢复使用拒绝所有写入的 Gateway，并核验真实远端数量和字段。

生产代码 SHA-256：`b925aa999b18af4d9bc80356fc14a51b7d4f5d124f6ffcc3ef11a01f6d6aa74f`。该批次在后续局部日期精度修复之前采集，不能混作后续全部行为验收。

| 中断位置 | 中断前持久状态 | 重启结果 | 实际远端数量 | 尝试数 | 恢复写入 |
| --- | --- | --- | ---: | ---: | ---: |
| before_write | executing | outcome_unknown | 0 | 1 | 0 |
| after_write_before_id | executing | outcome_unknown | 1 | 1 | 0 |
| after_id_before_verify | created_unverified | succeeded | 1 | 1 | 0 |

每个标记须核对真实执行 PID、边界和已提交的 SQLite 状态，随后 TerminateProcess 并等待该进程终止。生产启动流程将 executing 转为 outcome_unknown，可靠 ID 尚未落库时不把测试观察到的远端 ID 假装为本地证据；已持久化 ID 则通过同账号精确任务回读核验成功。重放原消息/同 request_key 返回账本结果，不启动第二次创建；没有独立回执记录。

首次预检发现 Windows venv 启动器 PID 与实际解释器 PID 不一致，未计为中断通过；原标记、数据库与诊断保留，实际进程终止后只读核对远端零任务。脚本改用同版本真实解释器和虚拟环境依赖路径，三个独立正式样本随后完成。未覆盖或删除该诊断。

本批覆盖实际 OS 强制中断与原生执行账本，不能代替 AstrBot 容器 SIGTERM/卸载中写入、真实渠道回复失败或模型部分成功回执的验收。原始私有证据：runtime-data/native-process-kill-results.json。
