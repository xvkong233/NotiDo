"""Use native conversation memory to separate clarification and new intent."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from tools import native_corpus_smoke as collector
from tools.container_smoke import PORT, PREFIX, ROOT, request, unwrap, wait_ready
from tools.native_performance_smoke import bind_session

JOURNAL = ROOT / "runtime-data/native-clarification-v7-results.json"
PENDING = "NotiDo 验收 · V7待澄清交材料"
INDEPENDENT = "NotiDo 验收 · V7独立买标签"


def main():
    if PORT != 16190:
        raise RuntimeError("reuse the sole current acceptance instance")
    token = wait_ready()
    settings = unwrap(request(PREFIX + "settings", token=token)[1])["settings"]
    project = json.loads((ROOT / "runtime-data/test-project.json").read_text(encoding="utf-8-sig"))
    if project["name"] != "NotiDo 验收" or settings["allowed_projects"] != [project["id"]]:
        raise RuntimeError("dedicated authorized test scope required")
    version = collector.runtime_version()
    collector.JOURNAL = JOURNAL
    if JOURNAL.exists():
        state = json.loads(JOURNAL.read_text(encoding="utf-8"))
    else:
        state = {**version, "record": {"id": "clarification-v7", "session": bind_session(token)}}
        collector.save(state)
    if any(state.get(k) != v for k, v in version.items()):
        raise RuntimeError("don't mix installed versions")
    record = state["record"]
    stages = [
        (
            "unclear",
            f"请在NotiDo 验收清单新建任务“{PENDING}”，7月16日14:20交材料。年份未知，需要你先向我询问年份，不要擅自填年，也不要先建无日期任务。",
        ),
        (
            "independent",
            f"先前交材料年份还没确认。现在是独立的新指令：在NotiDo 验收新建“{INDEPENDENT}”，买实验标签，这项没有日期。只做这项，不替交材料补年份或改日期。",
        ),
        (
            "clarified",
            "刚才交材料的年份是2027年，时刻仍为14:20；请补齐之前的交材料任务，不要再建一次买标签，也不要改买标签的无日期状态。",
        ),
    ]
    for label, message in stages:
        slot = collector.send_once(token, state, record, label, message)
        rows = slot["operations"]
        if label == "unclear":
            if rows:
                raise RuntimeError("unknown year produced a mutation")
        else:
            if (
                len(rows) != 1
                or rows[0]["kind"] != "create"
                or rows[0]["state"] != "succeeded"
                or rows[0]["attempt"] != 1
            ):
                raise RuntimeError("each distinct clarified intent must create exactly once")
            fields = json.loads(rows[0]["result"])["actual_fields"]
            wanted = INDEPENDENT if label == "independent" else PENDING
            if fields["title"] != wanted:
                raise RuntimeError("clarification selected another intent")
            if label == "independent" and fields.get("dueDate") is not None:
                raise RuntimeError("independent intent inherited the pending date")
            if label == "clarified":
                local = datetime.fromisoformat(fields["dueDate"].replace("Z", "+00:00")).astimezone(
                    ZoneInfo("Asia/Shanghai")
                )
                if (
                    local.strftime("%Y-%m-%d %H:%M") != "2027-07-16 14:20"
                    or fields.get("isAllDay") is not False
                ):
                    raise RuntimeError("clarification didn't preserve the original date and time")
        print(json.dumps({"stage": label, "writes": len(rows), "pass": True}), flush=True)
    group_ids = {
        value["group_id"]
        for label in ("unclear", "independent", "clarified")
        for name, value in collector.tool_results(record[label]["output"])
        if name == "notido_record_outcome"
    }
    if len(group_ids) != 3:
        raise RuntimeError("expected original, independent and clarification groups")
    persisted = {}
    for group_id in group_ids:
        result = unwrap(request(PREFIX + "notices/" + group_id, token=token)[1])
        persisted[group_id] = result["group"]["state"]
        if persisted[group_id] != "completed" or not result["native_outcome"]:
            raise RuntimeError("the original pending conclusion was not actually closed")
    state["persisted_group_states"] = persisted
    state["collected"] = True
    collector.save(state)


if __name__ == "__main__":
    main()
