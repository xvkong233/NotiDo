# 当前写后回执断连（2026-10-08）

唯一V7实际AstrBot WebUI SSE连接，在NotiDo create已返回真实succeeded/verification=true时由验收客户端关闭，未收到最终complete事件。账本仍保存同一操作、真实task_id、实际字段、attempt=1；重新取回结果没有任何新写入。

安装代码SHA-256：`fbf9ce11f538144cab9985357e0f9e1ce9a68920a673ab44602da39f9d6b1628`；main.py SHA-256：`228630c26fabab03f3fc5801d41d5e998ea0d94f12f06ac3754e3626ca45fad3`。

立即发出的只读补回执消息被AstrBot并入原先仍运行的agent，其独立SSE没有final_text。首次采集器因此没有找到核查结果；随后确认真实会话is_running=false、active_runs为空，并从框架保存历史恢复实际完成的notido_check和最终回执。未重发请求，原空SSE观察保留，不伪造耗时或屏幕送达测量。

最终回执准确区分本次只读核查和原首次创建，清单、无日期、备注、真实任务ID及带时区创建时间一致。1次创建/1次尝试，0次补取写入。输出hash见reply-disconnect-v7-semantic-review.json。断连由客户端受控触发，不宣称实际公网故障。

工具 tools/native_reply_disconnect_smoke.py；完整实际事件与持久操作仅在runtime-data/native-reply-disconnect-v7-results.json。补回执与排队仍由AstrBot原生会话负责，没有插件独立发信或outbox。
