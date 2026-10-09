# 当前原生澄清与独立请求（2026-10-08）

唯一V7实例，AstrBot 4.28.2、deepseek/deepseek-flash / deepseek-flash、已授权中国版专用清单。3轮正常会话由AstrBot保留未决意图和年份补充，NotiDo只接收工具参数与结论；没有插件独立草稿/追问流程。

main.py SHA-256：`228630c26fabab03f3fc5801d41d5e998ea0d94f12f06ac3754e3626ca45fad3`；安装代码 SHA-256：`fbf9ce11f538144cab9985357e0f9e1ce9a68920a673ab44602da39f9d6b1628`。

| 轮次 | 实际结果 | 语义复核 |
| --- | --- | --- |
| 交材料，7月16日14:20，年份未知 | 零写，awaiting_clarification | 只问年份，不猜年或降无日期 |
| 独立买实验标签，无日期 | 首次创建1项，dueDate=null | 保持交材料待澄清，未把旧日期带到新意图 |
| 补交材料年份为2027 | 首次创建另一项，2027-07-16 14:20，非全天 | 保留原月日/时刻，未改或重复买标签；本轮组和原待澄清组均登记completed |

两次create均attempt=1，各一份最终原生回复。完成后通过实际API只读回查原待澄清组、独立组和补充组，三组持久状态均completed，均有原生结论记录。不是仅根据模型声称已关闭判定。3份输出的hash绑定复核保存在clarification-v7-semantic-review.json。

工具 tools/native_clarification_v7_smoke.py 在发送前记账；原始事件、参数、操作与三组回查保存在runtime-data/native-clarification-v7-results.json。恢复脚本只复用已保存响应并回查，不重发模型请求。独立补充样本不加入冻结50条分母。
