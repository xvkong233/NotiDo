# 未知回执契约实际复测（2026-10-09）

复用唯一notido-native-v7、原端口16190及原数据根，镜像通过托管源码升级安装修正，未挂载工作区源码。8项真实AstrBot公共契约通过。镜像manifest为 `a7bac51a4e63fa1f0f8a1f1d98c7b50aa0e3dd60dcaa8823b49ed83e437da578`，本地image/manifest list为 `339ff1fd0961c47416c95169c92cf75820d60f170dcd5a30f8cd4763a0ed90b2`，实际安装代码SHA-256为 `6bc404e44322ee554966c8ffba00aa01ee47efe30b9a365e810bf2bf25d6ac87`。

两个全新专用合成任务分别在真实远端写入后、CLI stdout返回前执行实际插件卸载和容器SIGTERM。临时runner的范围、排他标记和配置恢复同[原始边界报告](native-shutdown-v7.md)，没有向插件导入runner掌握的远端ID。

| 检查 | 卸载 | SIGTERM |
| --- | --- | --- |
| 远端创建／账本尝试 | 1／1 | 1／1 |
| 重载后账本 | outcome_unknown，remote_id=null | outcome_unknown，remote_id=null |
| 实际查询与核查 | 完整标题1项，原操作仍未知 | 完整标题1项，原操作仍未知 |
| 恢复额外写入 | 0 | 0 |
| 未知回执 | 原始回复及只读恢复准确区分查询存在与未核验 | 只读恢复准确说明未知，未再建议把它作为新写入重试 |

原始卸载失败的“已核验”与SIGTERM恢复建议重建，在新批次均未复现。但SIGTERM恢复回执把备注“专用退出边界验收”误写为清单名称，**本条完整回执语义仍失败**；实际工具查询只返回projectId而无名称，不能据此报告一个猜测名称。工作区新增从实时清单查询取得project_name/query_scope；清单名称读取失败时明确null，不把任务备注、历史猜测或任务中非权威名称字段当成清单名。40项定向回归通过，新的完整回归/容器集成尚在进行。

不以未知状态修复通过掩盖其他字段回执失败，不覆盖前批失败。证据在runtime-data/native-shutdown-v7-r2-results.json、native-maintenance-framework-results.json。工具为native_shutdown_v7_smoke --batch v7-r2；collector为新批使用独立journal与边界目录，禁止重发已尝试创建。
