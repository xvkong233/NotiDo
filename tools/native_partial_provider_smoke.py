"""Actual native Provider partial upload receipt and recovery of only the failed upload.

The quota error is a controlled rejection before the external attachment CLI,
never a claim that the real account exhausted its quota. Every send is journaled.
"""

import hashlib
import json
import os
import shutil
import time
import uuid

from tools import native_corpus_smoke as collector
from tools.container_smoke import PREFIX, ROOT, request, unwrap, wait_ready
from tools.native_material_smoke import upload
from tools.native_performance_smoke import bind_session
from tools.native_shutdown_v7_smoke import CONFIG, DATA, docker, restore, toggle

JOURNAL = ROOT / "runtime-data/native-partial-provider-results.json"
TITLE = "NotiDo 验收 · 原生模型部分附件"


def operation(token, identifier):
    return unwrap(request(PREFIX + "operations/" + identifier, token=token)[1])


def main():
    collector.JOURNAL = JOURNAL
    token = wait_ready()
    version = collector.runtime_version()
    state = json.loads(JOURNAL.read_text(encoding="utf-8")) if JOURNAL.exists() else {}
    if not state:
        project = json.loads((ROOT / "runtime-data/test-project.json").read_text("utf-8-sig"))
        settings = unwrap(request(PREFIX + "settings", token=token)[1])["settings"]
        if project["name"] != "NotiDo 验收" or settings["allowed_projects"] != [project["id"]]:
            raise RuntimeError("dedicated authorized scope required")
        state = {
            **version,
            "project_id": project["id"],
            "record": {
                "id": "partial-provider",
                "session": bind_session(token),
                "phase": "prepared",
            },
        }
        collector.save(state)
    if any(state.get(k) != v for k, v in version.items()):
        raise RuntimeError("don't mix installed versions")
    record = state["record"]
    folder = DATA / "acceptance-probes/partial-provider"
    if record["phase"] == "prepared":
        if folder.exists():
            raise RuntimeError("unexpected old boundary directory; inspect without overwrite")
        folder.mkdir(parents=True)
        record["files"] = {}
        for suffix in ("甲", "乙"):
            path = folder / f"NotiDo-原生部分原件{suffix}.txt"
            path.write_text(
                f"本原件专属于独立任务{TITLE} · {suffix}。\n专用合成附件验收。\n", "utf-8"
            )
            record["files"][suffix] = {
                "hash": hashlib.sha256(path.read_bytes()).hexdigest(),
                "attachment": upload(token, path),
            }
        config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        record.update(
            {
                "original_node": config.get("cli_node", "/usr/local/bin/node"),
                "original_node_present": "cli_node" in config,
                "injected_node": "/AstrBot/data/acceptance-probes/partial-provider/node-boundary",
            }
        )
        if record["original_node"] != "/usr/local/bin/node":
            raise RuntimeError("nonstandard runner; inspect before injection")
        shutil.copyfile(ROOT / "tools/native_boundary_node.mjs", folder / "node-boundary")
        boundary = {
            "mode": "reject_upload",
            "real_node": record["original_node"],
            "attachment_script": config.get(
                "attachment_cli_script",
                "/AstrBot/data/plugins/astrbot_plugin_notido/tools/attachment-cli.mjs",
            ),
            "project_id": state["project_id"],
            "sha256": record["files"]["乙"]["hash"],
            "rejected": "/AstrBot/data/acceptance-probes/partial-provider/rejected.json",
        }
        (folder / "boundary.json").write_text(json.dumps(boundary), "utf-8")
        docker("exec", collector.CONTAINER, "chmod", "700", record["injected_node"])
        record["phase"] = "prepared_injection"
        collector.save(state)
    if record["phase"] == "prepared_injection":
        config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        config["cli_node"] = record["injected_node"]
        staged = CONFIG.with_suffix(".partial.tmp")
        staged.write_text(json.dumps(config, ensure_ascii=False, indent=2), "utf-8")
        os.replace(staged, CONFIG)
        try:
            toggle(token, False)
            toggle(token, True)
            token = wait_ready()
            message = [
                {
                    "type": "plain",
                    "text": f"请在NotiDo 验收清单新建两个独立无日期任务，完整标题分别为“{TITLE} · 甲”和“{TITLE} · 乙”，"
                    "备注为专用部分附件验收。两份原件各只挂到其正文指定的对应任务。先保存并读取原件，"
                    "保存任务及原生附件；如果一项失败，保留其他成功项并如实分项报告。只处理这两个新任务，"
                    "不修改其他任务，不完成不删除。",
                }
            ]
            message.extend(
                {
                    "type": "file",
                    "attachment_id": value["attachment"]["attachment_id"],
                    "filename": value["attachment"]["filename"],
                }
                for value in record["files"].values()
            )
            result = collector.send_once(token, state, record, "partial", message)
            record["operations"] = result["operations"]
            record["phase"] = "collected"
            collector.save(state)
        finally:
            restore(record)
            toggle(token, False)
            toggle(token, True)
            wait_ready()
    if record["phase"] == "collected":
        rows = record["operations"]
        if len(rows) == 2 and all(
            r["kind"] == "create" and r["state"] == "succeeded" for r in rows
        ):
            # Preserve the initial query failure. The acceptance wrapper exited
            # before its large stdout pipe flushed; no upload reached the CLI.
            # Continue existing tasks after fixing that wrapper, never resend
            # the original create request or replace the original output.
            record.setdefault(
                "collector_observation", "large_wrapper_stdout_truncated_before_upload"
            )
            original_groups = [
                value["group_id"]
                for name, value in collector.tool_results(record["partial"]["output"])
                if name == "notido_materials"
            ]
            if len(original_groups) != 1 or (folder / "rejected.json").exists():
                raise RuntimeError("unexpected partial boundary; inspect without replay")
            shutil.copyfile(ROOT / "tools/native_boundary_node.mjs", folder / "node-boundary")
            config = json.loads(CONFIG.read_text("utf-8-sig"))
            if config.get("cli_node", "/usr/local/bin/node") != record["original_node"]:
                raise RuntimeError("runner changed independently")
            config["cli_node"] = record["injected_node"]
            CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2), "utf-8")
            try:
                toggle(token, False)
                toggle(token, True)
                token = wait_ready()
                continued = collector.send_once(
                    token,
                    state,
                    record,
                    "attachment_followup",
                    "查询故障已恢复，请只继续刚才两份原件的挂接：原材料组引用 "
                    + original_groups[0]
                    + "。甲原件只挂甲任务、乙原件只挂乙任务。"
                    "两个已核验任务保持原样，不新建、修改、完成或删除；附件失败如实分项报告。",
                )
            finally:
                restore(record)
                toggle(token, False)
                toggle(token, True)
                wait_ready()
            rows = [*rows, *continued["operations"]]
            record["operations"] = rows
            collector.save(state)
        creates = [r for r in rows if r["kind"] == "create"]
        uploads = [r for r in rows if r["kind"] == "upload"]
        successful = [r for r in uploads if r["state"] == "succeeded"]
        failed = [r for r in uploads if r["state"] == "failed_safe"]
        if len(rows) != 4 or len(creates) != 2 or len(successful) != 1 or len(failed) != 1:
            raise RuntimeError("partial sample must contain exactly two creates and two uploads")
        if len(successful) != 1 or len(failed) != 1 or not (folder / "rejected.json").exists():
            raise RuntimeError("actual safe quota boundary not observed")
        expected = {TITLE + " · " + s for s in record["files"]}
        if {r["plan"]["fields"]["title"] for r in creates} != expected or any(
            r["state"] != "succeeded" or r["attempt"] != 1 for r in creates
        ):
            raise RuntimeError("actual task creates don't match")
        for suffix, row in (("甲", successful[0]), ("乙", failed[0])):
            target = next(
                r for r in creates if r["plan"]["fields"]["title"] == TITLE + " · " + suffix
            )
            if (
                row["plan"]["hash"] != record["files"][suffix]["hash"]
                or row["plan"]["task_id"] != target["remote_id"]
            ):
                raise RuntimeError("wrong original/target association")
        failure = failed[0]
        result = json.loads(failure["result"])
        if result.get("side_effect") != "none" or failure["attempt"] != 1:
            raise RuntimeError("failure lacks trustworthy zero-side-effect evidence")
        record["failed_operation"] = failure["id"]
        record["phase"] = "verified_partial"
        collector.save(state)
    if record["phase"] == "verified_partial":
        row = operation(token, record["failed_operation"])
        record["retry"] = {"request_id": str(uuid.uuid4()), "expected_revision": row["revision"]}
        record["phase"] = "retry_sent"
        collector.save(state)
        status, result = request(
            PREFIX + "operations/" + row["id"] + "/retry", token=token, payload=record["retry"]
        )
        if status != 200 or not unwrap(result).get("queued"):
            raise RuntimeError("safe failure recovery wasn't queued")
    if record["phase"] == "retry_sent":
        deadline = time.monotonic() + 35
        while True:
            row = operation(token, record["failed_operation"])
            if row["state"] == "succeeded":
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("recover existing operation, never create a new retry")
            time.sleep(0.5)
        record["recovered_operations"] = [operation(token, r["id"]) for r in record["operations"]]
        if any(
            r["state"] != "succeeded" or r["attempt"] != (2 if r["id"] == row["id"] else 1)
            for r in record["recovered_operations"]
        ):
            raise RuntimeError("safe recovery repeated successful operations")
        for uploaded in (r for r in record["recovered_operations"] if r["kind"] == "upload"):
            if (
                json.loads(uploaded["result"])["actual_fields"]["sha256"]
                != uploaded["plan"]["hash"]
            ):
                raise RuntimeError("recovered original download hash mismatch")
        result = collector.send_once(
            token,
            state,
            record,
            "recovery_receipt",
            "管理员已只补传失败的原件，未重建任何任务。请仅用notido_check核查本轮四个操作："
            + "、".join(r["id"] for r in record["operations"])
            + "。依据实际核验更新原材料组的结论；不要新建、修改、完成、删除或再次上传。",
        )
        if result["operations"]:
            raise RuntimeError("recovery receipt introduced a new write")
        record["phase"] = "verified"
        collector.save(state)
    print(json.dumps({"phase": record["phase"], "semantic_review_pending": True}), flush=True)


if __name__ == "__main__":
    main()
