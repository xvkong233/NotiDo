"""Journaled native daily/month-end/leap-year rule readbacks; never complete recurring tasks."""

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from notido.recurrence import canonical_rule
from tools.container_smoke import PREFIX, ROOT, request, unwrap, wait_ready
from tools.native_chat_smoke import stream
from tools.native_recurrence_delete_smoke import operations

JOURNAL = ROOT / "runtime-data/native-recurrence-matrix-results.json"
CASES = [
    {
        "id": "daily",
        "suffix": "每日间隔及完成起算",
        "description": "首次2027年12月21日08:10，之后从完成日起每2天重复，共4次",
        "first": "2027-12-21 08:10",
        "rule": "RRULE:FREQ=DAILY;INTERVAL=2;COUNT=4",
        "from": "1",
    },
    {
        "id": "monthly",
        "suffix": "月底及截止日起算",
        "description": "首次2027年12月31日09:05，每个月最后一天09:05，按截止日起算，共3次",
        "first": "2027-12-31 09:05",
        "rule": "RRULE:FREQ=MONTHLY;INTERVAL=1;BYMONTHDAY=-1;COUNT=3",
        "from": "0",
    },
    {
        "id": "yearly",
        "suffix": "闰年及结束日期",
        "description": "首次2028年2月29日10:15，每年2月29日10:15，按日历起算，结束日期为2036年2月29日（含当日）",
        "first": "2028-02-29 10:15",
        "rule": "RRULE:FREQ=YEARLY;INTERVAL=1;BYMONTHDAY=29;BYMONTH=2;UNTIL=20360229T155959Z",
        "from": "2",
    },
]


def save(state):
    temporary = JOURNAL.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, JOURNAL)


def main():
    token = wait_ready()
    project = json.loads((ROOT / "runtime-data/test-project.json").read_text(encoding="utf-8-sig"))
    settings = unwrap(request(PREFIX + "settings", token=token)[1])["settings"]
    if project["name"] != "NotiDo 验收" or settings["allowed_projects"] != [project["id"]]:
        raise RuntimeError("dedicated test scope required")
    state = (
        json.loads(JOURNAL.read_text(encoding="utf-8"))
        if JOURNAL.exists()
        else {
            "session": json.loads(
                (ROOT / "runtime-data/native-chat-results.json").read_text(encoding="utf-8")
            )["session"],
            "cases": {},
        }
    )
    for case in CASES:
        title = "NotiDo 验收 · 周期组合 · " + case["suffix"]
        record = state["cases"].setdefault(case["id"], {"phase": "ready", "title": title})
        if record["phase"] == "ready":
            record["phase"] = "request_sent"
            save(state)
            record["events"] = stream(
                token,
                state["session"],
                f"请在NotiDo 验收清单新建周期任务“{title}”：{case['description']}。备注“专用周期组合验收”，不要完成或删除。",
            )
            save(state)
        rows = operations(token, title)
        record["operations"] = rows
        save(state)
        if len(rows) != 1 or rows[0]["kind"] != "create" or rows[0]["state"] != "succeeded":
            raise RuntimeError(
                "recurring matrix not uniquely verified; inspect ledger without replay"
            )
        actual = json.loads(rows[0]["result"])["actual_fields"]
        local = datetime.fromisoformat(actual["dueDate"].replace("Z", "+00:00")).astimezone(
            ZoneInfo("Asia/Shanghai")
        )
        if local.strftime("%Y-%m-%d %H:%M") != case["first"] or actual.get("isAllDay") is not False:
            raise RuntimeError("first date/time mismatch")
        if (
            canonical_rule(actual.get("repeatFlag")) != canonical_rule(case["rule"])
            or str(actual.get("repeatFrom")) != case["from"]
        ):
            raise RuntimeError("recurrence rule/mode mismatch")
        if rows[0]["attempt"] != 1:
            raise RuntimeError("unexpected repeated write")
        record["phase"] = "verified"
        save(state)
    state["checks"] = [{"check": case["id"], "pass": True} for case in CASES]
    save(state)
    print(json.dumps(state["checks"]))


if __name__ == "__main__":
    main()
