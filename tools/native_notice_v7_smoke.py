"""Current native multi-revision conversation; journal before every send."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from tools import native_corpus_smoke as collector
from tools.container_smoke import PORT, PREFIX, ROOT, request, unwrap, wait_ready
from tools.native_performance_smoke import bind_session

JOURNAL = ROOT / "runtime-data/native-notice-v7-results.json"
TITLE = "NotiDo 验收 · V7多次延期报告"


def actual(row):
    return json.loads(row["result"])["actual_fields"]


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
        state = {**version, "record": {"id": "notice-v7", "session": bind_session(token)}}
        collector.save(state)
    if any(state.get(k) != v for k, v in version.items()):
        raise RuntimeError("don't mix installed versions")
    record = state["record"]
    notice = (
        "通知：材料学院2024级全体学生必须于2027年12月20日15:30提交V7多次延期报告，"
        "要求PDF格式、文件名ND-V7-NOTICE、发给验收辅导员。"
        f"请自动处理本人必做事项，任务标题完整用“{TITLE}”，清单NotiDo 验收。"
    )
    stages = [
        (
            "memory",
            "我是材料学院2024级学生，只使用NotiDo 验收清单。现在只记住身份，不查询或写入任务。",
        ),
        ("created", notice),
        ("repeated", notice),
        (
            "postponed1",
            f"最新通知：V7多次延期报告延期至2027年12月22日17:10，PDF、文件名和渠道不变。我确认是最新发布的通知；请只更新既有“{TITLE}”，不新建，保留用户备注。",
        ),
        (
            "postponed2",
            f"第二次最新通知：V7多次延期报告再延期至2027年12月24日18:20，其他要求不变。我确认是比上次更晚发布的通知；请只更新同一个“{TITLE}”，不新建，保留用户备注。",
        ),
        ("completed", f"我已完成“{TITLE}”，请核对唯一真实任务后标记完成。"),
        (
            "after_completion",
            "又收到延期通知：V7多次延期报告截止改为2027年12月26日10:00。请核对既有任务；若已完成，不自动重开或创建替代任务。",
        ),
    ]
    for label, message in stages:
        slot = collector.send_once(token, state, record, label, message)
        rows = slot["operations"]
        if label in ("memory", "repeated", "after_completion"):
            if rows:
                raise RuntimeError(f"{label}: unexpected mutation, preserve evidence")
        else:
            expected = (
                "create" if label == "created" else "complete" if label == "completed" else "update"
            )
            if (
                len(rows) != 1
                or rows[0]["kind"] != expected
                or rows[0]["state"] != "succeeded"
                or rows[0]["attempt"] != 1
            ):
                raise RuntimeError(
                    f"{label}: unique verified {expected} required, inspect without replay"
                )
            if label == "created":
                row = rows[0]
                if actual(row)["title"] != TITLE or not row["plan"].get("notice_id"):
                    raise RuntimeError("initial title and provenance required")
                state["remote_id"] = row["remote_id"]
                state["action_id"] = row["action_id"]
                collector.save(state)
            elif rows[0]["remote_id"] != state["remote_id"]:
                raise RuntimeError("revision or completion switched targets")
            if label.startswith("postponed"):
                row = rows[0]
                if row["action_id"] != state["action_id"]:
                    raise RuntimeError("notice revision lost the original action identity")
                fields = actual(row)
                local = datetime.fromisoformat(fields["dueDate"].replace("Z", "+00:00")).astimezone(
                    ZoneInfo("Asia/Shanghai")
                )
                wanted = "2027-12-22 17:10" if label == "postponed1" else "2027-12-24 18:20"
                if (
                    local.strftime("%Y-%m-%d %H:%M") != wanted
                    or fields.get("isAllDay") is not False
                ):
                    raise RuntimeError("notice revision deadline mismatch")
                if not all(
                    value in fields["content"] for value in ("PDF", "ND-V7-NOTICE", "验收辅导员")
                ):
                    raise RuntimeError("revision dropped submission requirements")
            if label == "completed" and actual(rows[0]).get("status") != 2:
                raise RuntimeError("completed status not actually verified")
        print(json.dumps({"stage": label, "writes": len(rows), "pass": True}), flush=True)
    state["collected"] = True
    collector.save(state)


if __name__ == "__main__":
    main()
