"""Hard-kill native execution at durable boundaries using dedicated real tasks.

No Provider/persona/planner is involved. Each case starts once; a recorded
launch is never blindly repeated. Only the first child may write, and only its
unique synthetic create. Recovery permits reads and refuses every write.
"""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from notido.cli import CLIRunner, DidaGateway
from notido.db import execute
from notido.keys import uid
from notido.models import InputEnvelope, Segment, Settings
from notido.native import NativeTools
from notido.service import Service
from tools.live_native_partial_smoke import Bridge
from tools.live_restore_smoke import ReadOnlyGateway
from tools.live_same_name_smoke import ROOT, save

PRIVATE = ROOT / "runtime-data"
JOURNAL = PRIVATE / "native-process-kill-results.json"
BOUNDARIES = ("before_write", "after_write_before_id", "after_id_before_verify")


def real_gateway(root):
    node = r"D:\Program Files\nodejs\node.exe"
    return DidaGateway(
        CLIRunner(
            node, str(ROOT / "node_modules/@suibiji/dida-cli/dist/index.js"), root / "cli-home"
        ),
        task_extension=CLIRunner(node, str(ROOT / "tools/task-extension.mjs"), root / "cli-home"),
    )


def incoming(record):
    envelope = InputEnvelope(
        event_id=record["message"],
        framework_instance_id=Bridge.instance_id,
        actor_key="local-acceptance",
        session_key=record["session"],
        message_key=record["message"],
        received_at=datetime.fromisoformat(record["received_at"]),
        reply_origin_ref="local-acceptance:no-send",
        source_kind="direct_request",
        segments=[Segment(source_id=record["source"], kind="text", text=record["title"])],
    )
    return SimpleNamespace(
        envelope=envelope, message_obj=SimpleNamespace(message_id=record["message"])
    )


def arguments(record):
    return {
        "request_key": "single-create",
        "title": record["title"],
        "notes": record["unique_note"],
    }


def operation(root):
    with sqlite3.connect((root / "notido.db").as_uri() + "?mode=ro", uri=True) as database:
        database.row_factory = sqlite3.Row
        rows = database.execute("SELECT * FROM operations").fetchall()
        if len(rows) != 1:
            raise RuntimeError("exactly one native operation is required")
        return dict(rows[0])


class BoundaryGateway:
    def __init__(self, real, record, marker):
        self.real, self.record, self.marker = real, record, marker

    def __getattr__(self, name):
        return getattr(self.real, name)

    async def pause(self, **values):
        save(self.marker, {"pid": os.getpid(), "boundary": self.record["boundary"], **values})
        await asyncio.Event().wait()

    async def write(self, kind, project, fields, task=None):
        if (
            kind != "create"
            or project != self.record["project_id"]
            or fields.get("title") != self.record["title"]
            or fields.get("content") != self.record["unique_note"]
        ):
            raise RuntimeError("child may only create its exact authorized synthetic task")
        if self.record["boundary"] == "before_write":
            await self.pause()
        result = await self.real.write(kind, project, fields, task)
        if result.error or not isinstance(result.value, dict) or not result.value.get("id"):
            raise RuntimeError("real CLI creation did not return a reliable success")
        if self.record["boundary"] == "after_write_before_id":
            await self.pause(remote_id=result.value["id"])
        return result

    async def get(self, project, task):
        if self.record["boundary"] == "after_id_before_verify":
            await self.pause(remote_id=task)
        return await self.real.get(project, task)


async def child(case_id):
    state = json.loads(JOURNAL.read_text(encoding="utf-8"))
    record = state["cases"][case_id]
    root = Path(record["root"])
    marker = Path(record["marker"])
    service = Service(root, Bridge(), BoundaryGateway(real_gateway(root), record, marker))
    service.owner_lock.acquire(timeout=0)
    await service.db.initialize()
    settings = Settings(
        account_ref="dedicated-cn-native-hard-kill",
        credential_generation=1,
        default_project=record["project_id"],
        allowed_projects=[record["project_id"]],
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
            "INSERT INTO actor_bindings VALUES (:id,'personal',:i,'local-acceptance',:s,1,0)",
            {"id": uid(), "i": Bridge.instance_id, "s": record["session"]},
        )
    await NativeTools(service).invoke(incoming(record), "create", arguments(record))
    raise RuntimeError("child failed to stop at its requested boundary")


async def recover(record):
    root = Path(record["root"])
    gateway = ReadOnlyGateway(real_gateway(root))
    service = Service(root, Bridge(), gateway)
    await service.start()
    try:
        native = NativeTools(service)
        first = operation(root)
        await service.reconcile({"operation_id": first["id"]})
        repeated = json.loads(await native.invoke(incoming(record), "create", arguments(record)))
        after = operation(root)
        marker = json.loads(
            await asyncio.to_thread(Path(record["marker"]).read_text, encoding="utf-8")
        )
        tasks = await gateway.tasks(record["project_id"])
        matched = [task for task in tasks if task.get("title") == record["title"]]
        expected = 0 if record["boundary"] == "before_write" else 1
        if len(matched) != expected or gateway.writes or after["attempt"] != 1:
            raise RuntimeError("crash recovery changed remote count or replayed a write")
        wanted = (
            "succeeded" if record["boundary"] == "after_id_before_verify" else "outcome_unknown"
        )
        if after["state"] != wanted or repeated["state"] != wanted:
            raise RuntimeError("crash recovery claimed an unproven outcome")
        if record["boundary"] != "after_id_before_verify" and after["remote_id"] is not None:
            raise RuntimeError("test-only remote observation was promoted into ledger proof")
        if matched:
            actual = await gateway.get(record["project_id"], marker["remote_id"])
            if (
                actual.get("content") != record["unique_note"]
                or actual.get("title") != record["title"]
            ):
                raise RuntimeError("real synthetic task fields changed")
        if await service.db.read("SELECT * FROM receipt_records"):
            raise RuntimeError("native recovery created an independent chat receipt")
        return {
            "pass": True,
            "state": after["state"],
            "attempt": after["attempt"],
            "matched_remote_tasks": len(matched),
            "recovery_writes": gateway.writes,
            "original_operation_id": after["id"],
            "remote_id_retained": bool(after["remote_id"]),
        }
    finally:
        await service.stop()


def main():
    project = json.loads((PRIVATE / "test-project.json").read_text(encoding="utf-8-sig"))
    if project["name"] != "NotiDo 验收":
        raise RuntimeError("authorized dedicated project required")
    files = sorted([ROOT / "main.py", *(ROOT / "notido").rglob("*.py")])
    version = hashlib.sha256(
        b"".join(
            str(path.relative_to(ROOT)).encode()
            + b"\0"
            + hashlib.sha256(path.read_bytes()).digest()
            for path in files
        )
    ).hexdigest()
    state = (
        json.loads(JOURNAL.read_text(encoding="utf-8"))
        if JOURNAL.exists()
        else {
            "implementation_sha256": version,
            "cases": {},
            "process": "Windows TerminateProcess after real durable boundary",
            "scope": "native facade plus real Dida; public bridge fixture; no Provider",
        }
    )
    if state["implementation_sha256"] != version:
        raise RuntimeError("do not mix source versions in the process-kill batch")
    for boundary in BOUNDARIES:
        record = state["cases"].get(boundary)
        if record and record.get("phase") == "verified":
            continue
        if record is None:
            run = uid()
            root = PRIVATE / f"native-hard-kill-{run}"
            root.mkdir()
            for relative in (".config/dida-cli/config.json",):
                target = root / "cli-home" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(PRIVATE / "cli-home" / relative, target)
                os.chmod(target, 0o600)
            record = {
                "boundary": boundary,
                "phase": "prepared",
                "root": str(root),
                "marker": str(root / "boundary.json"),
                "session": uid(),
                "message": uid(),
                "source": uid(),
                "received_at": datetime.now(UTC).isoformat(),
                "project_id": project["id"],
                "title": f"NotiDo 验收 · 原生进程中断 · {boundary} · {run[:8]}",
                "unique_note": f"NotiDo synthetic crash provenance {run}; no real notification.",
            }
            state["cases"][boundary] = record
            save(JOURNAL, state)
        if record["phase"] != "prepared":
            raise RuntimeError(
                "a recorded child was already launched; inspect its process/marker, do not relaunch"
            )
        record["phase"] = "launch_recorded"
        save(JOURNAL, state)
        log = Path(record["root"]) / "child-output.log"
        with log.open("wb") as output:
            # Windows venv python.exe is a launcher; terminating its PID need
            # not identify the Python execution process. Use the real same-
            # version interpreter with this venv's dependency path explicitly.
            child_env = os.environ.copy()
            child_env["PYTHONPATH"] = str(Path(sys.prefix) / "Lib" / "site-packages")
            process = subprocess.Popen(
                [
                    sys._base_executable,
                    "-X",
                    "utf8",
                    "-m",
                    "tools.native_process_kill_smoke",
                    "--child",
                    boundary,
                ],
                cwd=ROOT,
                env=child_env,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            try:
                record["child_pid"] = process.pid
                save(JOURNAL, state)
                deadline = time.monotonic() + 90
                while not Path(record["marker"]).exists():
                    if process.poll() is not None:
                        raise RuntimeError("child exited before boundary; private log retained")
                    if time.monotonic() >= deadline:
                        raise RuntimeError("boundary not reached; do not repeat the write")
                    time.sleep(0.1)
                observed = json.loads(Path(record["marker"]).read_text(encoding="utf-8"))
                if observed["pid"] != process.pid or observed["boundary"] != boundary:
                    raise RuntimeError("unexpected boundary process")
                before = operation(Path(record["root"]))
                expected = (
                    "created_unverified" if boundary == "after_id_before_verify" else "executing"
                )
                if before["state"] != expected or before["attempt"] != 1:
                    raise RuntimeError("requested durable boundary was not actually reached")
                record["before_kill"] = before
                process.kill()
                record["exit_code"] = process.wait(timeout=15)
                record["phase"] = "killed"
                save(JOURNAL, state)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=15)
        record["checks"] = asyncio.run(recover(record))
        record["phase"] = "verified"
        save(JOURNAL, state)
        print(json.dumps({"check": boundary, **record["checks"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", choices=BOUNDARIES)
    args = parser.parse_args()
    if args.child:
        asyncio.run(child(args.child))
    else:
        main()
