# 当前原生后台浏览器核验（2026-10-08）

在唯一保留的 notido-native-v7（localhost:16190）运行实际 AstrBot 4.28.2 WebUI，使用已授权本机凭据登录，由框架 Plugin Pages iframe 和 bridge 加载 NotiDo。Playwright Chromium执行9项只读检查，全部通过，页面变更为0。

main.py SHA-256：`228630c26fabab03f3fc5801d41d5e998ea0d94f12f06ac3754e3626ca45fad3`；安装代码 SHA-256：`fbf9ce11f538144cab9985357e0f9e1ce9a68920a673ab44602da39f9d6b1628`。与冻结语料V7、性能V4相同安装版本。

- 实际框架登录、Plugin Pages发现及bridge读取成功。
- 常规、滴答与清单、会话授权、材料与运行、数据与维护五组各自切换；每次仅一组可见。
- 页面没有手工身份或Provider输入控件。
- 当前通知历史、处理状态与操作历史可见。

本机保存 settings-1..5、notices、operations 共7张截图；已检查常规和材料与运行截图，分组及表单布局正常。首次浏览器的框架可关闭公告由Escape关闭，没有强制点击穿透遮罩或更改偏好。该报告只证明上述9项浏览器行为，不替代未登录API拒绝、全部XSS边界或迁移恢复的专门测试。

同一版本另以 tools/native_pages_auth_smoke.py 逐项检查源代码注册的30个API路由：读取、变更和原件下载均在无凭据请求下返回401或403。变更请求仅发送空对象；认证先于处理参数，不执行变更。完整状态码保存在本机native-pages-v7-auth-results.json，不记录响应正文或凭据。首次探针误把路由数量写为31，发送前即停止；改为读取实际注册表后30项全部拒绝。

可复现工具为 tools/native_pages_browser_smoke.py。凭据与截图仅保存在被Git/Docker排除的runtime-data，不进入公开报告。
