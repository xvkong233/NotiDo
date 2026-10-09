"""Real registered attachment + withheld CLI output, timeout, reopen DB, read-only recovery."""

import asyncio
import io
import json
import os
import time
from pathlib import Path

from notido.cli import CLIRunner, DidaGateway
from notido.db import execute
from notido.keys import uid
from notido.materials import AsyncFile
from notido.models import Settings
from notido.service import Service
from tools.live_same_name_smoke import LocalBridge, save

ROOT = Path(__file__).resolve().parent.parent


class TimeoutRunner(CLIRunner):
    async def call(self, args, **kwargs):
        if args[0] == "upload":
            kwargs["deadline_seconds"] = 10
        return await super().call(args, **kwargs)


async def main():
    private = ROOT / "runtime-data"
    project = json.loads((private / "test-project.json").read_text(encoding="utf-8-sig"))
    if project["name"] != "NotiDo 验收":
        raise RuntimeError("dedicated project required")
    task = json.loads((private / "live-smoke.json").read_text(encoding="utf-8"))[-1]["task_id"]
    node = r"D:\Program Files\nodejs\node.exe"
    marker = private / "upload-timeout-child-result.json"
    wrapper = private / "upload-timeout-wrapper.mjs"
    wrapper.write_text(
        "import {spawn} from 'node:child_process';import {writeFileSync} from 'node:fs';\n"
        + "const cli="
        + json.dumps(str(ROOT / "tools/attachment-cli.mjs"))
        + ";\n"
        + "const marker="
        + json.dumps(str(marker))
        + ";\n"
        + "const args=process.argv.slice(2);const child=spawn(process.execPath,[cli,...args],{stdio:['ignore','pipe','ignore']});let data='';\n"
        + "child.stdout.on('data',chunk=>{data+=chunk;if(data.length>1048576){child.kill();process.exit(1);}});\n"
        + "child.on('exit',code=>{if(args[0]==='upload'&&code===0){writeFileSync(marker,data,{mode:0o600});setInterval(()=>{},1000);}else{process.stdout.write(data);process.exit(code||0);}});\n",
        encoding="utf-8",
    )
    os.chmod(wrapper, 0o600)
    gateway = DidaGateway(
        CLIRunner(
            node, str(ROOT / "node_modules/@suibiji/dida-cli/dist/index.js"), private / "cli-home"
        ),
        attachment_runner=TimeoutRunner(node, str(wrapper), private / "cli-home"),
    )
    gateway.attachment_verified = True
    actual_task = await gateway.get(project["id"], task)
    if actual_task["title"] != "NotiDo 验收 · 时刻与原生附件" or actual_task.get("status", 0) != 0:
        raise RuntimeError("existing acceptance task required")
    root = private / "live-upload-timeout-data"
    journal = private / "live-upload-timeout-results.json"
    service = Service(root, LocalBridge(), gateway)
    service.owner_lock.acquire(timeout=0)
    try:
        await service.db.initialize()
        if journal.exists():
            record = json.loads(journal.read_text(encoding="utf-8"))
            if record["project_id"] != project["id"] or record["task_id"] != task:
                raise RuntimeError("journal scope mismatch")
        else:
            settings = Settings(
                account_ref="dedicated-cn-timeout-acceptance",
                credential_generation=1,
                default_project=project["id"],
                allowed_projects=[project["id"]],
                min_free_bytes=0,
            )
            group, asset_id = uid(), uid()
            blob = await service.blobs.save(
                AsyncFile(io.BytesIO("NotiDo 未知上传回读验收\n不含真实通知或身份。\n".encode())),
                min_free_bytes=0,
            )
            parent = {
                "plan_id": uid(),
                "group_id": group,
                "session_key": group,
                "account_ref": settings.account_ref,
                "credential_generation": 1,
                "identity_snapshot": settings.identity.model_dump(),
                "project_id": project["id"],
                "project_name": project["name"],
                "origin": "local-acceptance:no-send",
                "request_origin": "webui",
                "webui_actor": "authorized-local-acceptance",
                "processing_deadline_at": time.time() + 90,
            }
            async with service.db.transaction() as conn:
                await execute(
                    conn,
                    "INSERT INTO account_scopes VALUES (:a,'personal','cn',NULL,1,'active',:t)",
                    {"a": settings.account_ref, "t": time.time()},
                )
                await execute(
                    conn, "UPDATE settings SET payload=:p", {"p": settings.model_dump_json()}
                )
                await execute(
                    conn, "INSERT INTO sessions(id,user_id) VALUES (:g,'personal')", {"g": group}
                )
                await execute(
                    conn,
                    "INSERT INTO material_groups VALUES (:g,'personal',:g,'direct','partially_done',:t,:t,:t,0)",
                    {"g": group, "t": time.time()},
                )
                await service.register_blob(conn, blob)
                await execute(
                    conn,
                    "INSERT INTO assets VALUES (:id,'personal',:g,NULL,:h,'NotiDo-未知上传验收.txt',:s,'ready',NULL,:t,0)",
                    {"id": asset_id, "g": group, "h": blob["hash"], "s": uid(), "t": time.time()},
                )
                await service.schedule_upload(conn, parent, task, asset_id)
            record = {"project_id": project["id"], "task_id": task, "attempted": False}
            save(journal, record)
        operation = (await service.db.read("SELECT * FROM operations"))[0]
        if not record["attempted"]:
            record["attempted"] = True
            save(journal, record)
            await service.execute_operation({"operation_id": operation["id"]})
            operation = (await service.db.read("SELECT * FROM operations"))[0]
            if (
                operation["state"] != "outcome_unknown"
                or operation["remote_id"] is not None
                or operation["attempt"] != 1
                or not marker.exists()
            ):
                raise RuntimeError("expected real post-registration CLI timeout")
            record["timeout_state_verified"] = True
            save(journal, record)
            print(
                json.dumps(
                    {"check": "real_registration_then_cli_timeout_without_remote_id", "pass": True}
                )
            )
    finally:
        await service.db.close()
        service.owner_lock.release()
    # A new service owner reopens the actual persisted unknown operation. No upload replay.
    restarted = Service(root, LocalBridge(), gateway)
    restarted.owner_lock.acquire(timeout=0)
    try:
        await restarted.db.initialize()
        operation = (await restarted.db.read("SELECT * FROM operations"))[0]
        await restarted.reconcile({"operation_id": operation["id"]})
        verified = (await restarted.db.read("SELECT * FROM operations"))[0]
        if (
            verified["state"] != "succeeded"
            or verified["attempt"] != 1
            or not verified["remote_id"]
        ):
            raise RuntimeError("read-only original-byte recovery failed")
        record["reopened_read_only_recovery_verified"] = True
        record["result"] = json.loads(verified["result"])
        save(journal, record)
        print(
            json.dumps(
                {"check": "reopened_ledger_hash_recovery_without_upload_replay", "pass": True}
            )
        )
    finally:
        await restarted.db.close()
        restarted.owner_lock.release()


if __name__ == "__main__":
    asyncio.run(main())
