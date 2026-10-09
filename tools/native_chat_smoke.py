"""Real AstrBot conversation -> native tools -> dedicated Dida list.

Run only after installing the native refactor. Journal before each request; an
interrupted write phase is inspected through the ledger, never sent again.
Secrets stay in local protected files and never enter stdout or the journal.
"""

import json
import os
import urllib.request
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.container_smoke import BASE, PREFIX, ROOT, request, unwrap, wait_ready

JOURNAL = ROOT / "runtime-data/native-chat-results.json"


def save(state):
    staged = JOURNAL.with_suffix(".tmp")
    staged.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(staged, JOURNAL)


def stream(token, session, message):
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
    with urllib.request.urlopen(req, timeout=180) as response:
        events = []
        for line in response:
            value = line.decode().strip()
            if value.startswith("data:"):
                events.append(json.loads(value[5:].strip()))
        return events


def main():
    token = wait_ready()
    status, data = request("/api/v1/tools", token=token)
    tools = unwrap(data)
    if (
        status != 200
        or not isinstance(tools, list)
        or sum(str(tool.get("name", "")).startswith("notido_") for tool in tools) != 10
    ):
        raise RuntimeError("native refactor must be installed with all ten tools first")
    project = json.loads((ROOT / "runtime-data/test-project.json").read_text(encoding="utf-8-sig"))
    current = unwrap(request(PREFIX + "settings", token=token)[1])
    if (
        current["settings"]["allowed_projects"] != [project["id"]]
        or project["name"] != "NotiDo 验收"
    ):
        raise RuntimeError("dedicated authorized test list required")
    state = json.loads(JOURNAL.read_text(encoding="utf-8")) if JOURNAL.exists() else {}
    if not state.get("session"):
        session = unwrap(request("/api/v1/chat/sessions/new", token=token)[1])["session_id"]
        credentials = json.loads((ROOT / "runtime-data/acceptance-webui.json").read_text())
        state = {
            "session": session,
            "title": "NotiDo 验收 · 原生工具与会话记忆",
            "phase": "created",
        }
        save(state)
        current = unwrap(request(PREFIX + "settings", token=token)[1])
        status, data = request(
            PREFIX + "identity/bind",
            token=token,
            payload={
                "request_id": str(uuid.uuid4()),
                "expected_revision": current["revision"],
                "platform_id": "webchat",
                "actor_id": credentials["username"],
                "origin": f"webchat:FriendMessage:webchat!{credentials['username']}!{session}",
                "enabled": True,
            },
        )
        if status != 200 or "error" in unwrap(data):
            raise RuntimeError("test conversation binding failed; inspect local response")
        state["phase"] = "bound"
        save(state)
    if state["phase"] == "bound":
        state["phase"] = "memory_request_sent"
        save(state)
        state["memory_events"] = stream(
            token,
            state["session"],
            "本次验收请记住：我是材料学院2024级学生，验收班委。现在只确认身份，不查询或创建任何任务。",
        )
        state["phase"] = "memory_confirmed"
        save(state)
    if state["phase"] == "memory_confirmed":
        state["phase"] = "write_request_sent"
        save(state)
        state["write_events"] = stream(
            token,
            state["session"],
            f"通知：材料学院2024级班委须于2027年12月26日16:20提交PDF格式验收报告，文件名ND-NATIVE-01。仅为适用本人条件的必做事项自动建立待办，任务标题用“{state['title']}”，清单为NotiDo 验收。使用当前会话记忆判断适用性，不要维护新的身份档案。",
        )
        state["phase"] = "write_response_received"
        save(state)
    operations = unwrap(request(PREFIX + "operations?limit=100", token=token)[1])["items"]
    matches = []
    for operation in operations:
        detail = unwrap(request(PREFIX + f"operations/{operation['id']}", token=token)[1])
        row = detail.get("operation", detail)
        plan = json.loads(row["plan"]) if isinstance(row["plan"], str) else row["plan"]
        if (
            plan.get("delivery_mode") == "framework_tool"
            and plan.get("fields", {}).get("title") == state["title"]
        ):
            matches.append(row)
    state["operations"] = matches
    save(state)
    if len(matches) != 1 or matches[0]["state"] != "succeeded":
        raise RuntimeError(
            "native write not uniquely verified; request was journaled and will not be replayed"
        )
    result = (
        json.loads(matches[0]["result"])
        if isinstance(matches[0]["result"], str)
        else matches[0]["result"]
    )
    actual = result["actual_fields"]
    local = datetime.fromisoformat(actual["dueDate"].replace("Z", "+00:00")).astimezone(
        ZoneInfo("Asia/Shanghai")
    )
    if (
        not all(value in actual.get("content", "") for value in ("PDF", "ND-NATIVE-01"))
        or local.strftime("%Y-%m-%d %H:%M") != "2027-12-26 16:20"
        or actual.get("isAllDay") is True
    ):
        raise RuntimeError("native requirements/date readback mismatch")
    state["checks"] = [
        {"check": "real_native_chat_memory_tools_and_dida", "pass": True},
        {"check": "unique_single_attempt_verified_task", "pass": matches[0]["attempt"] == 1},
    ]
    save(state)
    print(json.dumps(state["checks"]))


if __name__ == "__main__":
    main()
