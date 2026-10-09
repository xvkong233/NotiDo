"""Native facade + real dedicated Dida account, controlled safe boundary failures.

No Provider is called here: native chat interpretation is tested separately.
Fault injection returns side_effect=none before invoking the external CLI, never
exhausts an account's quota. Actual recovered uploads verify downloaded bytes.
"""

import asyncio
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace

from notido.api import PagesAPI
from notido.cli import CLIResponse, CLIRunner, DidaGateway
from notido.db import execute
from notido.keys import uid
from notido.models import InputEnvelope, Segment, Settings
from notido.native import NativeTools
from notido.service import Service
from tools.live_same_name_smoke import ROOT, save


class Bridge:
    instance_id = "native-partial-acceptance"

    def identity(self, event):
        return {
            "i": self.instance_id,
            "a": event.envelope.actor_key,
            "s": event.envelope.session_key,
        }

    def normalize_event(self, event):
        return event.envelope

    def release_material_refs(self, envelope):
        pass

    async def acquire_material(self, source_id):
        return (
            ROOT / f"runtime-data/native-partial-{source_id}.txt",
            f"NotiDo-部分失败-{source_id}.txt",
        )

    async def reply(self, *args):
        raise RuntimeError("native probe must not send an independent receipt")


class ControlledGateway:
    def __init__(self, real):
        self.real = real
        self.reject_create = False
        self.reject_upload = False

    def __getattr__(self, name):
        return getattr(self.real, name)

    async def write(self, kind, project, fields, task=None):
        if kind != "create" or not fields.get("title", "").startswith("NotiDo 验收 · 原生部分失败"):
            raise RuntimeError("probe only creates its dedicated synthetic targets")
        if self.reject_create:
            return CLIResponse(error="AUTH_REQUIRED", side_effect="none")
        return await self.real.write(kind, project, fields, task)

    async def upload(self, plan):
        if self.reject_upload:
            return CLIResponse(error="ATTACHMENT_QUOTA", side_effect="none")
        return await self.real.upload(plan)


def incoming(state, label, *, materials=False):
    return SimpleNamespace(
        message_obj=SimpleNamespace(message_id=state["message"] + label),
        envelope=InputEnvelope(
            event_id=state["message"] + label,
            framework_instance_id=Bridge.instance_id,
            actor_key="local-acceptance",
            session_key=state["session"],
            message_key=state["message"] + label,
            received_at=datetime.fromisoformat(state["received_at"]),
            source_kind="direct_request",
            reply_origin_ref="local-acceptance:no-send",
            segments=[Segment(source_id="A", kind="file"), Segment(source_id="B", kind="file")]
            if materials
            else [Segment(source_id=label, kind="text", text="原生部分失败专用验收")],
        ),
    )


async def main():
    private = ROOT / "runtime-data"
    project = json.loads((private / "test-project.json").read_text(encoding="utf-8-sig"))
    if project["name"] != "NotiDo 验收":
        raise RuntimeError("dedicated authorized project required")
    journal = private / "live-native-partial-results.json"
    state = (
        json.loads(journal.read_text(encoding="utf-8"))
        if journal.exists()
        else {
            "session": uid(),
            "message": uid(),
            "received_at": datetime.now(UTC).isoformat(),
            "phase": "ready",
            "project_id": project["id"],
            "faults": "controlled safe failures before external CLI",
        }
    )
    if state["project_id"] != project["id"]:
        raise RuntimeError("cannot mix account/list scope")
    save(journal, state)
    node = r"D:\Program Files\nodejs\node.exe"
    gateway = ControlledGateway(
        DidaGateway(
            CLIRunner(
                node,
                str(ROOT / "node_modules/@suibiji/dida-cli/dist/index.js"),
                private / "cli-home",
            ),
            task_extension=CLIRunner(
                node, str(ROOT / "tools/task-extension.mjs"), private / "cli-home"
            ),
            attachment_runner=CLIRunner(
                node, str(ROOT / "tools/attachment-cli.mjs"), private / "cli-home"
            ),
            attachment_verified=True,
        )
    )
    service = Service(private / "live-native-partial-data", Bridge(), gateway)
    service.owner_lock.acquire(timeout=0)
    try:
        await service.db.initialize()
        settings, _ = await service.db.settings()
        if not settings.account_ref:
            settings = Settings(
                account_ref="dedicated-cn-native-partial-acceptance",
                credential_generation=1,
                default_project=project["id"],
                allowed_projects=[project["id"]],
                min_free_bytes=0,
            )
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
                    conn,
                    "INSERT INTO actor_bindings VALUES (:id,'personal',:i,'local-acceptance',:s,1,0)",
                    {"id": uid(), "i": Bridge.instance_id, "s": state["session"]},
                )
        elif settings.allowed_projects != [project["id"]]:
            raise RuntimeError("stored scope changed")
        native = NativeTools(service)

        async def call(label, operation, args, event_label="tasks"):
            if label not in state:
                # Exact event and arguments are journaled before invoking the facade.
                state[label] = {
                    "phase": "sent",
                    "operation": operation,
                    "args": args,
                    "event": event_label,
                }
                save(journal, state)
                result = await native.invoke(incoming(state, event_label), operation, args)
                state[label]["result"] = json.loads(result)
                state[label]["phase"] = "received"
                save(journal, state)
            if state[label]["phase"] != "received":
                raise RuntimeError(
                    "interrupted native call; inspect persisted ledger without replay"
                )
            return state[label]["result"]

        first = await call(
            "create_a", "create", {"request_key": "A", "title": "NotiDo 验收 · 原生部分失败 · A"}
        )
        gateway.reject_create = True
        failed = await call(
            "create_b", "create", {"request_key": "B", "title": "NotiDo 验收 · 原生部分失败 · B"}
        )
        gateway.reject_create = False
        if first["state"] != "succeeded" or failed["state"] != "failed_safe":
            raise RuntimeError("expected one actual success and one controlled safe failure")
        api = PagesAPI(service)

        async def retry(label, operation_id):
            row = (
                await service.db.read("SELECT * FROM operations WHERE id=:id", {"id": operation_id})
            )[0]
            if label not in state:
                state[label] = {"request_id": uid(), "expected_revision": row["revision"]}
                save(journal, state)

            async def body():
                return state[label]

            await api.retry(SimpleNamespace(username="local-acceptance", json=body), operation_id)
            await service.execute_operation({"operation_id": operation_id})
            updated = (
                await service.db.read("SELECT * FROM operations WHERE id=:id", {"id": operation_id})
            )[0]
            if updated["state"] != "succeeded":
                raise RuntimeError("safe-failure recovery incomplete; inspect without new request")
            return updated

        recovered = await retry("retry_create_b", failed["operation_id"])
        if "assets" not in state:
            for label in ("A", "B"):
                (private / f"native-partial-{label}.txt").write_text(
                    f"NotiDo 原生部分失败原件 {label}\n", encoding="utf-8"
                )
            result = await native.invoke(
                incoming(state, "materials", materials=True), "materials", {}
            )
            manifest = json.loads(result.content[0].text)
            if len(manifest["assets"]) != 2:
                raise RuntimeError("both actual originals required")
            state["assets"] = manifest["assets"]
            save(journal, state)
        if "selection" not in state:
            selected = await native.invoke(
                incoming(state, "select"), "query", {"keyword": "NotiDo 验收 · 原生部分失败 · A"}
            )
            tasks = json.loads(selected)["tasks"]
            if len(tasks) != 1 or tasks[0]["id"] != first["remote_id"]:
                raise RuntimeError("unique exact target required")
            state["selection"] = tasks[0]["selection_ref"]
            save(journal, state)
        args = {
            "request_key": "attach-A",
            "selection_ref": state["selection"],
            "asset_id": state["assets"][0]["id"],
        }
        attached = await call("attach_a", "attach", args, "attachments")
        gateway.reject_upload = True
        args = {**args, "request_key": "attach-B", "asset_id": state["assets"][1]["id"]}
        blocked = await call("attach_b", "attach", args, "attachments")
        gateway.reject_upload = False
        if attached["state"] != "succeeded" or blocked["state"] != "failed_safe":
            raise RuntimeError("expected actual attachment + controlled quota failure")
        recovered_upload = await retry("retry_attach_b", blocked["operation_id"])
        rows = await service.db.read("SELECT * FROM operations")
        creates = [row for row in rows if row["kind"] == "create"]
        uploads = [row for row in rows if row["kind"] == "upload"]
        checks = [
            {
                "check": "real_partial_create_recovers_only_failed_action",
                "pass": len(creates) == 2 and sorted(row["attempt"] for row in creates) == [1, 2],
            },
            {
                "check": "real_partial_upload_recovers_only_failed_original",
                "pass": len(uploads) == 2 and sorted(row["attempt"] for row in uploads) == [1, 2],
            },
            {
                "check": "actual_task_and_download_hash_readbacks",
                "pass": all(row["state"] == "succeeded" for row in rows)
                and all(
                    json.loads(row["result"])["actual_fields"].get("sha256")
                    == json.loads(row["plan"])["hash"]
                    for row in uploads
                ),
            },
            {
                "check": "native_path_no_planner_or_independent_receipt",
                "pass": not await service.db.read("SELECT * FROM receipt_records"),
            },
        ]
        state.update(
            phase="verified" if all(c["pass"] for c in checks) else "failed",
            checks=checks,
            operations=rows,
            recovered_task=recovered["remote_id"],
            recovered_upload=recovered_upload["remote_id"],
        )
        save(journal, state)
        print(json.dumps({"phase": state["phase"], "checks": checks}))
        if state["phase"] != "verified":
            raise RuntimeError("partial recovery evidence incomplete")
    finally:
        await service.db.close()
        service.owner_lock.release()


if __name__ == "__main__":
    asyncio.run(main())
