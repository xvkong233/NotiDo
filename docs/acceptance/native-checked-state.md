# 核验状态与日期依据修正（2026-10-09）

[V12完整复测](native-corpus-v12.md)48通过／2失败。N07的报名截止保存正确，但回执把滴答随到期日自动补齐的startDate当作比赛日期；N22账本正确为partially_done，回执却把模型提交的awaiting_materials声明称作整组状态。原批次与冻结参考均保留。

工具边界现在只向AstrBot返回NotiDo核验后的state，并标明来自本地账本；声明仍保存在审计记录中，不与核验状态混作两个回执答案。has_saved_work明确是否已有成功保存的工作，未决材料或参与意愿仍保留。测试覆盖实际部分完成、声明保留及禁止重复写入；没有增加回复解析、模型调用或独立澄清流程。

AstrBot工具指导明确：自动补齐的startDate不是活动日期依据；全天回执只显示日期与全天，不把存储午夜说成时刻截止；回执以实际标题、清单、日期、要求和核验后的状态为准。身份、记忆、Provider与聊天处理仍由AstrBot负责。

280项完整回归通过（55.75秒，私有native-checked-state-junit.xml），静态检查及修改文件格式检查通过。8项实际AstrBot公开框架契约通过，隔离临时数据、0 Provider／滴答调用。V13使用原50条冻结参考、新授权会话及真实deepseek/deepseek-flash完整复测并逐条复核：[48通过／2失败](native-corpus-v13.md)。N07和N22已修正；N28把URL列作实际正文的等价选项、D15历史日期来源解释再次失败，原批次保留。

同一notido-native-v7在localhost:16190替换，原数据与授权保留，没有增加容器。初次构建在native.py换行格式统一前取得快照，核查只有换行不同；重新构建后才启动正式V13，未拼接两次安装的结果。

安装代码规范路径SHA-256：`6fe128ef3a815b737d243f00c6036cfcbe1fed3f4ab17848b50a24c53ef3b120`；main.py SHA-256：`6f65ebfd7e492a8fa0c5ef59d8109d79121a89b75b7faca0f042a08ba7071b03`。
镜像manifest：`502b17a971b92711ba696471595541134d8bfffbe2deb59340264e18fa39858d`；image／manifest list：`3b27556eb337830974a059cbaf36b08b74b875b0df3ec00601402257524d0397`。

开发／测试删除按维护者授权自动处理。测试仍通过具体目标零写入预览与后续确认消息验证生产二次确认契约，正式用户任务不绕过确认。完整57项发布验收尚未通过，见[当前范围核对](current-requirements.md)。
