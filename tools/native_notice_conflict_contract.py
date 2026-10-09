"""Real Dida notice guards using actual AstrBot public Node/event fixtures.

No Provider interprets this batch. Explicit fixture arguments test execution
guards against older/unknown provenance, external edits and completion.
"""

import asyncio
import hashlib
import json
import sys
import time
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path

from astrbot.api.message_components import Node, Plain

from notido.bridge import AstrBotBridge
from notido.cli import CLIRunner, DidaGateway
from notido.db import execute
from notido.models import Settings
from notido.native import NativeTools
from notido.service import Service
from tools.handler_contract_smoke import ContextFixture, event

PROJECT_ROOT = Path(sys.modules["notido"].__file__).resolve().parent.parent
FOLDER = Path("/AstrBot/data/acceptance-probes/notice-conflict")
JOURNAL = FOLDER / "results.json"


def save(state):
    staged = JOURNAL.with_suffix(".tmp")
    staged.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    staged.replace(JOURNAL)


class ExactGateway:
    def __init__(self, real, state):
        self.real, self.state = real, state
        self.writes = 0

    def __getattr__(self, name):
        return getattr(self.real, name)

    async def write(self, kind, project, fields, task=None):
        if project != self.state["project_id"]:
            raise RuntimeError("only the authorized dedicated test list is writable")
        if kind == "create":
            if self.state.get("remote_id") or task or fields.get("title") != self.state["title"]:
                raise RuntimeError("only one exact synthetic create is permitted")
        elif kind not in ("update", "complete") or task != self.state.get("remote_id") or not task:
            raise RuntimeError("only this batch's saved synthetic task may change")
        if fields.get("title", self.state["title"]) != self.state["title"]:
            raise RuntimeError("target title may not change")
        self.writes += 1
        return await self.real.write(kind, project, fields, task)


async def main():
    FOLDER.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    if JOURNAL.exists():
        old = json.loads(JOURNAL.read_text("utf-8"))
        if old.get("phase") == "verified":
            print(json.dumps(old["checks"]), flush=True)
            return
        raise RuntimeError("recorded batch requires inspection; never replay a mutation")
    project = json.loads(
        await asyncio.to_thread(Path("/tmp/notido-test-project.json").read_text, "utf-8-sig")
    )
    if project["name"] != "NotiDo 验收":
        raise RuntimeError("authorized test list required")
    run = uuid.uuid4().hex
    files = sorted([PROJECT_ROOT / "main.py", *(PROJECT_ROOT / "notido").rglob("*.py")])
    version = hashlib.sha256(
        b"".join(
            str(f.relative_to(PROJECT_ROOT)).encode()
            + b"\0"
            + hashlib.sha256(f.read_bytes()).digest()
            for f in files
        )
    ).hexdigest()
    state = {
        "run": run,
        "runtime_code_sha256": version,
        "project_id": project["id"],
        "title": "NotiDo 验收 · 原生来源冲突与外部修改 · " + run[:8],
        "phase": "prepared",
        "steps": {},
        "checks": [],
    }
    real = DidaGateway(
        CLIRunner(
            "/usr/local/bin/node",
            "/opt/notido-cli/node_modules/@suibiji/dida-cli/dist/index.js",
            Path("/AstrBot/data/plugin_data/astrbot_plugin_notido/cli-home"),
        ),
        task_extension=CLIRunner(
            "/usr/local/bin/node",
            str(PROJECT_ROOT / "tools/task-extension.mjs"),
            Path("/AstrBot/data/plugin_data/astrbot_plugin_notido/cli-home"),
        ),
    )
    projects = await real.projects()
    if not any(
        p["id"] == project["id"] and p["name"] == project["name"] and not p.get("closed")
        for p in projects
    ):
        raise RuntimeError("actual account cannot verify the dedicated list")
    gateway = ExactGateway(real, state)
    context = ContextFixture()
    bridge = AstrBotBridge(context, "native-conflict-public-fixture")
    service = Service(FOLDER / ("data-" + run), bridge, gateway)
    await service.start()
    save(state)
    try:

        def incoming(label, text, published=None):
            value = event(text, run + ":" + label)
            if published:
                value.message_obj.message = [
                    Node(
                        content=[Plain(text)],
                        name="合成原通知作者",
                        time=int(datetime.fromisoformat(published).timestamp()),
                    )
                ]
            return value

        first = incoming(
            "original",
            "本人须于2027年12月20日17:00交验收报告，PDF格式。",
            "2026-10-07T10:00:00+00:00",
        )
        identity = bridge.identity(first)
        settings = Settings(
            account_ref="native-conflict-fixture-account",
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
            await execute(conn, "UPDATE settings SET payload=:p", {"p": settings.model_dump_json()})
            await execute(
                conn,
                "INSERT INTO actor_bindings VALUES ('fixture','personal',:i,:a,:s,1,0)",
                identity,
            )
        native = NativeTools(service)

        async def invoke(value, kind, args):
            return json.loads(await native.invoke(value, kind, args))

        async def material(value):
            manifest = json.loads((await native.invoke(value, "materials", {})).content[0].text)
            item = manifest["items"][0]
            return manifest, {
                "source_id": item["source_id"],
                "location": item["location"],
                "quote": item["text"],
            }

        async def step(label, callback):
            state["steps"][label] = {"phase": "request_sent"}
            save(state)
            result = await callback()
            state["steps"][label] = {
                "phase": "received",
                "result": asdict(result) if is_dataclass(result) else result,
            }
            save(state)
            return result

        manifest, evidence = await material(first)
        created = await step(
            "created",
            lambda: invoke(
                first,
                "create",
                {
                    "request_key": "report",
                    "title": state["title"],
                    "notes": "用户原有备注",
                    "date_text": "2027-12-20",
                    "time_text": "17:00",
                    "all_day": False,
                    "requirements": ["PDF格式"],
                    "notice": {"group_id": manifest["group_id"], "action_key": "report"},
                    "evidence": [evidence],
                },
            ),
        )
        assert created["state"] == "succeeded" and created["receipt_policy"]["write_verified"]
        state.update(remote_id=created["remote_id"], operation_id=created["operation_id"])
        original = created["actual_fields"]
        save(state)
        await invoke(first, "outcome", {"state": "completed"})

        async def revision(label, published, expected, day="2027-12-21"):
            value = incoming(label, f"该验收报告延期至{day} 18:00，PDF格式不变。", published)
            source, proof = await material(value)
            selected = (await invoke(value, "query", {"keyword": state["title"]}))["tasks"]
            assert len(selected) == 1 and selected[0]["id"] == state["remote_id"]
            before = gateway.writes
            before_rows = len(await service.db.read("SELECT id FROM operations"))
            result = await step(
                label,
                lambda: invoke(
                    value,
                    "update",
                    {
                        "request_key": label,
                        "selection_ref": selected[0]["selection_ref"],
                        "patch": {"date_text": day, "time_text": "18:00", "all_day": False},
                        "notice": {"group_id": source["group_id"], "evidence": [proof]},
                    },
                ),
            )
            if expected:
                assert result["error"] == expected
                assert (
                    gateway.writes == before
                    and len(await service.db.read("SELECT id FROM operations")) == before_rows
                )
                state["checks"].append({"check": label + "_zero_write", "pass": True})
                await invoke(
                    value,
                    "outcome",
                    {
                        "state": "awaiting_clarification",
                        "pending_reason": "来源或外部修改需明确核对",
                    },
                )
            return value, selected[0], source, proof, result

        await revision("older_source", "2026-10-06T10:00:00+00:00", "SOURCE_ORDER_CONFLICT")
        await revision("unknown_source_order", None, "SOURCE_ORDER_UNKNOWN")

        external_fields = {
            "dueDate": "2027-12-25T15:00:00+0800",
            "startDate": "2027-12-25T15:00:00+0800",
            "isAllDay": False,
            "content": original["content"] + "\n用户另加备注保留",
        }
        external = await step(
            "external_edit",
            lambda: gateway.write("update", project["id"], external_fields, state["remote_id"]),
        )
        state["steps"]["external_edit"]["result"] = {"side_effect": external.side_effect}
        save(state)
        current = await gateway.get(project["id"], state["remote_id"])
        assert service.fields_match(current, external_fields)
        await revision(
            "external_edit_guard", "2026-10-08T10:00:00+00:00", "TARGET_CHANGED", "2027-12-22"
        )
        current = await gateway.get(project["id"], state["remote_id"])
        assert service.fields_match(current, external_fields)

        reset = await step(
            "restore_test_date",
            lambda: gateway.write(
                "update",
                project["id"],
                {
                    "dueDate": original["dueDate"],
                    "startDate": original["startDate"],
                    "isAllDay": original["isAllDay"],
                },
                state["remote_id"],
            ),
        )
        state["steps"]["restore_test_date"]["result"] = {"side_effect": reset.side_effect}
        save(state)
        value, _, _, _, updated = await revision(
            "later_source_valid", "2026-10-08T12:00:00+00:00", None, "2027-12-22"
        )
        assert updated["state"] == "succeeded"
        assert updated["actual_fields"]["id"] == state["remote_id"]
        assert (
            "用户原有备注" in updated["actual_fields"]["content"]
            and "用户另加备注保留" in updated["actual_fields"]["content"]
        )
        updated_row = (
            await service.db.read(
                "SELECT action_id,plan FROM operations WHERE id=:id",
                {"id": updated["operation_id"]},
            )
        )[0]
        created_row = (
            await service.db.read(
                "SELECT action_id FROM operations WHERE id=:id", {"id": created["operation_id"]}
            )
        )[0]
        assert updated_row["action_id"] == created_row["action_id"]
        assert json.loads(updated_row["plan"])["item_revision"] == 1
        state["checks"].append(
            {
                "check": "later_source_updates_same_task_action_and_preserves_user_notes",
                "pass": True,
            }
        )
        await invoke(value, "outcome", {"state": "completed"})

        complete_event = incoming("complete", "我已完成该合成验收报告")
        selected = (await invoke(complete_event, "query", {"keyword": state["title"]}))["tasks"][0]
        completed = await step(
            "completed",
            lambda: invoke(
                complete_event,
                "complete",
                {
                    "request_key": "completed",
                    "selection_ref": selected["selection_ref"],
                },
            ),
        )
        assert completed["state"] == "succeeded" and completed["actual_fields"]["status"] == 2
        await invoke(complete_event, "outcome", {"state": "completed"})
        after = incoming(
            "after_completion",
            "已完成报告又延期至2027年12月24日18:00。",
            "2026-10-09T10:00:00+00:00",
        )
        source, proof = await material(after)
        before = gateway.writes
        guarded = await step(
            "completed_target_guard",
            lambda: invoke(
                after,
                "update",
                {
                    "request_key": "after-completion",
                    "selection_ref": selected["selection_ref"],
                    "patch": {"date_text": "2027-12-24"},
                    "notice": {"group_id": source["group_id"], "evidence": [proof]},
                },
            ),
        )
        assert (
            guarded.get("error") in ("TASK_COMPLETED", "TARGET_CHANGED")
            and gateway.writes == before
        )
        assert (await gateway.get(project["id"], state["remote_id"]))["status"] == 2
        state["checks"].append({"check": "completed_task_not_reopened_or_recreated", "pass": True})
        assert not context.models and not context.sent
        state.update(
            phase="verified",
            gateway_write_calls=gateway.writes,
            provider_calls=0,
            scope="real_dida_and_public_astrbot_fixture_explicit_arguments",
        )
        save(state)
        print(json.dumps(state["checks"]), flush=True)
    finally:
        await service.stop()


if __name__ == "__main__":
    asyncio.run(main())
