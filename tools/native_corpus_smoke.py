"""Collect independent native outputs against the maintainer-frozen 50 cases.

No heuristic model grader. Sends each case once, keeps all tool calls and actual
ledger evidence, and leaves semantic grading for review. D10's second message
requires a separate confirmation of its actual synthetic target.
"""

import argparse
import hashlib
import json
import os
import subprocess
import uuid
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from tools.container_smoke import PREFIX, ROOT, request, unwrap, wait_ready
from tools.native_performance_smoke import bind_session, timed_stream

FROZEN = ROOT / "docs/acceptance/corpus-frozen-v1.json"
JOURNAL = ROOT / "runtime-data/native-corpus-v1-results.json"
CONTAINER = os.environ.get("NOTIDO_SMOKE_CONTAINER", "notido-acceptance")
if CONTAINER not in (
    "notido-acceptance",
    "notido-native-v3",
    "notido-native-v4",
    "notido-native-v5",
    "notido-native-v6",
    "notido-native-v7",
):
    raise RuntimeError("an isolated acceptance container is required")


def runtime_version():
    # Inspect the installed plugin, not the editable workspace. This permits
    # preparing fixes while an earlier version awaits its deletion confirmation.
    code = (
        "import hashlib,json; from pathlib import Path; "
        "p=Path('/AstrBot/data/plugins/astrbot_plugin_notido'); "
        "files=sorted([p/'main.py',*(p/'notido').rglob('*.py')]); "
        "print(json.dumps({'main_sha256':hashlib.sha256((p/'main.py').read_bytes()).hexdigest(),"
        "'runtime_code_sha256':hashlib.sha256(b''.join(str(f.relative_to(p)).encode()+b'\\0'+"
        "hashlib.sha256(f.read_bytes()).digest() for f in files)).hexdigest()}))"
    )
    result = subprocess.run(
        ["docker", "exec", CONTAINER, "python", "-c", code],
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError("cannot inspect installed acceptance implementation")
    return json.loads(result.stdout)


def tool_results(output):
    """Associate actual results with their calls; never grade tool arguments."""
    calls = {}
    for entry in output.get("tools", []):
        payload = entry["payload"]
        if entry["kind"] == "tool_call":
            calls[payload["id"]] = payload.get("name")
            continue
        if entry["kind"] != "tool_call_result":
            continue
        value = payload.get("result")
        try:
            if isinstance(value, str):
                # AstrBot appends its image cache descriptions to MCP text.
                # Decode only the leading tool manifest; never interpret the
                # appended prose as additional tool evidence or instructions.
                value = (
                    json.JSONDecoder().raw_decode(value.lstrip())[0]
                    if calls.get(payload["id"]) == "notido_materials"
                    else json.loads(value)
                )
        except ValueError:
            continue
        if isinstance(value, dict) and not value.get("error"):
            yield calls.get(payload["id"]), value


def returned_evidence(output):
    """Only current successful tool results count as readback evidence.

    A historical materials snapshot alone cannot prove that a task still exists.
    Queries may fulfil a repeated notice only by the exact source-linked task ID.
    """
    references, known, queried, notice_refs = {}, {}, {}, set()
    for name, value in tool_results(output):
        if name in {"notido_create", "notido_update", "notido_complete", "notido_check"}:
            if value.get("state") == "succeeded" and value.get("operation_id"):
                references[value["operation_id"]] = value
        if name == "notido_materials":
            if value.get("notice_ref"):
                notice_refs.add(value["notice_ref"])
            for action in value.get("known_actions", []):
                known[action["task_id"]] = action
        if name == "notido_query":
            for task in value.get("tasks", []):
                queried[task["id"]] = task
    observed = [
        {"actual_fields": queried[task_id], "source_action": action}
        for task_id, action in known.items()
        if task_id in queried
    ]
    # A restored/empty local ledger may lack its prior links. A complete exact
    # managed marker returned by the real query is still source-associated
    # remote evidence; never accept a same-title task or an arbitrary substring.
    from notido.errors import NotiDoError
    from notido.policy import managed_region

    for task_id, task in queried.items():
        if task_id in known:
            continue
        for notice_ref in notice_refs:
            try:
                managed_region(task.get("content", ""), notice_ref)
            except NotiDoError:
                continue
            observed.append(
                {
                    "actual_fields": task,
                    "source_action": {
                        "notice_ref": notice_ref,
                        "task_id": task_id,
                        "association": "exact_remote_managed_marker",
                    },
                }
            )
            break
    return references, observed


def save(state):
    staged = JOURNAL.with_suffix(".tmp")
    staged.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(staged, JOURNAL)


def ledger_ids(token):
    ids, cursor = set(), None
    while True:
        endpoint = PREFIX + "operations?limit=100"
        if cursor:
            endpoint += "&cursor=" + quote(cursor)
        page = unwrap(request(endpoint, token=token)[1])
        ids.update(item["id"] for item in page["items"])
        cursor = page.get("next_cursor")
        if not cursor:
            return ids


def new_operations(token, before):
    return [
        unwrap(request(PREFIX + f"operations/{operation}", token=token)[1])
        for operation in sorted(ledger_ids(token) - set(before))
    ]


def send_once(token, state, record, label, message):
    slot = record.setdefault(label, {"phase": "ready"})
    if slot["phase"] == "ready":
        slot["before"] = sorted(ledger_ids(token))
        slot["message"] = message
        slot["phase"] = "request_sent"
        save(state)
        slot["output"] = timed_stream(token, record["session"], message)
        slot["phase"] = "response_received"
        save(state)
    if slot["phase"] == "request_sent":
        # Network interruption: collect ledger, but never resend a possibly applied request.
        slot["operations"] = new_operations(token, slot["before"])
        save(state)
        raise RuntimeError(f"{record['id']} {label} interrupted; inspect without replay")
    if "operations" not in slot:
        slot["operations"] = new_operations(token, slot["before"])
    else:
        # Preserve this request's collected operation IDs. Later test requests
        # must not become part of an earlier result when the journal resumes.
        slot["operations"] = [
            unwrap(request(PREFIX + f"operations/{row['id']}", token=token)[1])
            for row in slot["operations"]
        ]
    if "returned_operations" not in slot:
        references, observed = returned_evidence(slot["output"])
        slot["returned_operations"] = [
            {
                "operation": unwrap(request(PREFIX + f"operations/{op}", token=token)[1]),
                "current_result": result,
                "new_in_request": op not in slot["before"],
            }
            for op, result in references.items()
        ]
        slot["observed_notice_tasks"] = observed
    save(state)
    return slot


def main(confirm_delete=False, regressions=False, batch="v1", select_delete_fixture=False):
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    if (
        hashlib.sha256((FROZEN.parent / frozen["source"]).read_bytes()).hexdigest()
        != frozen["source_sha256"]
    ):
        raise RuntimeError("reviewed reference source changed after freeze")
    digest = hashlib.sha256(FROZEN.read_bytes()).hexdigest()
    token = wait_ready()
    project = json.loads((ROOT / "runtime-data/test-project.json").read_text(encoding="utf-8-sig"))
    settings = unwrap(request(PREFIX + "settings", token=token)[1])["settings"]
    if project["name"] != "NotiDo 验收" or settings["allowed_projects"] != [project["id"]]:
        raise RuntimeError("dedicated allowed test list required")
    state = (
        json.loads(JOURNAL.read_text(encoding="utf-8"))
        if JOURNAL.exists()
        else {
            "frozen_sha256": digest,
            "main_sha256": hashlib.sha256((ROOT / "main.py").read_bytes()).hexdigest(),
            "cases": {},
            "date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
            "batch": batch,
        }
    )
    if state["frozen_sha256"] != digest:
        raise RuntimeError("cannot mix two versions of the reviewed corpus")
    if batch != "v1":
        installed = runtime_version()
        if state["main_sha256"] != installed["main_sha256"] or (
            state.get("runtime_code_sha256")
            and state["runtime_code_sha256"] != installed["runtime_code_sha256"]
        ):
            raise RuntimeError("cannot mix installed implementation versions within a formal batch")
        if "runtime_code_sha256" not in state:
            state["runtime_snapshot_captured_after_collection"] = bool(state["cases"])
            state["runtime_code_sha256"] = installed["runtime_code_sha256"]
            save(state)
    cases = (
        sorted(frozen["cases"], key=lambda c: c["id"] == "D10")
        if batch != "v1"
        else frozen["cases"]
    )
    for case in cases:
        case_id = case["id"]
        if regressions and case_id not in {"N04", "N08", "N16", "N25", "D11", "D12"}:
            continue
        record = state["cases"].setdefault(case_id, {**case, "phase": "ready"})
        if record["phase"] == "ready":
            record["session"] = bind_session(token)
            record["phase"] = "bound"
            save(state)
        if record["phase"] == "bound":
            memory = send_once(
                token,
                state,
                record,
                "memory",
                "本次验收请记住：我是东北大学材料学院2024级学生、班委，其他角色未确认。"
                "默认及允许清单是NotiDo 验收。接下来直接处理我发的输入，意图明确即可执行，"
                f"若新建任务，标题统一加“NotiDo 验收 · {case_id} · ”前缀并保留实际事项名称；"
                "不要因前缀多造任务。现在只确认这些会话条件，不查询、创建或变更任何任务。",
            )
            if memory["operations"]:
                raise RuntimeError("unexpected write while seeding native session memory")
            record["phase"] = "seeded"
            save(state)
        if case_id in ("D10", "D16") and record["phase"] == "seeded":
            fixture_message = (
                "请在NotiDo 验收清单新建一个无日期测试任务，标题必须完整为"
                f"“NotiDo 验收 · 语料删除测试{(' · ' + batch.upper()) if batch != 'v1' else ''}”，不使用之前的前缀，不完成、不删除。"
                "接下来本轮“专用语料删除测试”唯一指代这个完整标题；其他历史同名测试任务不在本轮目标范围。"
                if case_id == "D10"
                else "请在NotiDo 验收清单新建两个无日期测试任务：“报告甲”和“报告乙”，"
                "两个都用本会话前缀，不完成、不删除。这是接下来测试同名歧义的准备。"
            )
            fixture = send_once(token, state, record, "fixture", fixture_message)
            rows = fixture["operations"]
            count = 1 if case_id == "D10" else 2
            if len(rows) != count or any(
                row["kind"] != "create" or row["state"] != "succeeded" or row["attempt"] != 1
                for row in rows
            ):
                raise RuntimeError(f"{case_id} prepared target(s) not uniquely verified")
            record["phase"] = "fixture_ready"
            save(state)
        if case_id == "D19" and batch != "v1" and record["phase"] == "seeded":
            fixture = send_once(
                token,
                state,
                record,
                "fixture",
                f"在NotiDo 验收清单新建无日期任务“NotiDo 验收 · D19 · 保留已保存结果 · {batch.upper()}”。只建这一项，不完成、不删除。",
            )
            rows = fixture["operations"]
            if len(rows) != 1 or rows[0]["kind"] != "create" or rows[0]["state"] != "succeeded":
                raise RuntimeError("D19 saved fixture not verified")
            if "pending_operation" not in record:
                record.setdefault("fixture_request_id", str(uuid.uuid4()))
                save(state)
                result = subprocess.run(
                    [
                        "docker",
                        "exec",
                        "-i",
                        "-w",
                        "/opt/notido-plugin",
                        CONTAINER,
                        "python",
                        "-m",
                        "tools.native_cancel_fixture",
                    ],
                    input=json.dumps(
                        {
                            "saved_operation_id": rows[0]["id"],
                            "request_id": record["fixture_request_id"],
                        }
                    ).encode(),
                    capture_output=True,
                    timeout=30,
                )
                if result.returncode:
                    raise RuntimeError("D19 paused fixture failed; inspect without replay")
                record["pending_operation"] = json.loads(result.stdout)["id"]
                save(state)
            record["phase"] = "fixture_ready"
            save(state)
        if "evaluation" not in record or record["evaluation"]["phase"] != "response_received":
            message = "删除专用“语料删除测试”任务。" if case_id == "D10" else case["input"]
            send_once(token, state, record, "evaluation", message)
            record["phase"] = "awaiting_delete_confirmation" if case_id == "D10" else "collected"
            save(state)
            print(json.dumps({"case": case_id, "phase": record["phase"]}), flush=True)
        elif record["phase"] != "collected" and case_id != "D10":
            # An observer may restore the actual saved framework response after
            # SSE loss. Refresh only its captured IDs, then close collection.
            send_once(token, state, record, "evaluation", record["evaluation"]["message"])
            record["phase"] = "collected"
            save(state)
        if case_id == "D19" and batch != "v1":
            readback = send_once(
                token,
                state,
                record,
                "readback",
                f"只查询标题含“NotiDo 验收 · D19 · 保留已保存结果 · {batch.upper()}”的未完成任务，不修改、完成或删除。",
            )
            pending = unwrap(
                request(PREFIX + f"operations/{record['pending_operation']}", token=token)[1]
            )
            saved_row = record["fixture"]["operations"][0]
            visible = any(
                task.get("id") == saved_row["remote_id"] and task.get("status") == 0
                for name, value in tool_results(readback["output"])
                if name == "notido_query"
                for task in value.get("tasks", [])
            )
            record["cancel_checks"] = {
                "cancelled_unstarted": pending["state"] == "cancelled"
                and pending["attempt"] == 0
                and pending["remote_id"] is None,
                "no_remote_write": not record["evaluation"]["operations"]
                and not readback["operations"],
                "saved_task_preserved": visible,
            }
            save(state)
        if case_id == "D10" and batch != "v1":
            expected_target = record["fixture"]["operations"][0]
            preview_slot = record.get("target_selection", record["evaluation"])
            previews = [
                value
                for name, value in tool_results(preview_slot["output"])
                if name == "notido_delete" and value.get("confirmation_ref")
            ]
            if not previews:
                record["phase"] = "awaiting_target_selection"
                record["fixture_ambiguous_in_evaluation"] = True
                save(state)
                if select_delete_fixture:
                    title = expected_target["plan"]["fields"]["title"]
                    preview_slot = send_once(
                        token,
                        state,
                        record,
                        "target_selection",
                        f"本轮专用测试目标仅为刚准备的“{title}”，不涉及其他同名任务。请查询并仅预览这一个目标，不删除，等待我后续确认。",
                    )
                    previews = [
                        value
                        for name, value in tool_results(preview_slot["output"])
                        if name == "notido_delete" and value.get("confirmation_ref")
                    ]
            if previews:
                if (
                    preview_slot["operations"]
                    or len(previews) != 1
                    or previews[0]["task"]["id"] != expected_target["remote_id"]
                ):
                    raise RuntimeError(
                        "deletion preview must be zero-write and match exact prepared fixture"
                    )
                if record["phase"] != "collected":
                    record["phase"] = "awaiting_delete_confirmation"
                record["actual_preview"] = previews[0]
                save(state)
        if (
            case_id == "D10"
            and confirm_delete
            and record["phase"] == "awaiting_delete_confirmation"
        ):
            confirmation = send_once(token, state, record, "confirmation", "确认删除")
            record["phase"] = "collected"
            save(state)
            print(
                json.dumps(
                    {
                        "case": case_id,
                        "phase": "collected",
                        "operations": len(confirmation["operations"]),
                    }
                ),
                flush=True,
            )
    state["summary"] = {
        "collected": sum(r["phase"] == "collected" for r in state["cases"].values()),
        "pending_confirmation": [
            case_id
            for case_id, r in state["cases"].items()
            if r["phase"] == "awaiting_delete_confirmation"
        ],
        "graded": False,
    }
    save(state)
    print(json.dumps(state["summary"]), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--batch",
        choices=[
            "v1",
            "v2",
            "v3",
            "v4",
            "v5",
            "v6",
            "v7",
            "v8",
            "v9",
            "v10",
            "v11",
            "v12",
            "v13",
            "v14",
        ],
        default="v1",
    )
    parser.add_argument("--regressions", action="store_true")
    parser.add_argument("--confirm-delete", action="store_true")
    parser.add_argument("--select-delete-fixture", action="store_true")
    args = parser.parse_args()
    if args.regressions:
        JOURNAL = ROOT / "runtime-data/native-corpus-guidance-regressions-v2.json"
    else:
        JOURNAL = ROOT / f"runtime-data/native-corpus-{args.batch}-results.json"
    main(
        confirm_delete=args.confirm_delete,
        regressions=args.regressions,
        batch=args.batch,
        select_delete_fixture=args.select_delete_fixture,
    )
