"""Opt-in real attachment check; imported verified target, no model or channel send."""

import asyncio
import json
import time
from datetime import UTC, datetime

from notido.cli import CLIRunner, DidaGateway
from notido.db import execute
from notido.keys import canonical, key, uid
from notido.models import InputEnvelope, Segment, Settings
from notido.service import Service
from tools.live_same_name_smoke import ROOT, save


class LocalBridge:
    instance_id = "supplement-acceptance"
    provider_id = ""

    def __init__(self, original):
        self.original = original

    async def acquire_material(self, source_id):
        return self.original, "NotiDo-跨会话补件验收.txt"

    async def call_provider(self, *args, **kwargs):
        raise RuntimeError("supplement must not call a Provider")

    async def reply(self, origin, body):
        return "unknown"


async def main():
    private = ROOT / "runtime-data"
    project = json.loads((private / "test-project.json").read_text(encoding="utf-8-sig"))
    if project["name"] != "NotiDo 验收":
        raise RuntimeError("dedicated project required")
    task_id = json.loads((private / "live-smoke.json").read_text(encoding="utf-8"))[-1]["task_id"]
    gateway = DidaGateway(
        CLIRunner(
            r"D:\Program Files\nodejs\node.exe",
            str(ROOT / "node_modules/@suibiji/dida-cli/dist/index.js"),
            private / "cli-home",
        ),
        attachment_runner=CLIRunner(
            r"D:\Program Files\nodejs\node.exe",
            str(ROOT / "tools/attachment-cli.mjs"),
            private / "cli-home",
        ),
        attachment_verified=True,
    )
    target = await gateway.get(project["id"], task_id)
    if target["title"] != "NotiDo 验收 · 时刻与原生附件" or target.get("status", 0) != 0:
        raise RuntimeError("expected existing target required")
    original = private / "supplement-original.txt"
    original.write_text("NotiDo 跨会话明确任务补件验收\n不含真实通知或身份。\n", encoding="utf-8")
    service = Service(private / "live-supplement-data", LocalBridge(original), gateway)
    service.owner_lock.acquire(timeout=0)
    journal = private / "live-supplement-results.json"
    try:
        await service.db.initialize()
        if journal.exists():
            state = json.loads(journal.read_text(encoding="utf-8"))
            if state["task_id"] != task_id or state["project_id"] != project["id"]:
                raise RuntimeError("journal target mismatch")
        else:
            settings = Settings(
                account_ref="supplement-test-cn",
                credential_generation=1,
                default_project=project["id"],
                allowed_projects=[project["id"]],
                min_free_bytes=0,
            )
            group, operation = uid(), uid()
            async with service.db.transaction() as conn:
                await execute(
                    conn,
                    "INSERT INTO account_scopes VALUES (:a,'personal','cn',NULL,1,'active',:t)",
                    {"a": settings.account_ref, "t": time.time()},
                )
                await execute(
                    conn, "UPDATE settings SET payload=:p", {"p": settings.model_dump_json()}
                )
                for session in ("original-session", "supplement-session"):
                    await execute(
                        conn,
                        "INSERT INTO sessions(id,user_id) VALUES (:s,'personal')",
                        {"s": session},
                    )
                    await execute(
                        conn,
                        "INSERT INTO actor_bindings VALUES (:id,'personal',:i,'local-user',:s,1,0)",
                        {"id": uid(), "i": service.bridge.instance_id, "s": session},
                    )
                await execute(
                    conn,
                    "INSERT INTO material_groups VALUES (:g,'personal','original-session','direct','completed',:t,:t,:t,0)",
                    {"g": group, "t": time.time()},
                )
                parent = {
                    "plan_id": uid(),
                    "kind": "create",
                    "account_ref": settings.account_ref,
                    "project_id": project["id"],
                    "project_name": project["name"],
                    "group_id": group,
                    "session_key": "original-session",
                    "fields": {"title": target["title"]},
                }
                await execute(
                    conn,
                    "INSERT INTO operations(id,user_id,account_ref,kind,operation_key,plan,state,remote_id,created_at) VALUES (:id,'personal',:a,'create',:k,:p,'succeeded',:r,:t)",
                    {
                        "id": operation,
                        "a": settings.account_ref,
                        "k": key("imported-verified-target", task_id),
                        "p": canonical(parent),
                        "r": task_id,
                        "t": time.time(),
                    },
                )
            state = {"task_id": task_id, "project_id": project["id"], "attempted": False}
            save(journal, state)
        if not state.get("group_id"):
            event = InputEnvelope(
                event_id=uid(),
                framework_instance_id=service.bridge.instance_id,
                session_key="supplement-session",
                actor_key="local-user",
                message_key="supplement-message-1",
                received_at=datetime.now(UTC),
                source_kind="manual_notice",
                reply_origin_ref="local:no-send",
                segments=[
                    Segment(source_id=uid(), kind="text", text=f"补充任务 {task_id}"),
                    Segment(source_id=uid(), kind="file"),
                ],
            )
            result = await service.intake(event)
            state.update(
                {"group_id": result["group_id"], "envelope": event.model_dump(mode="json")}
            )
            save(journal, state)
        if not state.get("upload_id"):
            acquisitions = await service.db.read(
                "SELECT id FROM assets WHERE group_id=:g AND state='pending'",
                {"g": state["group_id"]},
            )
            for asset in acquisitions:
                await service.acquire({"asset_id": asset["id"]})
            await service.read_materials({"group_id": state["group_id"]})
            uploads = await service.db.read("SELECT * FROM operations WHERE kind='upload'")
            if len(uploads) != 1:
                raise RuntimeError("one upload, no new task required")
            state["upload_id"] = uploads[0]["id"]
            save(journal, state)
        if not state["attempted"]:
            state["attempted"] = True
            save(journal, state)
            await service.execute_operation({"operation_id": state["upload_id"]})
        else:
            await service.reconcile({"operation_id": state["upload_id"]})
        upload = (
            await service.db.read(
                "SELECT * FROM operations WHERE id=:id", {"id": state["upload_id"]}
            )
        )[0]
        plan = json.loads(upload["plan"])
        actual = await gateway.inspect_upload(plan, plan["attachment_id"])
        if (
            upload["state"] != "succeeded"
            or upload["attempt"] != 1
            or actual["sha256"] != plan["hash"]
            or actual["task_id"] != task_id
        ):
            raise RuntimeError("native original was not verified")
        event = InputEnvelope.model_validate_json(canonical(state["envelope"]))
        if not (await service.intake(event))["duplicate"]:
            raise RuntimeError("message duplicate was not recognized")
        repeat = event.model_copy(
            update={
                "event_id": uid(),
                "message_key": "supplement-message-2",
                "received_at": datetime.now(UTC),
            }
        )
        result = await service.intake(repeat)
        for asset in await service.db.read(
            "SELECT id FROM assets WHERE group_id=:g AND state='pending'", {"g": result["group_id"]}
        ):
            await service.acquire({"asset_id": asset["id"]})
        await service.read_materials({"group_id": result["group_id"]})
        if len(await service.db.read("SELECT * FROM operations")) != 2:
            raise RuntimeError("duplicate hash or supplement created another operation")
        state["checks"] = [
            {"check": "cross_session_explicit_target_native_hash", "pass": True},
            {"check": "same_message_and_same_hash_no_extra_operation", "pass": True},
            {"check": "imported_target_reused_no_provider_or_new_task", "pass": True},
        ]
        save(journal, state)
        print(json.dumps(state["checks"]))
    finally:
        await service.db.close()
        service.owner_lock.release()


if __name__ == "__main__":
    asyncio.run(main())
