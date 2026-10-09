"""Close real SSE after a verified native write, then retrieve without replay."""

import json
import urllib.request

from tools import native_corpus_smoke as collector
from tools.container_smoke import BASE, PORT, PREFIX, ROOT, request, unwrap, wait_ready
from tools.native_performance_smoke import bind_session

JOURNAL = ROOT / "runtime-data/native-reply-disconnect-v7-results.json"
TITLE = "NotiDo 验收 · V7写后回执断连"


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
        state = {
            **version,
            "phase": "ready",
            "record": {"id": "reply-disconnect-v7", "session": bind_session(token)},
        }
        collector.save(state)
    if any(state.get(k) != v for k, v in version.items()):
        raise RuntimeError("don't mix installed versions")
    record = state["record"]
    if state["phase"] == "ready":
        state["before"] = sorted(collector.ledger_ids(token))
        state["phase"] = "request_sent"
        state["events"] = []
        collector.save(state)
        payload = {
            "session_id": record["session"],
            "message": f"请在NotiDo 验收清单新建一项无日期任务，标题完整为“{TITLE}”，备注“专用回执断连验收”。只创建这一个独立任务，不修改历史项，不完成或删除。",
            "selected_provider": "deepseek/deepseek-flash",
            "selected_model": "deepseek-flash",
            "enable_streaming": True,
        }
        req = urllib.request.Request(
            BASE + "/api/chat/send",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        )
        calls = {}
        with urllib.request.urlopen(req, timeout=180) as response:
            for line in response:
                line = line.decode().strip()
                if not line.startswith("data:"):
                    continue
                item = json.loads(line[5:].strip())
                state["events"].append(item)
                if item.get("type") == "complete":
                    raise RuntimeError(
                        "final response already received, not a disconnect-boundary test"
                    )
                if item.get("chain_type") not in ("tool_call", "tool_call_result"):
                    continue
                value = json.loads(item["data"])
                if item["chain_type"] == "tool_call":
                    calls[value["id"]] = value.get("name")
                    continue
                if calls.get(value["id"]) != "notido_create":
                    continue
                result = value.get("result")
                result = json.loads(result) if isinstance(result, str) else result
                if isinstance(result, dict) and result.get("state") == "succeeded":
                    state["write_result"] = result
                    state["phase"] = "disconnecting_after_write"
                    collector.save(state)
                    break
        if state["phase"] != "disconnecting_after_write":
            raise RuntimeError("no verified write before disconnect; inspect without replay")
        state["phase"] = "client_disconnected_before_final"
        collector.save(state)
    if state["phase"] not in ("client_disconnected_before_final", "collected"):
        raise RuntimeError("interrupted boundary is recorded; don't resend")
    rows = collector.new_operations(token, state["before"])
    if (
        len(rows) != 1
        or rows[0]["kind"] != "create"
        or rows[0]["state"] != "succeeded"
        or rows[0]["attempt"] != 1
    ):
        raise RuntimeError("disconnected write did not persist uniquely")
    row = rows[0]
    fields = json.loads(row["result"])["actual_fields"]
    if (
        fields["title"] != TITLE
        or fields.get("dueDate") is not None
        or row["id"] != state["write_result"]["operation_id"]
    ):
        raise RuntimeError("persisted result differs from verified write")
    state["persisted_operation"] = row
    collector.save(state)
    followup = collector.send_once(
        token,
        state,
        record,
        "retrieval",
        "上一条创建任务后的最终回复连接被验收客户端主动中断，尚未收到最终回执。"
        f"请只用notido_check核查实际操作 {row['id']} 并重新给出其结果，不创建、重建或修改任何任务。",
    )
    if followup["operations"]:
        raise RuntimeError("retrieving the missing reply caused a new write")
    if not followup["output"].get("final_text"):
        # AstrBot can enqueue this follow-up into the still-running original
        # agent after the first SSE client disconnects. Its separate stream
        # then has no final message; recover the completed saved response,
        # never send another request or invent a delivery measurement.
        from tools.native_corpus_recover import recovered_output

        status, saved = request("/api/v1/chat/sessions/" + record["session"], token=token)
        saved = unwrap(saved)
        if (
            status != 200
            or saved.get("is_running")
            or saved.get("active_runs")
            or saved.get("has_more")
        ):
            raise RuntimeError(
                "original framework agent is still live; inspect this session without replay"
            )
        followup.setdefault("initial_stream_output", followup["output"])
        followup["output"] = recovered_output(saved["history"], followup["message"])
        state["observation_recovery"] = "saved_idle_framework_history; queued_followup_not_resent"
        collector.save(state)
    checks = [v for n, v in collector.tool_results(followup["output"]) if n == "notido_check"]
    if (
        len(checks) != 1
        or checks[0].get("operation_id") != row["id"]
        or checks[0].get("state") != "succeeded"
    ):
        raise RuntimeError("followup didn't retrieve the actual persisted result")
    state["phase"] = "collected"
    collector.save(state)
    print(
        json.dumps(
            {
                "verified_create": 1,
                "create_attempts": 1,
                "retrieval_writes": 0,
                "final_not_received_before_disconnect": True,
                "pass": True,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
