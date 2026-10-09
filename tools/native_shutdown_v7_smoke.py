"""Actual unload and SIGTERM after remote create, before CLI stdout reaches NotiDo."""

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import time

from tools import native_corpus_smoke as collector
from tools.container_smoke import PORT, PREFIX, ROOT, request, unwrap, wait_ready
from tools.native_performance_smoke import bind_session, timed_stream

JOURNAL = ROOT / "runtime-data/native-shutdown-v7-results.json"
DATA = ROOT / "runtime-data/astrbot-native-v7"
CONFIG = DATA / "config/astrbot_plugin_notido_config.json"
CONTAINER = "notido-native-v7"
ENDPOINT = "/api/v1/plugins/astrbot_plugin_notido/enabled"


def toggle(token, enabled):
    status, value = request(
        ENDPOINT, token=token, method="PATCH", payload={"enabled": enabled}, deadline_seconds=40
    )
    if status != 200 or value.get("status") == "error":
        raise RuntimeError("actual framework unload/reload failed")


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, timeout=50)
    if result.returncode:
        raise RuntimeError("controlled acceptance container action failed")


def wait_idle(token, session):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        status, value = request("/api/v1/chat/sessions/" + session, token=token)
        value = unwrap(value)
        if status == 200 and value.get("is_running") is False and not value.get("active_runs"):
            return
        time.sleep(0.5)
    raise RuntimeError("original agent is still live; don't send recovery yet")


def restore(record):
    value = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    if value.get("cli_node") == record["injected_node"]:
        if record["original_node_present"]:
            value["cli_node"] = record["original_node"]
        else:
            value.pop("cli_node", None)
        staged = CONFIG.with_suffix(".boundary.tmp")
        staged.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(staged, CONFIG)
    elif value.get("cli_node", "/usr/local/bin/node") != record["original_node"]:
        raise RuntimeError("CLI config changed independently; inspect before restoring")


def inspect_record(token, state, record):
    rows = collector.new_operations(token, record["before"])
    rows = [r for r in rows if r["plan"].get("fields", {}).get("title") == record["title"]]
    if (
        len(rows) != 1
        or rows[0]["kind"] != "create"
        or rows[0]["attempt"] != 1
        or rows[0]["remote_id"] is not None
        or rows[0]["state"] != "outcome_unknown"
    ):
        raise RuntimeError("remote stdout loss didn't retain exactly one unknown attempt")
    record["operation"] = rows[0]
    # The test marker knows the remote ID, but is deliberately never imported
    # into the plugin. Query through the real native model and retain uncertainty.
    result = collector.send_once(
        token,
        state,
        record,
        "readonly_recovery",
        f"刚才“{record['title']}”的创建过程中验收实例退出/插件卸载，结果可能未知。请只查询这个完整标题，并用notido_check核查实际操作 {rows[0]['id']}。不要新建、重建、修改或删除；没有可靠ID时账本继续未知，不把远端查询到任务说成原操作已核验。",
    )
    if result["operations"]:
        raise RuntimeError("recovery produced a new write")
    tasks = [
        t
        for n, v in collector.tool_results(result["output"])
        if n == "notido_query"
        for t in v.get("tasks", [])
        if t.get("title") == record["title"]
    ]
    # The general corpus helper intentionally excludes unsuccessful results.
    # This fault case must inspect the actual unknown result rather than
    # discarding it because its error field is present.
    calls, checks = {}, []
    for entry in result["output"].get("tools", []):
        payload = entry["payload"]
        if entry["kind"] == "tool_call":
            calls[payload["id"]] = payload.get("name")
        elif entry["kind"] == "tool_call_result" and calls.get(payload["id"]) == "notido_check":
            value = payload.get("result")
            value = json.loads(value) if isinstance(value, str) else value
            if isinstance(value, dict):
                checks.append(value)
    if (
        len(tasks) != 1
        or tasks[0]["id"] != record["remote_marker"]["id"]
        or not any(
            v.get("operation_id") == rows[0]["id"] and v.get("state") == "outcome_unknown"
            for v in checks
        )
    ):
        raise RuntimeError("read-only remote/unknown recovery evidence incomplete")
    current = unwrap(request(PREFIX + "operations/" + rows[0]["id"], token=token)[1])
    if (
        current["attempt"] != 1
        or current["state"] != "outcome_unknown"
        or current["remote_id"] is not None
    ):
        raise RuntimeError("recovery adopted an unreliable ID or replayed the write")
    record["recovered_operation"] = current
    record["phase"] = "verified"
    collector.save(state)


def main(recover=False, batch="v7"):
    global JOURNAL
    if batch != "v7":
        JOURNAL = ROOT / f"runtime-data/native-shutdown-{batch}-results.json"
    if PORT != 16190 or collector.CONTAINER != CONTAINER:
        raise RuntimeError("reuse the sole current acceptance instance")
    collector.JOURNAL = JOURNAL
    if JOURNAL.exists():
        state = json.loads(JOURNAL.read_text(encoding="utf-8"))
    else:
        token = wait_ready()
        project = json.loads(
            (ROOT / "runtime-data/test-project.json").read_text(encoding="utf-8-sig")
        )
        settings = unwrap(request(PREFIX + "settings", token=token)[1])["settings"]
        if project["name"] != "NotiDo 验收" or settings["allowed_projects"] != [project["id"]]:
            raise RuntimeError("dedicated authorized test scope required")
        state = {**collector.runtime_version(), "project_id": project["id"], "cases": {}}
        collector.save(state)
    if recover:
        for record in state["cases"].values():
            restore(record)
        docker("start", CONTAINER)
        token = wait_ready()
        toggle(token, True)
        token = wait_ready()
        for record in state["cases"].values():
            if record["phase"] != "verified" and record.get("remote_marker"):
                wait_idle(token, record["session"])
                inspect_record(token, state, record)
        print(json.dumps({"recovery_only": True, "writes": 0}), flush=True)
        return
    token = wait_ready()
    if any(state.get(k) != v for k, v in collector.runtime_version().items()):
        raise RuntimeError("don't mix installed versions")
    for mode in ("unload", "sigterm"):
        record = state["cases"].setdefault(
            mode,
            {
                "id": mode,
                "phase": "ready",
                "title": "NotiDo 验收 · V7退出边界 · "
                + ("" if batch == "v7" else batch + " · ")
                + mode,
            },
        )
        if record["phase"] == "verified":
            continue
        if record["phase"] != "ready":
            raise RuntimeError("interrupted case must only use --recover, never resend")
        probe_name = mode if batch == "v7" else batch + "-" + mode
        folder = DATA / "acceptance-probes" / probe_name
        if folder.exists():
            raise RuntimeError("old boundary files exist; inspect without overwriting")
        folder.mkdir(parents=True)
        shutil.copyfile(ROOT / "tools/native_boundary_node.mjs", folder / "node-boundary")
        config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        record.update(
            {
                "session": bind_session(token),
                "before": sorted(collector.ledger_ids(token)),
                "original_node": config.get("cli_node", "/usr/local/bin/node"),
                "original_node_present": "cli_node" in config,
                "injected_node": "/AstrBot/data/acceptance-probes/" + probe_name + "/node-boundary",
            }
        )
        if record["original_node"] != "/usr/local/bin/node":
            raise RuntimeError("inspect nonstandard Node config")
        boundary = {
            "mode": "hold_create",
            "real_node": record["original_node"],
            "real_script": config.get(
                "cli_script", "/opt/notido-cli/node_modules/@suibiji/dida-cli/dist/index.js"
            ),
            "project_id": state["project_id"],
            "title": record["title"],
            "started": "/AstrBot/data/acceptance-probes/" + probe_name + "/started.json",
            "applied": "/AstrBot/data/acceptance-probes/" + probe_name + "/applied.json",
        }
        (folder / "boundary.json").write_text(json.dumps(boundary), encoding="utf-8")
        docker("exec", CONTAINER, "chmod", "700", record["injected_node"])
        record["phase"] = "injecting_runner"
        collector.save(state)
        config["cli_node"] = record["injected_node"]
        CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        future = None
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            toggle(token, False)
            toggle(token, True)
            token = wait_ready()
            record["phase"] = "native_request_sent"
            collector.save(state)
            future = pool.submit(
                timed_stream,
                token,
                record["session"],
                f"请在NotiDo 验收清单新建一项独立无日期任务，标题完整为“{record['title']}”，备注“专用退出边界验收”。只创建这一个任务，不操作其他任务，不完成或删除。",
            )
            deadline = time.monotonic() + 60
            while not (folder / "applied.json").exists():
                if future.done() or time.monotonic() >= deadline:
                    raise RuntimeError("no confirmed remote boundary; never resend")
                time.sleep(0.1)
            record["remote_marker"] = json.loads(
                (folder / "applied.json").read_text(encoding="utf-8")
            )
            record["phase"] = "remote_success_before_stdout"
            collector.save(state)
            if mode == "unload":
                toggle(token, False)
            else:
                docker("stop", "--time", "35", CONTAINER)
            record["phase"] = "stopped_after_write"
            collector.save(state)
        finally:
            restore(record)
            if mode == "sigterm":
                docker("start", CONTAINER)
            token = wait_ready() if mode == "sigterm" else token
            toggle(token, True)
            token = wait_ready()
            if future is not None:
                try:
                    record["original_stream"] = future.result(timeout=30)
                except Exception as exc:
                    record["original_stream_error_type"] = type(exc).__name__
            pool.shutdown(wait=False)
            collector.save(state)
        wait_idle(token, record["session"])
        inspect_record(token, state, record)
        print(
            json.dumps(
                {
                    "case": mode,
                    "remote_creates": 1,
                    "attempts": 1,
                    "unknown_without_id": True,
                    "recovery_writes": 0,
                    "pass": True,
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--batch", choices=("v7", "v7-r2"), default="v7")
    options = parser.parse_args()
    main(options.recover, options.batch)
