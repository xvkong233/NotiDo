"""Recover one interrupted corpus response from authenticated saved AstrBot history.

Never sends messages, changes task state, or invents missing SSE timings.
"""

import argparse
import json

from tools import native_corpus_smoke as collector
from tools.container_smoke import ROOT, request, unwrap, wait_ready


def recovered_output(history, original_message):
    if not isinstance(original_message, str):
        raise RuntimeError("this recovery requires an exact plain corpus input")
    ordered = sorted(history, key=lambda row: row["id"])
    users = [row for row in ordered if row["content"].get("type") == "user"]
    if not users or users[-1]["content"].get("message") != [
        {"type": "plain", "text": original_message}
    ]:
        raise RuntimeError(
            "latest saved user input does not exactly match interrupted corpus request"
        )
    responses = [
        row
        for row in ordered
        if row["id"] > users[-1]["id"] and row["content"].get("type") == "bot"
    ]
    if len(responses) != 1:
        raise RuntimeError("need exactly one finished saved response, not a guess or replay")
    response = responses[0]
    parts = response["content"]["message"]
    if not parts or parts[-1].get("type") != "plain" or not parts[-1].get("text"):
        raise RuntimeError("saved response lacks a final plain receipt")
    tools = []
    for part in parts:
        for call in part.get("tool_calls", []):
            if "result" not in call or "finished_ts" not in call:
                raise RuntimeError("cannot recover unfinished tool calls")
            payload = {k: call[k] for k in ("id", "name", "args", "ts")}
            tools.append({"kind": "tool_call", "seconds": None, "payload": payload})
            tools.append(
                {
                    "kind": "tool_call_result",
                    "seconds": None,
                    "payload": {
                        "id": call["id"],
                        "ts": call["finished_ts"],
                        "result": call["result"],
                    },
                }
            )
    return {
        "tools": tools,
        "final_text": "".join(p["text"] for p in parts if p.get("type") == "plain"),
        "framework_message_saved_seconds": None,
        "first_visible_text_seconds": None,
        "end_to_end_seconds": None,
        "recovered_from_framework_history": True,
        "framework_user_message_id": users[-1]["id"],
        "framework_bot_message_id": response["id"],
    }


def main(batch, case):
    collector.JOURNAL = ROOT / f"runtime-data/native-corpus-{batch}-results.json"
    state = json.loads(collector.JOURNAL.read_text(encoding="utf-8"))
    if any(state[k] != v for k, v in collector.runtime_version().items()):
        raise RuntimeError("installed implementation changed since original request")
    record = state["cases"][case]
    slot = record["evaluation"]
    if slot["phase"] != "request_sent" or slot.get("output"):
        raise RuntimeError("only interrupted uncollected response may be recovered")
    token = wait_ready()
    status, data = request("/api/v1/chat/sessions/" + record["session"], token=token)
    data = unwrap(data)
    if status != 200 or data.get("is_running") or data.get("active_runs") or data.get("has_more"):
        raise RuntimeError("need complete saved idle-session history before recovery")
    slot["output"] = recovered_output(data["history"], slot["message"])
    slot["phase"] = "response_received"
    state.setdefault("observation_recoveries", []).append(
        {
            "case": case,
            "source": "authenticated_saved_framework_history",
            "request_resent": False,
            "timing_measured": False,
        }
    )
    collector.save(state)
    print(
        json.dumps(
            {
                "case": case,
                "output_recovered": True,
                "request_resent": False,
                "timing_measured": False,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", choices=("v5", "v6", "v7"), required=True)
    parser.add_argument("--case", required=True)
    main(**vars(parser.parse_args()))
