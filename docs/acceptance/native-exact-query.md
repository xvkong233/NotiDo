# 精确任务核验修复（2026-10-09）

V10 N24 的查询只返回关键词第一页10项，`has_more=true`；`known_actions`中的确切来源任务没有返回，`notice_readbacks`为空。AstrBot仍把同名旧任务当作已核验目标，结论为待澄清而非部分完成。账本拒绝未关联查询的行为正确；修复不扩大成功证据范围。

- `notido_query`新增可选`task_id`，在当前允许清单、未完成状态和其他查询条件内精确筛选，仍使用实时滴答结果。索引不是成功凭据。
- AstrBot工具说明要求重复通知优先用材料索引返回的确切任务ID查询，并核对实际返回ID及`notice_readbacks`。关键词第一页同名项不替代来源关联目标，未返回项不宣称已核实。
- 补记指导区分尚未同意与明确拒绝；前者询问授权、登记待澄清。缺少年份只问年份，回执不默认或猜测年份。
- 不增加模型、身份设置、回复解析或另一套澄清流程。

完整本地回归280项通过（62.91秒），静态检查通过；本次修改文件格式检查通过。新增两项回归覆盖超过一页的同名项、精确来源目标的部分完成状态，以及允许范围外／已完成任务不可查询为当前待办。既有未核验／未知结果／来源字段变化／未读材料等拒绝边界保持。

同一个`notido-native-v7`已替换，localhost:16190和原数据／授权保留，没有新建额外容器。8项实际AstrBot公开框架契约通过，包含人格／记忆／原工具保留和删除确认来源；初次探针因测试模块不在安装目录而未执行，修正模块路径后直接加载已安装插件，使用隔离临时数据根，0 Provider与0滴答调用。首次stderr诊断从工具结果保存为私有`native-exact-query-framework-failure.json`（首次stdout文件为空）；完整通过结果在`native-exact-query-framework-r2-raw.txt`。

安装代码规范路径SHA-256：`99a83baf0d2f6306409900be0b48a3c191a33cc84e5543f74a503e7d0c633809`；main.py SHA-256：`1ea827074cdb8fc7f99fd4e1ce4987415fcc85fda0880b74fd25bc96c72f2d0a`。
镜像manifest：`b5f06aeb15fe6b96b39ad9b2088653751ad07937ea59049fd2a718ad4ed9fc7f`；image／manifest list：`329a63086095e737db5bb904c6ad5f39f3a76de62fa8253872ade4b0fa76bd1c`。

V11固定该安装版本、使用原50条冻结参考和新的授权会话，通过真实deepseek/deepseek-flash完整复测，已完整采集并逐条复核：[V11](native-corpus-v11.md)49通过／1失败（D15）。N17实际待澄清并询问补记；N22、N24实际按确切task_id查询、`notice_readbacks.verified=true`，当前组均为`partially_done`。28项行动／21项日期匹配，零额外创建与不确定误写；D15查询历史日期解释不准确仍保留失败。5项真实生命周期检查通过，完整结果在私有native-exact-query-lifecycle-raw.txt。测试删除按维护者授权自动走具体目标预览与后续确认两阶段，不改生产确认规则。V10失败保持在[完整报告](native-corpus-v10.md)，不能以回归通过宣称正式语义验收完成。
