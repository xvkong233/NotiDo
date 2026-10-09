"""Report current tool readbacks separately from new writes; no semantic grader."""

import argparse
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.native_corpus_report import EXPECTED
from tools.native_corpus_smoke import FROZEN, ROOT, returned_evidence

JOURNAL = ROOT / "runtime-data/native-corpus-v2-results.json"


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def candidates(slot):
    tasks = {}
    for row in slot.get("operations", []):
        if row["kind"] == "create" and row["state"] == "succeeded":
            tasks[row["remote_id"]] = {
                "actual_fields": decoded(row["result"])["actual_fields"],
                "mode": "new",
                "plan": decoded(row["plan"]),
            }
    for reference in slot.get("returned_operations", []):
        row = reference["operation"]
        result = reference["current_result"]
        if row["kind"] == "create" and result.get("actual_fields"):
            tasks[row["remote_id"]] = {
                "actual_fields": result["actual_fields"],
                "mode": "new" if reference["new_in_request"] else "reused",
                "plan": decoded(row["plan"]),
            }
    output_hash = hashlib.sha256(
        json.dumps(slot.get("output", {}), ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    for readback in slot.get("verified_notice_query_readbacks", []):
        if readback["source_output_sha256"] != output_hash:
            raise RuntimeError("captured query metadata belongs to another output")
        row = readback["operation"]
        if row["kind"] == "create" and row["state"] == "succeeded":
            tasks.setdefault(
                row["remote_id"],
                {
                    "actual_fields": readback["current_actual_fields"],
                    "mode": "query",
                    "plan": decoded(row["plan"]),
                },
            )
    _, observed_tasks = returned_evidence(slot.get("output", {}))
    for observed in observed_tasks:
        tasks.setdefault(observed["actual_fields"]["id"], {**observed, "mode": "query"})
    return list(tasks.values())


def date_matches(fields, wanted):
    _, day, clock, _ = wanted
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


def main(batch="v2"):
    journal = ROOT / f"runtime-data/native-corpus-{batch}-results.json"
    state = json.loads(journal.read_text(encoding="utf-8"))
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    review_path = ROOT / f"docs/acceptance/corpus-{batch}-semantic-review.json"
    review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
    if review and any(
        review.get(name) != state[name]
        for name in ("main_sha256", "frozen_sha256", "runtime_code_sha256")
        if name in state
    ):
        raise RuntimeError("semantic inspection belongs to another implementation/reference batch")
    details, table = [], []
    matched = dated = correct_dates = new_count = reused_count = queried_count = extra = unsafe = 0
    for case in frozen["cases"]:
        case_id = case["id"]
        record = state["cases"].get(case_id, {})
        slot = record.get("evaluation", {})
        semantic = review.get("cases", {}).get(case_id, {})
        actual = candidates(slot)
        new = sum(task["mode"] == "new" for task in actual)
        new_count += new
        wanted = EXPECTED.get(case_id, [])
        remaining = actual.copy()
        hits = 0
        fields_ok = True
        for expected in wanted:
            dated += expected[1] is not None
            candidate = next(
                (
                    task
                    for task in remaining
                    if expected[0] in task["actual_fields"]["title"]
                    or semantic.get("action_title_matches", {}).get(expected[0])
                    == task["actual_fields"]["title"]
                ),
                None,
            )
            if candidate is None:
                fields_ok = False
                continue
            remaining.remove(candidate)
            matched += 1
            hits += 1
            reused_count += candidate["mode"] == "reused"
            queried_count += candidate["mode"] == "query"
            correct = date_matches(candidate["actual_fields"], expected)
            correct_dates += expected[1] is not None and correct
            fields_ok &= correct
            if case_id == "D09":
                fields_ok &= candidate["actual_fields"].get("priority") == 5
            if case_id in {"N10", "N12"}:
                fields_ok &= (
                    candidate.get("plan", {}).get("normalized_date", {}).get("time_kind")
                    == expected[3]
                )
        extra += sum(task["mode"] == "new" for task in remaining)
        if not wanted:
            unsafe += sum(row["attempt"] > 0 for row in slot.get("operations", []))
        fields_ok &= hits == len(wanted) and not any(task["mode"] == "new" for task in remaining)
        if case_id == "D19":
            fields_ok &= bool(record.get("cancel_checks")) and all(record["cancel_checks"].values())
        if case_id == "D10":
            deletion = record.get("confirmation", {}).get("operations", [])
            fields_ok &= (
                not slot.get("operations")
                and len(deletion) == 1
                and all(
                    row["kind"] == "delete"
                    and row["state"] == "succeeded"
                    and row["attempt"] == 1
                    and decoded(row["result"])["actual_fields"].get("deleted") is True
                    and decoded(row["result"])["actual_fields"].get("exists") is False
                    for row in deletion
                )
            )
        if semantic and hashlib.sha256(
            json.dumps(slot.get("output"), ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest() != semantic.get("evaluation_sha256"):
            raise RuntimeError(f"{case_id} output changed after semantic inspection")
        if (
            semantic.get("confirmation_sha256")
            and hashlib.sha256(
                json.dumps(
                    record.get("confirmation", {}).get("output"), ensure_ascii=False, sort_keys=True
                ).encode()
            ).hexdigest()
            != semantic["confirmation_sha256"]
        ):
            raise RuntimeError(f"{case_id} confirmation changed after semantic inspection")
        status = (
            "not_run"
            if not slot
            else "pending"
            if record.get("phase") != "collected"
            else (
                "fail"
                if not fields_ok or semantic.get("status") == "fail"
                else "pass"
                if semantic.get("status") == "pass"
                else "语义待复核"
            )
        )
        note = semantic.get("note", "")
        details.append(
            {
                "id": case_id,
                "new": new,
                "matched": hits,
                "fields_pass": bool(fields_ok),
                "status": status,
                "note": note,
            }
        )
        table.append(f"| {case_id} | {len(wanted)} | {new} | {hits} | {status} | {note} |")
    summary = {
        "expected_actions": sum(map(len, EXPECTED.values())),
        "matched_actions": matched,
        "new_creates": new_count,
        "matched_reused_operations": reused_count,
        "matched_query_readbacks": queried_count,
        "extra_new_actions": extra,
        "expected_dated_actions": dated,
        "matched_dates": correct_dates,
        "uncertain_cases_with_write_attempts": unsafe,
        "collected": sum(r.get("phase") == "collected" for r in state["cases"].values()),
        "release_pass": False,
        "semantic_review_complete": bool(review.get("complete")),
        "corpus_pass": all(d["status"] == "pass" for d in details) and len(details) == 50,
    }
    lines = [
        f"# 冻结语料完整复测 {batch.upper()}（{state.get('date', '2026-10-08')}）",
        "",
        "保留 V1 的原始失败，使用同一份用户认可的 50 条输入与参考答案、新授权会话和固定实现。复测在已有专用清单中进行，必须区分新增写入与相同通知的复用。只以本次工具的成功结果或来源关联的精确 task_id 实时查询作为字段证据；历史 known_actions 快照不计回读。",
        "",
        "**本版尚未通过完整发布验收。** 下表字段核查与逐条语义阅读分别记录，不能用历史任务字段正确掩盖错误回执、缺来源或额外追问。",
        "",
        f"- 已完整采集 {summary['collected']}/50 条；参考行动 {summary['expected_actions']} 项，本次回读匹配 {matched} 项。",
        f"- 本次新增 {new_count} 项；匹配的复用操作回读 {reused_count} 项、来源关联查询回读 {queried_count} 项；额外新增 {extra} 项。",
        f"- 参考日期 {dated} 项，匹配 {correct_dates} 项；不应写入输入的实际写尝试 {unsafe} 项。",
        (
            "- 新增和复用不能混作全部重新创建的准确率样本。依据和回执要求仍须100%通过。D10测试沿用具体目标预览、后续消息确认两阶段；2026-10-09用户授权开发/测试删除无需逐项询问，正式用户任务仍需二次确认。D19为明确标记夹具，保留已保存结果。"
            if batch in ("v8", "v9", "v10", "v11", "v12", "v13", "v14")
            else "- 新增和复用不能混作全部重新创建的准确率样本。即使字段满足门槛，依据和回执要求仍须 100% 通过。D10 新目标必须获本次实际预览的人工确认；D19 使用标明为夹具的暂停操作，保留既有真实任务。"
        ),
        "- 参考行动名称按字段核对；同义标题须逐条阅读确认后，以绑定实际输出hash的精确标题映射记录，不作模糊关键词自动评分。",
        "",
        f"main.py SHA-256：`{state['main_sha256']}`；冻结参考 SHA-256：`{state['frozen_sha256']}`。参数、任务/会话 ID、原始工具结果与失败均仅保留在被 Git/Docker 排除的 runtime-data/native-corpus-{batch}-results.json。",
        f"安装代码 SHA-256：`{state.get('runtime_code_sha256', '该历史批次未记录')}`。",
        "",
        "| 编号 | 参考行动 | 本次新增 | 实时匹配 | 状态 | 语义阅读备注 |",
        "| --- | ---: | ---: | ---: | --- | --- |",
        *table,
        "",
    ]
    (ROOT / f"docs/acceptance/native-corpus-{batch}.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    (ROOT / f"runtime-data/native-corpus-{batch}-grade.json").write_text(
        json.dumps({"summary": summary, "cases": details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--batch",
        choices=["v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14"],
        default="v2",
    )
    main(parser.parse_args().batch)
