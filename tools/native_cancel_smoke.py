"""Actual AstrBot chat cancellation of a paused local operation, keeping Dida task."""

import json
import os
import subprocess
import uuid

from tools.container_smoke import PREFIX, ROOT, request, unwrap, wait_ready
from tools.native_corpus_smoke import new_operations, send_once
from tools.native_performance_smoke import bind_session

JOURNAL = ROOT / "runtime-data/native-cancel-results.json"


def save(state):
    staged = JOURNAL.with_suffix(".tmp")
    staged.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(staged, JOURNAL)


def main():
    # Shared journal-before-send primitive, with this independent journal path.
    from tools import native_corpus_smoke as collector

    collector.JOURNAL = JOURNAL
    token = wait_ready()
    project = json.loads((ROOT / "runtime-data/test-project.json").read_text(encoding="utf-8-sig"))
    settings = unwrap(request(PREFIX + "settings", token=token)[1])["settings"]
    if project["name"] != "NotiDo 验收" or settings["allowed_projects"] != [project["id"]]:
        raise RuntimeError("dedicated authorized test scope required")
    state = (
        json.loads(JOURNAL.read_text(encoding="utf-8"))
        if JOURNAL.exists()
        else {"id": "D19", "phase": "ready"}
    )
    if state["phase"] == "ready":
        state["session"] = bind_session(token)
        state["phase"] = "bound"
        save(state)
    saved = send_once(
        token,
        state,
        state,
        "saved_fixture",
        "在NotiDo 验收清单新建无日期任务“NotiDo 验收 · D19 · 保留已保存结果”。只建这一项，不完成、不删除。",
    )
    if (
        len(saved["operations"]) != 1
        or saved["operations"][0]["kind"] != "create"
        or saved["operations"][0]["state"] != "succeeded"
    ):
        raise RuntimeError("single saved fixture not verified")
    saved_row = saved["operations"][0]
    if "pending_operation" not in state:
        state.setdefault("fixture_request_id", str(uuid.uuid4()))
        save(state)
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                "-w",
                "/opt/notido-plugin",
                "notido-acceptance",
                "python",
                "-m",
                "tools.native_cancel_fixture",
            ],
            input=json.dumps(
                {"saved_operation_id": saved_row["id"], "request_id": state["fixture_request_id"]}
            ).encode(),
            capture_output=True,
            timeout=30,
        )
        if result.returncode:
            raise RuntimeError("paused local fixture failed; no remote retry")
        state["pending_operation"] = json.loads(result.stdout)["id"]
        state["phase"] = "staged"
        save(state)
    evaluated = send_once(
        token, state, state, "evaluation", "取消本次尚未开始的本地计划，不撤销已经保存的远端任务。"
    )
    # Local cancellation creates no new remote-operation record.
    writes = new_operations(token, evaluated["before"])
    pending = unwrap(request(PREFIX + f"operations/{state['pending_operation']}", token=token)[1])
    existing = unwrap(request(PREFIX + f"operations/{saved_row['id']}", token=token)[1])
    selected = send_once(
        token,
        state,
        state,
        "readback",
        "查询标题含“NotiDo 验收 · D19 · 保留已保存结果”的未完成任务。只查询，别修改、完成或删除。",
    )
    task_visible = False
    for entry in selected["output"]["tools"]:
        if entry["kind"] != "tool_call_result":
            continue
        value = entry["payload"].get("result")
        try:
            value = json.loads(value) if isinstance(value, str) else value
        except ValueError:
            continue
        if isinstance(value, dict) and any(
            task.get("id") == saved_row["remote_id"] and task.get("status") == 0
            for task in value.get("tasks", [])
        ):
            task_visible = True
    state["checks"] = [
        {
            "check": "actual_paused_local_operation_cancelled_without_attempt",
            "pass": pending["state"] == "cancelled"
            and pending["attempt"] == 0
            and pending["remote_id"] is None,
        },
        {
            "check": "native_cancel_no_remote_write",
            "pass": not writes and not selected["operations"],
        },
        {
            "check": "saved_operation_and_actual_dida_task_preserved",
            "pass": existing["state"] == "succeeded"
            and existing["remote_id"] == saved_row["remote_id"]
            and task_visible,
        },
    ]
    state["phase"] = "verified" if all(c["pass"] for c in state["checks"]) else "failed"
    save(state)
    print(json.dumps(state["checks"]))
    if state["phase"] != "verified":
        raise RuntimeError("native cancellation evidence incomplete; inspect without replay")


if __name__ == "__main__":
    main()
