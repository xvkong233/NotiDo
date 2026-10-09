# AstrBot 原生反馈边界核对（2026-10-08）

用户明确确认：PRD 的“3 秒轻量反馈”复用 AstrBot 原生接收／处理中提示。NotiDo 不另建聊天反馈流程。最终任务／原件结果仍独立计时、依据真实核验结果回答。

已只读核对运行中的 AstrBot 4.28.2 验收容器 `notido-native-v6`：

- `astrbot/dashboard/dist/assets/chatMarkdownComponents-BqWAEh23.js` 的消息提交逻辑插入用户消息和 `isLoading:true` 的助手占位；`MessageContentTransition` 在 loading 时显示原生 `loading-message`。
- `astrbot/dashboard/dist/assets/Chat-mlI1dYFE.js` 的消息列表将消息 `isLoading` 传给该组件。`user_message_saved` 事件仅补充消息 ID、创建时间与 checkpoint，不是加载提示的开关。
- `astrbot/dashboard/services/chat_service.py:845` 在保存用户消息后发送 `user_message_saved` SSE 事件。实际原生请求的接收时间保留在既有性能批次中。

[性能 V3](native-performance-v3.md) 的 40 个独立请求中，服务器接收事件 p95 为 0.057 秒；文字／文件最终结果 p95 为 6.916／22.120 秒。数据绑定原批次安装代码，不能当成后续修改版本的性能报告。该批次不包含浏览器绘制耗时；本次界面证据来自实际安装源码，不声称逐帧测量了可见反馈延迟，也不外推所有下游渠道。

旧性能报告中的“不能替代插件准入反馈”保留为当时尚未对齐的历史口径。此文记录用户确认后的口径与实际框架行为，不改变原始计时数据或将旧批次改写成当前版本通过。
