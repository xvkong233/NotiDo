# 当前安装版后台与鉴权复验（2026-10-09）

在唯一notido-native-v7（localhost:16190）实际AstrBot 4.28.2 WebUI中以已授权本机凭据登录，使用Plugin Pages iframe及bridge读取页面。Playwright Chromium完成9项只读检查，全部通过，未保存配置、上传材料、重试操作或删除任务。

安装代码SHA-256：`cd324442a5596b8716eca38fcebc083a76ec6fc6ab42435241b5f9e9c17f7bf4`；main.py SHA-256：`0f19c0def72b6fee72e8f55776394cb1c4cd44a61bf8eeaa44690187f652a124`。检查前后版本一致，与V14语料、性能V5及复杂材料V5同版。

- 框架登录、Plugin Pages发现及bridge读取成功。
- 常规、滴答与清单、会话授权、材料与运行、数据与维护五组逐一切换，每次只有一组显示。
- 无手工身份或Provider输入；人格、记忆、AI配置留在AstrBot。
- 实际通知／待处理及操作／恢复历史可见。

本机保存7张页面截图，已直接检查材料与运行截图，分组导航和表单布局正常。首次框架可关闭公告以Escape关闭，未强制穿透遮罩或修改偏好。

同版另逐项测试实际API注册表的30条路由，无凭据的读取、变更、原件下载全部返回401或403。变更仅提交空对象，鉴权先于参数处理，未执行变更；不记录响应正文或凭据。该证据证明当前注册路由的未登录拒绝，不扩称所有安全边界的穷尽测试；XSS与秘密字段保护另由回归核查。

采集器native_pages_browser_smoke.py与native_pages_auth_smoke.py --batch v7-r2，结果和截图仅存Git／Docker排除的runtime-data。原[后台V7基线证据](native-pages-v7.md)未覆盖或改写。
