"""Journaled native chat recurrence and deletion preview on the dedicated test list.

Deletion is resumed with --confirm only after the maintainer confirms the exact
previewed synthetic test task. Requests are never resent after interruptions.
"""

import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from notido.recurrence import canonical_rule
from tools.container_smoke import PREFIX, ROOT, request, unwrap, wait_ready
from tools.native_chat_smoke import stream

JOURNAL = ROOT / "runtime-data/native-recurrence-delete-results.json"
TITLE = "NotiDo 验收 · 每周周期与二次确认删除"


def save(state):
    staged = JOURNAL.with_suffix(".tmp")
    staged.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(staged, JOURNAL)


def operations(token, title=TITLE):
    result, cursor = [], None
    while True:
        endpoint = PREFIX + "operations?limit=100"
        if cursor:
            from urllib.parse import quote

            endpoint += "&cursor=" + quote(cursor)
        page = unwrap(request(endpoint, token=token)[1])
        for item in page["items"]:
            row = unwrap(request(PREFIX + f"operations/{item['id']}", token=token)[1])
            row = row.get("operation", row)
            plan = json.loads(row["plan"]) if isinstance(row["plan"], str) else row["plan"]
            if plan.get("fields", {}).get("title", "").startswith(title) or plan.get(
                "before", {}
            ).get("title", "").startswith(title):
                result.append({**row, "plan": plan})
        cursor = page.get("next_cursor")
        if not cursor:
            return result


def main(confirm=False, check=False):
    token = wait_ready()
    project = json.loads((ROOT / "runtime-data/test-project.json").read_text(encoding="utf-8-sig"))
    settings = unwrap(request(PREFIX + "settings", token=token)[1])["settings"]
    if project["name"] != "NotiDo 验收" or settings["allowed_projects"] != [project["id"]]:
        raise RuntimeError("dedicated authorized test list required")
    state = (
        json.loads(JOURNAL.read_text(encoding="utf-8"))
        if JOURNAL.exists()
        else {
            "phase": "ready",
            "title": TITLE,
            "session": json.loads(
                (ROOT / "runtime-data/native-chat-results.json").read_text(encoding="utf-8")
            )["session"],
        }
    )
    if state["phase"] == "ready":
        state["phase"] = "create_request_sent"
        save(state)
        state["create_events"] = stream(
            token,
            state["session"],
            f"请在NotiDo 验收清单新建周期任务“{TITLE}”。首次为2027年12月20日17:00，此后每周一17:00，按日历重复，共5次；备注是专用周期及删除验收，请不要完成或删除。",
        )
        state["operations"] = operations(token)
        save(state)
        creates = [row for row in state["operations"] if row["kind"] == "create"]
        if len(creates) != 1 or creates[0]["state"] != "succeeded":
            raise RuntimeError(
                "native recurring creation not uniquely verified; inspect journal before continuing"
            )
        actual = json.loads(creates[0]["result"])["actual_fields"]
        local = datetime.fromisoformat(actual["dueDate"].replace("Z", "+00:00")).astimezone(
            ZoneInfo("Asia/Shanghai")
        )
        if (
            local.strftime("%Y-%m-%d %H:%M") != "2027-12-20 17:00"
            or actual.get("isAllDay") is not False
        ):
            raise RuntimeError("first recurring deadline mismatch")
        if (
            canonical_rule(actual.get("repeatFlag"))
            != canonical_rule("RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=MO;COUNT=5")
            or str(actual.get("repeatFrom")) != "2"
        ):
            raise RuntimeError("native recurrence rule/readback mismatch")
        state["remote_id"] = creates[0]["remote_id"]
        state["phase"] = "created"
        save(state)
    if state["phase"] == "created":
        state["phase"] = "preview_request_sent"
        save(state)
        state["preview_events"] = stream(
            token,
            state["session"],
            f"我要删除刚创建的“{TITLE}”，包含这个周期任务及其未来安排。请先查询唯一目标并给出二次确认预览，等我后续明确确认后再删除。",
        )
        state["operations"] = operations(token)
        save(state)
        if any(row["kind"] == "delete" for row in state["operations"]):
            raise RuntimeError("delete must not run during preview")
        state["phase"] = "awaiting_confirmation"
        state["checks"] = [
            {"check": "native_recurring_first_deadline_and_rrule_readback", "pass": True},
            {"check": "native_deletion_preview_no_delete_write", "pass": True},
        ]
        save(state)
    if confirm and state["phase"] == "awaiting_confirmation":
        state["phase"] = "confirm_request_sent"
        save(state)
        state["confirm_events"] = stream(token, state["session"], "确认删除")
        state["operations"] = operations(token)
        save(state)
    if check and state["phase"] == "confirm_request_sent":
        state["check_events"] = stream(
            token,
            state["session"],
            "请只用 notido_check 核查刚才已提交的删除操作，确认原生删除状态；不得新建、重建或再次发送删除。",
        )
        state["operations"] = operations(token)
        save(state)
    if state["phase"] == "confirm_request_sent":
        deletes = [row for row in state["operations"] if row["kind"] == "delete"]
        if (
            len(deletes) != 1
            or deletes[0]["state"] != "succeeded"
            or deletes[0]["remote_id"] != state["remote_id"]
        ):
            raise RuntimeError("confirmed deletion not verified; inspect journal without replay")
        if not json.loads(deletes[0]["result"])["actual_fields"].get("deleted"):
            raise RuntimeError("deletion absence proof missing")
        if any(row["attempt"] != 1 for row in state["operations"]):
            raise RuntimeError("test writes must each execute only once")
        state["checks"].append(
            {"check": "second_message_delete_exact_target_and_absence_proof", "pass": True}
        )
        state["phase"] = "deleted"
        save(state)
    print(json.dumps({"phase": state["phase"], "checks": state.get("checks", [])}))


if __name__ == "__main__":
    main(confirm=sys.argv[1:] == ["--confirm"], check=sys.argv[1:] == ["--check"])
