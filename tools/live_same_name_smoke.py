"""Opt-in dedicated-account check of real Service attachment naming and no replay."""

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

ROOT = Path(__file__).resolve().parent.parent


class LocalBridge:
    instance_id = "local-acceptance"
    provider_id = ""

    async def reply(self, origin, body):
        return "unknown"  # This probe never sends a downstream message.


def save(path, value):
    staged = path.with_suffix(".tmp")
    with staged.open("w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(staged, path)


async def main():
    private = ROOT / "runtime-data"
    project = json.loads((private / "test-project.json").read_text(encoding="utf-8-sig"))
    if project["name"] != "NotiDo 验收":
        raise RuntimeError("dedicated project required")
    task_id = json.loads((private / "live-smoke.json").read_text(encoding="utf-8"))[-1]["task_id"]
    node = r"D:\Program Files\nodejs\node.exe"
    gateway = DidaGateway(
        CLIRunner(
            node, str(ROOT / "node_modules/@suibiji/dida-cli/dist/index.js"), private / "cli-home"
        ),
        attachment_runner=CLIRunner(
            node, str(ROOT / "tools/attachment-cli.mjs"), private / "cli-home"
        ),
    )
    gateway.attachment_verified = True
    target = await gateway.get(project["id"], task_id)
    if target["title"] != "NotiDo 验收 · 时刻与原生附件" or target.get("status", 0) != 0:
        raise RuntimeError("expected existing acceptance task required")
    service = Service(private / "live-same-name-data", LocalBridge(), gateway)
    service.owner_lock.acquire(timeout=0)
    journal = private / "live-same-name-results.json"
    try:
        await service.db.initialize()
        if journal.exists():
            state = json.loads(journal.read_text(encoding="utf-8"))
            if state["project_id"] != project["id"] or state["task_id"] != task_id:
                raise RuntimeError("journal target mismatch")
        else:
            settings = Settings(
                account_ref="dedicated-cn-acceptance",
                credential_generation=1,
                default_project=project["id"],
                allowed_projects=[project["id"]],
                min_free_bytes=0,
            )
            group = uid()
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
            parent = {
                "plan_id": uid(),
                "group_id": group,
                "session_key": group,
                "account_ref": settings.account_ref,
                "credential_generation": 1,
                "authorization_revision": 0,
                "identity_snapshot": settings.identity.model_dump(),
                "project_id": project["id"],
                "project_name": project["name"],
                "origin": "local-acceptance:no-send",
                "request_origin": "webui",
                "webui_actor": "authorized-local-acceptance",
                "processing_deadline_at": time.time() + 180,
            }
            for label in ("A", "B"):
                content = f"NotiDo 同名原件验收 {label}\n不含真实通知或身份。\n".encode()
                blob = await service.blobs.save(AsyncFile(io.BytesIO(content)), min_free_bytes=0)
                asset_id = uid()
                async with service.db.transaction() as conn:
                    await service.register_blob(conn, blob)
                    await execute(
                        conn,
                        "INSERT INTO assets VALUES (:id,'personal',:g,NULL,:h,'NotiDo-同名验收.txt',:s,'ready',NULL,:t,0)",
                        {
                            "id": asset_id,
                            "g": group,
                            "h": blob["hash"],
                            "s": uid(),
                            "t": time.time(),
                        },
                    )
                    await service.schedule_upload(conn, parent, task_id, asset_id)
            state = {
                "project_id": project["id"],
                "task_id": task_id,
                "attempted": {},
                "results": [],
            }
            save(journal, state)
        operations = await service.db.read("SELECT * FROM operations ORDER BY created_at,id")
        if len(operations) != 2:
            raise RuntimeError("exactly two attachment plans required")
        names = []
        for operation in operations:
            plan = json.loads(operation["plan"])
            names.append(plan["name"])
            if not state["attempted"].get(operation["id"]):
                state["attempted"][operation["id"]] = True
                save(journal, state)  # Persist before claim, including on a crashed first run.
                await service.execute_operation({"operation_id": operation["id"]})
            actual = await gateway.inspect_upload(plan, plan["attachment_id"])
            if (
                actual["sha256"] != plan["hash"]
                or actual["task_id"] != task_id
                or actual["metadata"]["fileName"] != plan["name"]
            ):
                raise RuntimeError("native name or original bytes mismatch")
            state["results"].append(
                {
                    "operation_id": operation["id"],
                    "hash_verified": True,
                    "name_verified": True,
                    "actual": actual,
                }
            )
            save(journal, state)
            print(
                json.dumps(
                    {"check": "same_original_name_distinct_native_name_and_hash", "pass": True}
                )
            )
        if names[0] == names[1]:
            raise RuntimeError("names did not distinguish distinct bytes")
        before = len(await service.db.read("SELECT * FROM operations"))
        async with service.db.transaction() as conn:
            asset_id = (
                await service.db.read(
                    "SELECT asset_id FROM task_attachment_links ORDER BY id LIMIT 1"
                )
            )[0]["asset_id"]
            await service.schedule_upload(
                conn, json.loads(operations[0]["plan"]), task_id, asset_id
            )
        if len(await service.db.read("SELECT * FROM operations")) != before:
            raise RuntimeError("same hash generated duplicate operation")
        print(json.dumps({"check": "verified_same_hash_does_not_schedule_again", "pass": True}))
    finally:
        await service.db.close()
        service.owner_lock.release()


if __name__ == "__main__":
    asyncio.run(main())
