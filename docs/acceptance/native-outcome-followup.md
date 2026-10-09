# 原生结论修正的独立联调（2026-10-08）

使用本机 AstrBot 原生会话和 deepseek/deepseek-flash，独立于正式 V6 50条，不替换原失败。三条均单次发出并持久记录工具结果，实际新增远端操作为零。

已确认删除的补充请求明确要求关闭两个本地组，不再次执行删除；这证明精确引用的结论登记与页面持久回读有效，不代替下一批自然删除确认后自动更新关联组的验收。另两条直接缺时刻/未知清单输入要求如实登记原生处理结论。

main.py SHA-256：`228630c26fabab03f3fc5801d41d5e998ea0d94f12f06ac3754e3626ca45fad3`；安装代码 SHA-256：`89a601020f7218d0728bfb81b060c5591e67d75d82d23f0d6a1cf4303c5e211f`。

| 检查 | 结果 | 实际输出 SHA-256 |
| --- | --- | --- |
| original_delete_conclusion | pass | `7fdb7ddc19e305ee7fea6bd079cca9d82108ddcd8df62e2d9763350591232bd2` |
| direct_missing_time | pass | `bebec1a6794a7f2c6f30916572fc7d0d14066c13a058f459dad7c78b252ec0d0` |
| direct_unknown_project | pass | `7ae5b915e6ad15b815c94ef51831bfe2f38d3a944c5575dfa8486f28999ab390` |

原预览组和确认消息组都经notido_record_outcome返回completed，随后通知 API回读group.state=completed；未决原因清空。缺时刻和未知清单均实际登记awaiting_clarification，零远端任务写入。

本证据不证明全部语料、文件命名修正或新版本性能通过；这些仍须独立完整复测。原始私有记录为runtime-data/native-outcome-followup-results.json。
