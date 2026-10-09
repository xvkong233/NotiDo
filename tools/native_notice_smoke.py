"""Normal AstrBot conversation verifies native notification lifecycle on the test list."""

import json
import os

from tools.container_smoke import PREFIX, ROOT, request, unwrap, wait_ready
from tools.native_chat_smoke import stream

JOURNAL = ROOT / "runtime-data/native-notice-results.json"
TITLE = "NotiDo 验收 · 原生通知去重与延期"


def save(state):
    staged = JOURNAL.with_suffix(".tmp")
    staged.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(staged, JOURNAL)


def operations(token):
    result, cursor = [], None
    while True:
        endpoint = PREFIX + "operations?limit=100"
        if cursor:
            from urllib.parse import quote

            endpoint += "&cursor=" + quote(cursor)
        page = unwrap(request(endpoint, token=token)[1])
        for item in page["items"]:
            detail = unwrap(request(PREFIX + f"operations/{item['id']}", token=token)[1])
            row = detail.get("operation", detail)
            plan = json.loads(row["plan"]) if isinstance(row["plan"], str) else row["plan"]
            if (
                plan.get("fields", {}).get("title") == TITLE
                or plan.get("before", {}).get("title") == TITLE
            ):
                result.append({**row, "plan": plan})
        cursor = page.get("next_cursor")
        if not cursor:
            return result


def main():
    token = wait_ready()
    settings = unwrap(request(PREFIX + "settings", token=token)[1])["settings"]
    project = json.loads((ROOT / "runtime-data/test-project.json").read_text(encoding="utf-8-sig"))
    if settings["allowed_projects"] != [project["id"]] or project["name"] != "NotiDo 验收":
        raise RuntimeError("dedicated authorized test list required")
    state = (
        json.loads(JOURNAL.read_text(encoding="utf-8"))
        if JOURNAL.exists()
        else {
            "session": json.loads(
                (ROOT / "runtime-data/native-chat-results.json").read_text(encoding="utf-8")
            )["session"],
            "phase": "ready",
            "title": TITLE,
        }
    )
    notification = (
        "通知：材料学院2024级班委必须于2027年12月27日15:30提交验收实验报告，要求PDF格式、文件名ND-NOTICE-01、发给验收辅导员。"
        f"按当前会话身份自动处理，任务标题用“{TITLE}”，只使用NotiDo 验收清单。"
    )
    stages = [
        ("ready", "created", notification),
        ("created", "duplicated", notification),
        (
            "duplicated",
            "postponed",
            f"最新通知：上述验收实验报告延期至2027年12月28日18:10，PDF格式、文件名和提交渠道仍不变。我明确确认这是最新发布的通知。请更新“{TITLE}”既有任务，不新建；保留其他要求和用户备注。",
        ),
        ("postponed", "completed", f"我已完成“{TITLE}”，请查询这条真实任务并标记完成。"),
    ]
    for before, after, message in stages:
        if state["phase"] != before:
            continue
        state["phase"] = after + "_request_sent"
        save(state)
        state[after + "_events"] = stream(token, state["session"], message)
        state["operations"] = operations(token)
        save(state)
        creates = [row for row in state["operations"] if row["kind"] == "create"]
        if len(creates) != 1 or creates[0]["state"] != "succeeded":
            raise RuntimeError("create not uniquely verified; journaled request must not be resent")
        if not creates[0]["plan"].get("notice_id"):
            raise RuntimeError(
                "AstrBot did not use notification provenance contract; inspect journal"
            )
        if after == "postponed":
            updates = [row for row in state["operations"] if row["kind"] == "update"]
            if len(updates) != 1 or updates[0]["state"] != "succeeded":
                raise RuntimeError(
                    "postponement not verified; inspect journal, never blindly retry"
                )
        if after == "completed":
            completed = [row for row in state["operations"] if row["kind"] == "complete"]
            if len(completed) != 1 or completed[0]["state"] != "succeeded":
                raise RuntimeError("completion not verified; inspect journal")
        state["phase"] = after
        save(state)
    if state["phase"] != "completed":
        raise RuntimeError(
            "interrupted stage is journaled; inspect existing ledger before continuing"
        )
    rows = state["operations"]
    create = next(row for row in rows if row["kind"] == "create")
    update = next(row for row in rows if row["kind"] == "update")
    complete = next(row for row in rows if row["kind"] == "complete")
    actual = json.loads(update["result"])["actual_fields"]
    from datetime import datetime
    from zoneinfo import ZoneInfo

    local = datetime.fromisoformat(actual["dueDate"].replace("Z", "+00:00")).astimezone(
        ZoneInfo("Asia/Shanghai")
    )
    if local.strftime("%Y-%m-%d %H:%M") != "2027-12-28 18:10" or not all(
        value in actual["content"] for value in ("PDF", "ND-NOTICE-01", "验收辅导员")
    ):
        raise RuntimeError("updated deadline/requirements mismatch")
    if json.loads(complete["result"])["actual_fields"].get("status") != 2:
        raise RuntimeError("completion readback mismatch")
    if not (create["remote_id"] == update["remote_id"] == complete["remote_id"]):
        raise RuntimeError("notification lifecycle unexpectedly switched tasks")
    state["checks"] = [
        {"check": "native_notice_evidence_and_managed_requirements", "pass": True},
        {"check": "repeated_notice_no_duplicate_create", "pass": True},
        {"check": "explicit_latest_postponement_same_task", "pass": True},
        {"check": "native_query_update_complete_real_readback", "pass": True},
        {"check": "all_writes_single_attempt", "pass": all(row["attempt"] == 1 for row in rows)},
    ]
    save(state)
    print(json.dumps(state["checks"]))


if __name__ == "__main__":
    main()
