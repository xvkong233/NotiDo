"""Real native AstrBot image tool loop and Dida original attachment verification."""

import hashlib
import json
import os
import urllib.request
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.container_smoke import BASE, PREFIX, ROOT, request, unwrap, wait_ready
from tools.container_vision_smoke import fixture
from tools.native_chat_smoke import stream


def save(path, state):
    staging = path.with_suffix(".tmp")
    staging.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(staging, path)


def upload(token, path):
    boundary = "NotiDo" + uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
        + path.read_bytes()
        + f"\r\n--{boundary}--\r\n".encode()
    )
    req = urllib.request.Request(
        BASE + "/api/chat/post_file",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return unwrap(json.loads(response.read()))


def main(kind):
    if kind not in ("png", "pdf", "docx"):
        raise ValueError("png/pdf/docx required")
    token = wait_ready()
    chat = json.loads((ROOT / "runtime-data/native-chat-results.json").read_text(encoding="utf-8"))
    if not all(check["pass"] for check in chat.get("checks", [])) or not chat.get("checks"):
        raise RuntimeError("native conversation smoke must pass first")
    journal = ROOT / f"runtime-data/native-material-{kind}.json"
    state = json.loads(journal.read_text(encoding="utf-8")) if journal.exists() else {}
    if not state:
        path = ROOT / f"runtime-data/notido-native.{kind}"
        expected = fixture(kind, path, "NATIVE")
        state = {
            "phase": "prepared",
            "session": chat["session"],
            "title": f"NotiDo 验收 · 原生图文 {kind}",
            "expected": expected,
            "original_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
            "path": str(path),
        }
        save(journal, state)
    if state["phase"] == "prepared":
        from pathlib import Path

        state["attachment"] = upload(token, Path(state["path"]))
        state["phase"] = "uploaded_to_astrbot"
        save(journal, state)
    if state["phase"] == "uploaded_to_astrbot":
        state["phase"] = "native_request_sent"
        save(journal, state)
        attachment = state["attachment"]
        # The deadline and requirements exist only in the supplied original image.
        message = [
            {
                "type": "plain",
                "text": f"请先用材料工具保存并读取这份我的必做通知，再记录本人事项，任务标题统一用“{state['title']}”，清单用NotiDo 验收。日期、时刻、提交要求严格从材料读取，并把这份原件上传到任务原生附件。沿用当前会话身份与原生工具循环。",
            },
            {
                "type": "image" if kind == "png" else "file",
                "attachment_id": attachment["attachment_id"],
                "filename": attachment["filename"],
            },
        ]
        state["events"] = stream(token, state["session"], message)
        state["phase"] = "native_response_received"
        save(journal, state)
    operations = unwrap(request(PREFIX + "operations?limit=100", token=token)[1])["items"]
    details = [
        unwrap(request(PREFIX + f"operations/{operation['id']}", token=token)[1])
        for operation in operations
    ]
    creates = [
        row
        for row in details
        if row["kind"] == "create"
        and row["plan"].get("delivery_mode") == "framework_tool"
        and row["plan"].get("fields", {}).get("title") == state["title"]
    ]
    if len(creates) != 1:
        state["operations"] = creates
        save(journal, state)
        raise RuntimeError("native creation not unique; inspect journal, never resend")
    created = creates[0]
    attached = [
        row
        for row in details
        if row["kind"] == "upload" and row["plan"].get("task_id") == created["remote_id"]
    ]
    state["operations"] = [created, *attached]
    save(journal, state)
    if created["state"] != "succeeded" or len(attached) != 1 or attached[0]["state"] != "succeeded":
        raise RuntimeError("native task/original not verified; inspect journal, never replay")
    result = json.loads(created["result"])
    actual = result["actual_fields"]
    local = datetime.fromisoformat(actual["dueDate"].replace("Z", "+00:00")).astimezone(
        ZoneInfo("Asia/Shanghai")
    )
    if (
        local.date().isoformat() != state["expected"]["date"]
        or local.strftime("%H:%M") != state["expected"]["time"]
        or not all(
            value in actual.get("content", "") for value in ("PDF", state["expected"]["code"])
        )
    ):
        raise RuntimeError("native visual date/time/requirements mismatch")
    if json.loads(attached[0]["result"])["actual_fields"].get("sha256") != state["original_hash"]:
        raise RuntimeError("downloaded native original differs from input bytes")
    state["checks"] = [
        {"check": f"native_{kind}_vision_deadline_requirements", "pass": True},
        {"check": f"native_{kind}_original_attachment_hash", "pass": True},
        {
            "check": "single_attempt_task_and_attachment",
            "pass": created["attempt"] == attached[0]["attempt"] == 1,
        },
    ]
    save(journal, state)
    print(json.dumps(state["checks"]))


if __name__ == "__main__":
    import sys

    main(sys.argv[1])
