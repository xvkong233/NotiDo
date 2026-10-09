"""Native facade date transitions on one new, exact dedicated real task.

No Provider, independent receipt, deletion or historical task mutation. A
recorded in-flight step is inspected rather than blindly sent again.
"""

import argparse
import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace

from notido.db import execute
from notido.keys import uid
from notido.models import InputEnvelope, Segment, Settings
from notido.native import NativeTools
from notido.query import task_date
from notido.service import Service
from tools.live_native_partial_smoke import Bridge
from tools.live_same_name_smoke import ROOT, save
from tools.native_process_kill_smoke import real_gateway

PRIVATE = ROOT / "runtime-data"
JOURNAL = PRIVATE / "native-date-transition-results.json"


class ExactGateway:
    def __init__(self, real, state):
        self.real, self.state = real, state
        self.writes = 0

    def __getattr__(self, name):
        return getattr(self.real, name)

    async def write(self, kind, project, fields, task=None):
        if project != self.state["project_id"]:
            raise RuntimeError("only the authorized dedicated list is writable")
        if kind == "create":
            if task or fields.get("title") != self.state["title"]:
                raise RuntimeError("only this exact synthetic create is permitted")
        elif kind == "update":
            if task != self.state.get("remote_id") or not task:
                raise RuntimeError("only this batch's saved task is writable")
        else:
            raise RuntimeError("no completion or deletion in the date probe")
        self.writes += 1
        return await self.real.write(kind, project, fields, task)


def incoming(state, step):
    message = state["run"] + ":" + step
    return SimpleNamespace(
        message_obj=SimpleNamespace(message_id=message),
        envelope=InputEnvelope(
            event_id=message,
            framework_instance_id=Bridge.instance_id,
            actor_key="local-acceptance",
            session_key=state["session"],
            message_key=message,
            received_at=datetime.fromisoformat(state["received_at"]),
            reply_origin_ref="local-acceptance:no-send",
            source_kind="direct_request",
            segments=[Segment(source_id=message, kind="text", text="专用日期字段验收")],
        ),
    )


async def main(review_recorded=False):
    project = json.loads((PRIVATE / "test-project.json").read_text(encoding="utf-8-sig"))
    if project["name"] != "NotiDo 验收":
        raise RuntimeError("authorized dedicated list required")
    files = sorted([ROOT / "main.py", *(ROOT / "notido").rglob("*.py")])
    version = hashlib.sha256(
        b"".join(
            str(p.relative_to(ROOT)).encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest()
            for p in files
        )
    ).hexdigest()
    if JOURNAL.exists():
        state = json.loads(JOURNAL.read_text(encoding="utf-8"))
    else:
        run = uid()
        state = {
            "implementation_sha256": version,
            "run": run,
            "session": uid(),
            "received_at": datetime.now(UTC).isoformat(),
            "project_id": project["id"],
            "title": f"NotiDo 验收 · 原生日期转换 · {run[:8]}",
            "note": f"Synthetic date transition provenance {run}",
            "cases": {},
            "phase": "prepared",
        }
        save(JOURNAL, state)
    if state["implementation_sha256"] != version or state["project_id"] != project["id"]:
        raise RuntimeError("source/list changed; do not mix evidence or replay writes")
    if state["phase"] == "verified":
        print(json.dumps({"already_verified": True, "cases": len(state["cases"])}))
        return
    gateway = ExactGateway(real_gateway(PRIVATE), state)
    service = Service(PRIVATE / ("native-date-transition-" + state["run"]), Bridge(), gateway)
    service.owner_lock.acquire(timeout=0)
    try:
        await service.db.initialize()
        settings, _ = await service.db.settings()
        if not settings.account_ref:
            settings = Settings(
                account_ref="dedicated-cn-native-date-transition",
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
        native = NativeTools(service)
        steps = [
            ("create_all_day", None, None, "2027-12-24", True),
            ("reject_missing_time", {"all_day": False}, "DATE_UNRESOLVED", "2027-12-24", True),
            (
                "reject_contradiction",
                {"all_day": True, "time_text": "17:40"},
                "DATE_CONTRADICTION",
                "2027-12-24",
                True,
            ),
            (
                "set_specific_time",
                {"time_text": "17:40", "all_day": False},
                None,
                "2027-12-24T17:40",
                False,
            ),
            (
                "change_date_keep_time",
                {"date_text": "2027-12-25", "all_day": False},
                None,
                "2027-12-25T17:40",
                False,
            ),
            ("convert_all_day", {"all_day": True}, None, "2027-12-25", True),
            (
                "reject_clear_plus_time",
                {"date_text": None, "time_text": "17:40"},
                "DATE_CONTRADICTION",
                "2027-12-25",
                True,
            ),
            ("clear_date", {"date_text": None}, None, None, None),
        ]
        for name, patch, error, expected_date, expected_all_day in steps:
            prior = state["cases"].get(name)
            if prior and prior["phase"] == "verified":
                continue
            if prior and not review_recorded:
                raise RuntimeError(
                    "recorded in-flight step requires inspection; never blindly replay"
                )
            event = incoming(state, name)
            before = gateway.writes
            before_rows = len(await service.db.read("SELECT id FROM operations"))
            if prior:
                result = prior.get("result", {})
                rows = await service.db.read(
                    "SELECT * FROM operations WHERE id=:id",
                    {"id": result.get("operation_id")},
                )
                if (
                    error
                    or len(rows) != 1
                    or result.get("state") != "succeeded"
                    or rows[0]["state"] != "succeeded"
                    or rows[0]["attempt"] != 1
                    or rows[0]["remote_id"] != state.get("remote_id")
                ):
                    raise RuntimeError(
                        "recorded result lacks durable exact success; inspect without replay"
                    )
                record = prior
                args = record["arguments"]
                operation = "create" if patch is None else "update"
                record["review_only"] = True
                record["probe_diagnostic"] = (
                    "Initial check compared UTC raw timestamp to a local date; reviewed actual instant in Asia/Shanghai without replay."
                )
            elif patch is None:
                operation = "create"
                args = {
                    "request_key": name,
                    "title": state["title"],
                    "notes": state["note"],
                    "priority": 3,
                    "date_text": "2027-12-24",
                    "all_day": True,
                }
            else:
                query = json.loads(await native.invoke(event, "query", {"keyword": state["title"]}))
                if len(query["tasks"]) != 1 or query["tasks"][0]["id"] != state["remote_id"]:
                    raise RuntimeError("query must select only this saved exact task")
                operation = "update"
                args = {
                    "request_key": name,
                    "selection_ref": query["tasks"][0]["selection_ref"],
                    "patch": patch,
                }
            if not prior:
                record = {"phase": "sending", "arguments": args}
                state["cases"][name] = record
                save(JOURNAL, state)
                result = json.loads(await native.invoke(event, operation, args))
                record["result"] = result
                save(JOURNAL, state)
            if error:
                if result.get("error") != error or gateway.writes != before:
                    raise RuntimeError(
                        "contradictory or incomplete patch was not blocked before writing"
                    )
                if len(await service.db.read("SELECT id FROM operations")) != before_rows:
                    raise RuntimeError("rejected input left a scheduled operation")
            else:
                if result.get("state") != "succeeded" or gateway.writes != before + (
                    0 if prior else 1
                ):
                    raise RuntimeError("native mutation did not verify with one write")
                if patch is None:
                    state["remote_id"] = result["remote_id"]
                    save(JOURNAL, state)
                elif result["remote_id"] != state["remote_id"]:
                    raise RuntimeError("update created or targeted another task")
            actual = await gateway.get(project["id"], state["remote_id"])
            if (actual.get("title"), actual.get("content"), actual.get("priority")) != (
                state["title"],
                state["note"],
                3,
            ):
                raise RuntimeError("unrelated task fields changed")
            due = task_date(actual, "Asia/Shanghai")
            if expected_date is None:
                if due is not None:
                    raise RuntimeError("native date was not cleared")
            elif (
                due is None
                or not due.isoformat().startswith(expected_date)
                or actual.get("isAllDay", False) is not expected_all_day
            ):
                raise RuntimeError("actual native date precision differs from the requested value")
            record.update(
                phase="verified",
                actual=actual,
                write_count=1 if prior else gateway.writes - before,
                pass_check=True,
            )
            save(JOURNAL, state)
            print(
                json.dumps({"case": name, "pass": True, "writes": gateway.writes - before}),
                flush=True,
            )
        rows = await service.db.read("SELECT state,attempt,remote_id FROM operations")
        matches = [
            task
            for task in await gateway.tasks(project["id"])
            if task.get("title") == state["title"]
        ]
        if (
            len(rows) != 5
            or len(matches) != 1
            or not all(
                r["state"] == "succeeded"
                and r["attempt"] == 1
                and r["remote_id"] == state["remote_id"]
                for r in rows
            )
        ):
            raise RuntimeError("expected five single-attempt mutations on exactly one remote task")
        if await service.db.read("SELECT * FROM receipt_records"):
            raise RuntimeError("native tools emitted an independent receipt")
        state.update(
            phase="verified",
            summary={
                "cases": 8,
                "native_operations": 5,
                "remote_tasks": 1,
                "blocked_zero_write_cases": 3,
                "independent_receipts": 0,
            },
        )
        save(JOURNAL, state)
        print(json.dumps(state["summary"]), flush=True)
    finally:
        await service.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-recorded", action="store_true")
    asyncio.run(main(parser.parse_args().review_recorded))
