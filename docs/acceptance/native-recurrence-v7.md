# 当前原生周期规则（2026-10-08）

唯一V7实例，AstrBot 4.28.2、deepseek/deepseek-flash / deepseek-flash及已授权中国版专用清单。4个独立会话、4次首次创建，每次attempt=1，标题/清单/备注/首次时刻及原生重复规则实际回读一致。没有完成或删除这些周期任务。

安装代码SHA-256：`fbf9ce11f538144cab9985357e0f9e1ce9a68920a673ab44602da39f9d6b1628`；main.py SHA-256：`228630c26fabab03f3fc5801d41d5e998ea0d94f12f06ac3754e3626ca45fad3`。

| 规则 | 首次（Asia/Shanghai，非全天） | 原生规则与模式 |
| --- | --- | --- |
| 每周一，共5次 | 2027-12-20 17:00 | WEEKLY / BYDAY=MO / COUNT=5，日历起算 |
| 每2天，共4次 | 2027-12-21 08:10 | DAILY / INTERVAL=2 / COUNT=4，完成日起算 |
| 每月最后一天，共3次 | 2027-12-31 09:05 | MONTHLY / BYMONTHDAY=-1 / COUNT=3，截止日起算 |
| 每年2月29日，至2036-02-29含当日 | 2028-02-29 10:15 | YEARLY / BYMONTH=2 / BYMONTHDAY=29 / UNTIL=20360229T155959Z，日历起算 |

逐条回执与字段已阅读，4份输出hash见recurrence-v7-semantic-review.json。月周期回执末尾有多余条件性清单提示，未增加确认门槛或额外操作。首次不明的自然请求另由冻结V7 D11验证零写；矛盾规则不退化的领域/原生回归保留。

工具 tools/native_recurrence_v7_smoke.py，私有原件为runtime-data/native-recurrence-v7-results.json，发送前记账、中断不重发。这些补充样本不计入冻结50条分母。
