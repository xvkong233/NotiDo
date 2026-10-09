"""Compare remote fields with frozen references; do not hide unsafe extra writes."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.native_corpus_smoke import FROZEN, JOURNAL, ROOT

# Direct transcription of the maintainer-approved references: title token, local
# date, time (None is all-day if a date exists), and event/deadline classification.
EXPECTED = {
    "N01": [("报告", "2027-12-20", "17:40", "deadline")],
    "N02": [("总结", "2027-12-21", None, "deadline")],
    "N03": [("联系方式", None, None, "deadline")],
    "N04": [("人数", "2027-12-22", "15:10", "deadline")],
    "N07": [("报名", "2027-12-24", None, "deadline")],
    "N10": [
        ("名单", "2027-12-26", "12:00", "deadline"),
        ("工作会", "2027-12-27", "14:00", "event"),
    ],
    "N11": [("报告", "2027-12-26", None, "deadline")],
    "N12": [
        ("报名", "2027-12-20", "18:00", "deadline"),
        ("准备材料", "2027-12-22", "16:00", "deadline"),
        ("参加实践", "2027-12-24", "09:00", "event"),
    ],
    "N14": [("材料", "2027-12-21", "17:00", "deadline")],
    "N20": [("报告", "2027-12-20", None, "deadline")],
    "N22": [("名单", "2027-12-20", None, "deadline")],
    "N24": [("总结", "2027-12-22", None, "deadline")],
    "N25": [("名单", "2027-12-23", None, "deadline")],
    "N26": [("报告", "2027-12-25", "00:00", "deadline")],
    "N29": [("报告", "2027-12-25", "09:20", "deadline")],
    "N30": [("总结", "2027-12-26", "14:30", "deadline")],
    "D01": [("笔记本", None, None, "deadline")],
    "D02": [("报告", "2027-12-20", "16:20", "deadline")],
    "D03": [("课程", "2027-12-21", None, "deadline")],
    "D05": [("实验本", None, None, "deadline")],
    "D06": [("资料", None, None, "deadline")],
    "D08": [("买纸", None, None, "deadline"), ("买笔", None, None, "deadline")],
    "D09": [("实验本", "2027-12-22", None, "deadline")],
    "D20": [("--flag $(echo data)", None, None, "deadline")],
}

# Additional reference/receipt checks require reading the preserved output, rather
# than inventing a second model to score semantics. These are identified defects.
DEFECTS = {
    "N04": "任务日期正确，但回执将 2027-12-22（周三）写成周二。",
    "N08": "正确停止写入并询问班长身份，但额外追问已明确的清单，不符合“只问是否班长”。",
    "N16": "未追问具体时刻，把“下午”降为全天并产生一项额外写入。",
    "N25": "日期正确，但任务备注未保留原文“日前”。",
    "D11": "未询问首次日期，自行选最近周一 2026-10-12 并创建周期任务。",
    "D12": "用户要求原生提醒，未询问是否只记待办就创建普通任务。",
    "D19": "缺少实际暂停本地计划夹具；调用其他调度工具不能作为 NotiDo 本地取消证据。",
}


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def actual(row):
    return decoded(row["result"])["actual_fields"]


def date_matches(row, wanted):
    _, day, clock, _ = wanted
    fields = actual(row)
    if not day:
        return not fields.get("dueDate")
    if not fields.get("dueDate"):
        return False
    local = datetime.fromisoformat(fields["dueDate"].replace("Z", "+00:00")).astimezone(
        ZoneInfo("Asia/Shanghai")
    )
    return (
        local.date().isoformat() == day
        and fields.get("isAllDay") is (clock is None)
        and (clock is None or local.strftime("%H:%M") == clock)
    )


def main():
    state = json.loads(JOURNAL.read_text(encoding="utf-8"))
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    if len(state["cases"]) != 50 or any(
        record["phase"] != "collected" for record in state["cases"].values()
    ):
        raise RuntimeError("all frozen cases and actual D10 confirmation must finish first")
    expected_total = sum(len(wanted) for wanted in EXPECTED.values())
    true_positive = produced = date_expected = dates_correct = unsafe = 0
    rows, details = [], []
    for case in frozen["cases"]:
        case_id = case["id"]
        record = state["cases"][case_id]
        writes = record["evaluation"]["operations"]
        creates = [row for row in writes if row["kind"] == "create" and row["state"] == "succeeded"]
        produced += len(creates)
        remaining = creates.copy()
        matched, date_ok = 0, True
        for wanted in EXPECTED.get(case_id, []):
            candidate = next((row for row in remaining if wanted[0] in actual(row)["title"]), None)
            date_expected += wanted[1] is not None
            if candidate:
                true_positive += 1
                matched += 1
                remaining.remove(candidate)
                ok = date_matches(candidate, wanted)
                dates_correct += wanted[1] is not None and ok
                date_ok &= ok
                # Only N10/N12 explicitly label event versus deadline in the
                # frozen reference. Do not invent labels for the other inputs.
                if case_id in ("N10", "N12"):
                    date_ok &= (
                        decoded(candidate["plan"])["normalized_date"]["time_kind"] == wanted[3]
                    )
            else:
                date_ok = False
        if not EXPECTED.get(case_id):
            unsafe += sum(row["attempt"] > 0 for row in writes)
        status = "字段通过 / 语义待复核"
        note = DEFECTS.get(case_id, "")
        if remaining or matched != len(EXPECTED.get(case_id, [])) or not date_ok:
            status = "fail"
        if note:
            status = "not_run" if case_id == "D19" else "fail"
        if case_id == "D09" and any(actual(row).get("priority") != 5 for row in creates):
            status, note = "fail", "最高优先级不一致。"
        if case_id == "D10":
            deletion = record["confirmation"]["operations"]
            valid = (
                not writes
                and len(deletion) == 1
                and deletion[0]["kind"] == "delete"
                and deletion[0]["state"] == "succeeded"
                and deletion[0]["attempt"] == 1
                and actual(deletion[0]).get("deleted") is True
                and actual(deletion[0]).get("exists") is False
            )
            status, note = (
                ("pass", "真实两轮：预览零写入，人工确认后一次删除，原生墓碑核验。")
                if valid
                else ("fail", "删除核验不完整。")
            )
        rows.append(
            f"| {case_id} | {len(EXPECTED.get(case_id, []))} | {len(creates)} | {status} | {note} |"
        )
        details.append({"id": case_id, "status": status, "note": note})
    summary = {
        "case_count": 50,
        "notification_count": 30,
        "expected_actions": expected_total,
        "saved_creates": produced,
        "matched_actions": true_positive,
        "extra_actions": produced - true_positive,
        "missing_actions": expected_total - true_positive,
        "precision": true_positive / produced,
        "recall": true_positive / expected_total,
        "expected_dated_actions": date_expected,
        "matched_dates": dates_correct,
        "date_match_rate_on_expected_actions": dates_correct / date_expected,
        "uncertain_cases_with_write_attempts": unsafe,
        "release_pass": False,
        "semantic_review_complete": False,
    }
    report = [
        "# 已审核语料的原生验收 V1（2026-10-08）",
        "",
        "用户认可的 50 条参考答案已冻结，30 条通知、20 条直接请求。使用独立授权 AstrBot 会话、DeepSeek Flash 和专用清单；先在原生会话交付共同身份条件，不改人格/长期记忆实现。工具说明与正式输入在本批期间保持不变。",
        "",
        "**结论：未通过。** 这是实际采集的 V1，失败没有删除或由调试结果替换；完整语义/回执逐条复核尚未完成。",
        "",
        f"- 参考行动 {expected_total} 项，实际创建 {produced} 项；正确行动 {true_positive}，额外 {produced - true_positive}，遗漏 {expected_total - true_positive}。",
        f"- 行动 precision {summary['precision']:.2%}（目标 ≥95%，未通过），recall {summary['recall']:.2%}（目标 ≥90%）。",
        f"- 应记录日期的 {date_expected} 项中 {dates_correct} 项匹配（{summary['date_match_rate_on_expected_actions']:.2%}）；此外 {unsafe} 项不应写入的输入产生了写入，不能用正确日期比例掩盖。关键不确定输入阻止写入的 100% 门槛未通过。N10/N12 明确标注的事件/截止分类另行核对，未给其他输入虚构类别标注。",
        "- D10 预览零写入；用户确认具体目标后只删除一次，原生 deleted=true 回读。准备 D10/D16 的三项夹具不计入行动统计，未清理其他测试任务。",
        "- N04 回执星期错误，N08 多问清单，N25 丢失端点原文；D19 缺少真实待取消本地计划，不计为已验收。其他额外追问、依据与回执仍需逐条复核，字段通过不等于完整场景通过。",
        "",
        f"冻结参考文件 SHA-256：`{state['frozen_sha256']}`。完整参数、目标/会话 ID、证据和回执位于 Git/Docker 排除的 runtime-data/native-corpus-v1-results.json。",
        "",
        "| 编号 | 参考新增数 | 实际新增数 | 当前状态 | 已发现问题 / 边界 |",
        "| --- | ---: | ---: | --- | --- |",
        *rows,
        "",
        "程序仅对参考行动名称、远端日期/时刻/全天和事件分类做可追溯字段比较；来源、格式、参与意愿、澄清质量与回执由逐条阅读核查，未引入模型评分器。修改工具说明后应新开 V2 重测全部 50 条，保留 V1，不拼接通过项。",
        "",
    ]
    (ROOT / "docs/acceptance/native-corpus-v1.md").write_text("\n".join(report), encoding="utf-8")
    (ROOT / "runtime-data/native-corpus-v1-grade.json").write_text(
        json.dumps({"summary": summary, "cases": details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
