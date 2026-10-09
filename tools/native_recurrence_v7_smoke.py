"""Current native recurring readbacks on four independent synthetic tasks."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from notido.recurrence import canonical_rule
from tools import native_corpus_smoke as collector
from tools.container_smoke import PORT, PREFIX, ROOT, request, unwrap, wait_ready
from tools.native_performance_smoke import bind_session
from tools.native_recurrence_matrix import CASES

JOURNAL = ROOT / "runtime-data/native-recurrence-v7-results.json"


def main():
    if PORT != 16190:
        raise RuntimeError("reuse the sole current acceptance instance")
    token = wait_ready()
    project = json.loads((ROOT / "runtime-data/test-project.json").read_text(encoding="utf-8-sig"))
    settings = unwrap(request(PREFIX + "settings", token=token)[1])["settings"]
    if project["name"] != "NotiDo 验收" or settings["allowed_projects"] != [project["id"]]:
        raise RuntimeError("dedicated authorized test scope required")
    version = collector.runtime_version()
    collector.JOURNAL = JOURNAL
    if JOURNAL.exists():
        state = json.loads(JOURNAL.read_text(encoding="utf-8"))
    else:
        state = {**version, "cases": {}}
        collector.save(state)
    if any(state.get(k) != v for k, v in version.items()):
        raise RuntimeError("don't mix installed versions")
    cases = [
        {
            "id": "weekly",
            "suffix": "每周一及次数",
            "description": "首次2027年12月20日17:00，此后每周一17:00，按日历起算，共5次",
            "first": "2027-12-20 17:00",
            "rule": "RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=MO;COUNT=5",
            "from": "2",
        },
        *CASES,
    ]
    for case in cases:
        record = state["cases"].setdefault(case["id"], {"id": case["id"]})
        if "session" not in record:
            record["session"] = bind_session(token)
            collector.save(state)
        title = "NotiDo 验收 · V7周期 · " + case["suffix"]
        slot = collector.send_once(
            token,
            state,
            record,
            "creation",
            f"请在NotiDo 验收清单新建独立周期任务“{title}”：{case['description']}。备注“专用V7周期验收”；只创建这一项，不修改历史任务，不完成或删除。",
        )
        rows = slot["operations"]
        if (
            len(rows) != 1
            or rows[0]["kind"] != "create"
            or rows[0]["state"] != "succeeded"
            or rows[0]["attempt"] != 1
        ):
            raise RuntimeError("exactly one verified create required; inspect without replay")
        fields = json.loads(rows[0]["result"])["actual_fields"]
        local = datetime.fromisoformat(fields["dueDate"].replace("Z", "+00:00")).astimezone(
            ZoneInfo("Asia/Shanghai")
        )
        if (
            fields["title"] != title
            or local.strftime("%Y-%m-%d %H:%M") != case["first"]
            or fields.get("isAllDay") is not False
        ):
            raise RuntimeError("first occurrence or title mismatch")
        if (
            canonical_rule(fields.get("repeatFlag")) != canonical_rule(case["rule"])
            or str(fields.get("repeatFrom")) != case["from"]
        ):
            raise RuntimeError("native repeat rule or mode mismatch")
        record["verified"] = True
        collector.save(state)
        print(json.dumps({"case": case["id"], "pass": True}), flush=True)
    state["collected"] = True
    collector.save(state)


if __name__ == "__main__":
    main()
