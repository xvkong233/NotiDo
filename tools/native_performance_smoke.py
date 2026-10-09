"""Thirty text and ten file native chats, with journaled single writes and timing.

Measures fresh bound sessions sequentially on the dedicated list. Stops on the
first correctness failure; interrupted requests are inspected, never resent.
"""

import argparse
import hashlib
import json
import math
import os
import platform
import time
import urllib.request
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.container_smoke import BASE, PREFIX, ROOT, request, unwrap, wait_ready
from tools.container_vision_smoke import fixture
from tools.native_material_smoke import upload
from tools.native_recurrence_delete_smoke import operations

JOURNAL = ROOT / "runtime-data/native-performance-v2-results.json"


def save(state):
    temporary = JOURNAL.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, JOURNAL)


def timed_stream(token, session, message):
    payload = {
        "session_id": session,
        "message": message,
        "selected_provider": "deepseek/deepseek-flash",
        "selected_model": "deepseek-flash",
        "enable_streaming": True,
    }
    req = urllib.request.Request(
        BASE + "/api/chat/send",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    started = time.perf_counter()
    metrics = {
        "framework_message_saved_seconds": None,
        "first_visible_text_seconds": None,
        "tools": [],
    }
    with urllib.request.urlopen(req, timeout=180) as response:
        for line in response:
            value = line.decode().strip()
            if not value.startswith("data:"):
                continue
            item = json.loads(value[5:].strip())
            elapsed = time.perf_counter() - started
            if item.get("type") == "user_message_saved":
                metrics["framework_message_saved_seconds"] = elapsed
            if (
                item.get("type") == "plain"
                and item.get("chain_type") is None
                and item.get("data")
                and metrics["first_visible_text_seconds"] is None
            ):
                metrics["first_visible_text_seconds"] = elapsed
            if item.get("chain_type") in ("tool_call", "tool_call_result"):
                metrics["tools"].append(
                    {
                        "kind": item["chain_type"],
                        "seconds": elapsed,
                        "payload": json.loads(item["data"]),
                    }
                )
            if item.get("type") == "complete":
                metrics["final_text"] = item["data"]
    metrics["end_to_end_seconds"] = time.perf_counter() - started
    return metrics


def bind_session(token):
    session = unwrap(request("/api/v1/chat/sessions/new", token=token)[1])["session_id"]
    credentials = json.loads((ROOT / "runtime-data/acceptance-webui.json").read_text())
    settings = unwrap(request(PREFIX + "settings", token=token)[1])
    status, data = request(
        PREFIX + "identity/bind",
        token=token,
        payload={
            "request_id": str(uuid.uuid4()),
            "expected_revision": settings["revision"],
            "platform_id": "webchat",
            "actor_id": credentials["username"],
            "origin": f"webchat:FriendMessage:webchat!{credentials['username']}!{session}",
            "enabled": True,
        },
    )
    if status != 200 or "error" in unwrap(data):
        raise RuntimeError("native benchmark binding failed")
    return session


def p95(values):
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


def main(batch="v3"):
    global JOURNAL
    JOURNAL = ROOT / f"runtime-data/native-performance-{batch}-results.json"
    from tools.native_corpus_smoke import runtime_version

    installed = runtime_version()
    token = wait_ready()
    project = json.loads((ROOT / "runtime-data/test-project.json").read_text(encoding="utf-8-sig"))
    settings = unwrap(request(PREFIX + "settings", token=token)[1])["settings"]
    if project["name"] != "NotiDo 验收" or settings["allowed_projects"] != [project["id"]]:
        raise RuntimeError("dedicated test scope required")
    state = (
        json.loads(JOURNAL.read_text(encoding="utf-8"))
        if JOURNAL.exists()
        else {
            "batch": batch,
            "date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
            **installed,
            "cases": {},
            "machine": {
                "host_os": platform.platform(),
                "host_processor": platform.processor(),
                "cpu_count": os.cpu_count(),
                "astrbot": "4.28.2",
                "task_cli": "0.1.14",
                "node": "24.18.0",
                "provider": "deepseek/deepseek-flash",
                "model": "deepseek-flash",
                "timezone": "Asia/Shanghai",
            },
            "conditions": "Sequential fresh authorized AstrBot sessions; text is direct timed creation; files are clear PNG/scanned PDF/DOCX image extraction plus native original attachment.",
        }
    )
    if any(state.get(key) != value for key, value in installed.items()):
        raise RuntimeError(
            "performance batch lacks matching installed source provenance; retain old evidence separately"
        )
    for index in range(40):
        case_id = f"P{index + 1:02d}"
        kind = "text" if index < 30 else ("png", "pdf", "docx")[(index - 30) % 3]
        title = f"NotiDo 验收 · 性能{batch.upper()} · {case_id}"
        record = state["cases"].setdefault(
            case_id, {"kind": kind, "title": title, "phase": "ready"}
        )
        if record["phase"] == "verified":
            continue
        if record["phase"] == "ready":
            record["session"] = bind_session(token)
            if kind != "text":
                path = ROOT / f"runtime-data/notido-perf-{batch}-{case_id}.{kind}"
                record["expected"] = fixture(kind, path, batch.upper() + case_id)
                record["path"] = str(path)
                record["bytes"] = path.stat().st_size
                record["original_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
                started = time.perf_counter()
                record["attachment"] = upload(token, path)
                record["astrbot_file_upload_seconds"] = time.perf_counter() - started
            else:
                record["expected"] = {"date": "2027-12-29", "time": "14:00"}
            record["phase"] = "prepared"
            save(state)
        if record["phase"] == "prepared":
            if kind == "text":
                message = f"请在NotiDo 验收清单新建“{title}”，2027年12月29日14:00截止，备注“专用性能验收”。这是我明确要记录的一项，不需要提醒。"
            else:
                attachment = record["attachment"]
                message = [
                    {
                        "type": "plain",
                        "text": f"材料中是本人明确要做的一项通知。请用材料工具读取图像后记录，标题必须为“{title}”，只使用NotiDo 验收清单，日期时刻和要求仅从材料取得，并将原件挂到任务原生附件。",
                    },
                    {
                        "type": "image" if kind == "png" else "file",
                        "attachment_id": attachment["attachment_id"],
                        "filename": attachment["filename"],
                    },
                ]
            record["phase"] = "request_sent"
            save(state)
            record["timing"] = timed_stream(token, record["session"], message)
            record["phase"] = "response_received"
            save(state)
        rows = operations(token, title)
        # The native model can correct its own title on the same saved task.
        # A title-prefix-only search would omit that original create snapshot.
        # Follow this request's actual returned references; never infer IDs from
        # prose, relax title matching, or replay the request to fill evidence.
        from tools.native_corpus_smoke import tool_results

        returned = [
            value
            for name, value in tool_results(record["timing"])
            if name in ("notido_create", "notido_update", "notido_check")
            and value.get("state") == "succeeded"
            and value.get("operation_id")
        ]
        by_id = {row["id"]: row for row in rows}
        missing = [
            value["operation_id"] for value in returned if value["operation_id"] not in by_id
        ]
        if missing and record.get("operations"):
            record.setdefault("initial_collected_operations", record["operations"])
            record.setdefault("collector_observations", []).append(
                "Title-prefix lookup omitted the original create; exact current tool references reread without request replay."
            )
        for value in returned:
            row = unwrap(request(PREFIX + f"operations/{value['operation_id']}", token=token)[1])
            plan = json.loads(row["plan"]) if isinstance(row["plan"], str) else row["plan"]
            if not plan["origin"].endswith("!" + record["session"]):
                raise RuntimeError("returned operation belongs to another session")
            by_id[row["id"]] = row
        rows = list(by_id.values())
        record["operations"] = rows
        save(state)
        creates = [row for row in rows if row["kind"] == "create"]
        if len(creates) != 1 or creates[0]["state"] != "succeeded":
            raise RuntimeError(
                f"{case_id} creation not uniquely verified; inspect ledger without replay"
            )
        created = creates[0]
        actual = json.loads(created["result"])["actual_fields"]
        record["initial_create_actual_fields"] = actual
        corrected = []
        for value in returned:
            row = by_id[value["operation_id"]]
            if row["kind"] == "update":
                if row["remote_id"] != created["remote_id"] or row["attempt"] != 1:
                    raise RuntimeError("self-correction targeted another task or repeated a write")
                actual = json.loads(row["result"])["actual_fields"]
                corrected.append(row["id"])
        record["self_correction_operation_ids"] = corrected
        record["final_actual_fields"] = actual
        save(state)
        if actual.get("title") != title:
            raise RuntimeError(f"{case_id} final title does not match the requested literal")
        local = datetime.fromisoformat(actual["dueDate"].replace("Z", "+00:00")).astimezone(
            ZoneInfo("Asia/Shanghai")
        )
        if (
            local.date().isoformat() != record["expected"]["date"]
            or local.strftime("%H:%M") != record["expected"]["time"]
            or actual.get("isAllDay") is not False
        ):
            raise RuntimeError(f"{case_id} deadline mismatch")
        if created["attempt"] != 1:
            raise RuntimeError("unexpected repeated write")
        if kind != "text":
            from urllib.parse import quote

            all_uploads, cursor = [], None
            while True:
                endpoint = PREFIX + "operations?limit=100"
                if cursor:
                    endpoint += "&cursor=" + quote(cursor)
                page = unwrap(request(endpoint, token=token)[1])
                all_uploads.extend(page["items"])
                cursor = page.get("next_cursor")
                if not cursor:
                    break
            uploaded = []
            for item in all_uploads:
                if item["kind"] != "upload":
                    continue
                detail = unwrap(request(PREFIX + f"operations/{item['id']}", token=token)[1])
                if detail["plan"].get("task_id") == created["remote_id"]:
                    uploaded.append(detail)
            record["uploads"] = uploaded
            save(state)
            if (
                len(uploaded) != 1
                or uploaded[0]["state"] != "succeeded"
                or uploaded[0]["attempt"] != 1
            ):
                raise RuntimeError(f"{case_id} original not verified")
            if (
                json.loads(uploaded[0]["result"])["actual_fields"].get("sha256")
                != record["original_hash"]
            ):
                raise RuntimeError(f"{case_id} original hash mismatch")
            if not all(
                word in actual.get("content", "") for word in ("PDF", record["expected"]["code"])
            ):
                raise RuntimeError(f"{case_id} requirement mismatch")
        if "timing" not in record:
            raise RuntimeError(
                "interrupted case has no trustworthy measured duration; do not invent timing"
            )
        record["phase"] = "verified"
        save(state)
        print(
            json.dumps(
                {
                    "case": case_id,
                    "kind": kind,
                    "pass": True,
                    "seconds": round(record["timing"]["end_to_end_seconds"], 3),
                }
            ),
            flush=True,
        )
    text = [
        r["timing"]["end_to_end_seconds"] for r in state["cases"].values() if r["kind"] == "text"
    ]
    files = [
        r["timing"]["end_to_end_seconds"] for r in state["cases"].values() if r["kind"] != "text"
    ]
    state["summary"] = {
        "text_count": len(text),
        "text_p95_seconds": p95(text),
        "file_count": len(files),
        "file_p95_seconds": p95(files),
        "text_target_pass": p95(text) <= 20,
        "file_target_pass": p95(files) <= 120,
        "framework_message_saved_p95_seconds": p95(
            [r["timing"]["framework_message_saved_seconds"] for r in state["cases"].values()]
        ),
    }
    save(state)
    print(json.dumps(state["summary"]), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", choices=("v3", "v4", "v5"), default="v3")
    main(parser.parse_args().batch)
