# 当前安装版本完整恢复复验（2026-10-09）

唯一notido-native-v7正常停机，离线备份当前实际SQLite、引用原件和两类授权，然后重启同一实例。恢复到本机独立目录，仅使用禁止写入的真实网关和NoProvider；未增加容器或运行独立AI。

备份来源安装版本与恢复工作区代码一致：`14f6a23478e9a73ebfffc9d794e9e408fba7804ad2611f9687cde9e6ad1e76ed`。实际镜像安装hash在备份前读取，源版本保存在证据source_runtime_version。当前8项框架公共契约、5项实际生命周期均通过；生命周期记录按代码hash分文件，保留旧批证据。

- 完整逐项只读复核119条操作历史，含原/新退出边界4条无ID未知、已确认删除、零尝试取消、通知连续版本及失败后仅补传的原件。
- 真实原件下载hash、DB与认证备份一致；缺少显式复核声明时确认被拒绝，完成明确声明后确认持久有效。
- 41个未完成组保留restore hold，131组原生结论/授权范围完整一致，28份未决声明保留。
- 所有4条受控退出未知操作保持outcome_unknown、remote_id=null、attempt=1，并在恢复确认后继续paused，未关联测试runner掌握的ID。
- 0次模型调用、0次远端任务写入或上传；没有复现原维护模式后台核查改变revision的竞争。

全部5项恢复检查通过。结论快照SHA-256：`fe66f40f1f45dc124a385452a87e3780fcbac1f5b071610ed92f219786f519b6`。原[113条恢复](native-outcome-restore-v7.md)的RESTORE_REVIEW_STALE观察保留，不替换为本批通过。

工具native_outcome_restore_smoke.py --batch v7-r2、live_restore_smoke.py；完整私有证据runtime-data/native-outcome-restore-v7-r2-results.json。原实例授权、凭据及历史ID不进入报告。
