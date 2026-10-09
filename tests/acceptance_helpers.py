"""Pure historical evidence fixtures; no framework deployment or API calls."""

import json


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


def isolate_framework_config(config):
    config = json.loads(json.dumps(config))
    for platform in config.get("platform", []):
        platform["enable"] = False
    return config
