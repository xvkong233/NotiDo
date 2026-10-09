import hashlib
import json
import sqlite3

import pytest
from conftest import create_plan, operation, parsed
from filelock import FileLock, Timeout

from notido.db import execute
from tools.backup import backup, restore


async def fixture_backup(service, tmp_path):
    payload = b"original bytes\x00\xff"
    value = hashlib.sha256(payload).hexdigest()
    path = service.root / "blobs" / value
    path.write_bytes(payload)
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "INSERT INTO blobs VALUES (:h,:s,:p,0)",
            {"h": value, "s": len(payload), "p": f"blobs/{value}"},
        )
    service.bridge.plan = create_plan
    await parsed(service)
    snapshot = tmp_path.parent / (tmp_path.name + "-backup")
    backup(service.root, snapshot)
    return snapshot, value


async def test_consistent_backup_restores_private_blobs_and_pauses_writes(service, tmp_path):
    snapshot, value = await fixture_backup(service, tmp_path)
    target = tmp_path.parent / (tmp_path.name + "-restore")
    assert restore(snapshot, target)["remote_review_required"]
    assert (target / "blobs" / value).read_bytes() == b"original bytes\x00\xff"
    assert (target / "restore-review.required").is_file()
    with sqlite3.connect(target / "notido.db") as conn:
        assert conn.execute("SELECT state,paused FROM operations").fetchone() == ("validated", 1)
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()


async def test_backup_refuses_active_owner(service, tmp_path):
    with FileLock(str(service.root / "worker.lock")):
        with pytest.raises(Timeout):
            backup(service.root, tmp_path.parent / (tmp_path.name + "-locked"))


@pytest.mark.parametrize("attack", ["corrupt", "undeclared", "missing_blob", "path", "duplicate"])
async def test_restore_rejects_before_creating_target(service, tmp_path, attack):
    snapshot, value = await fixture_backup(service, tmp_path)
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if attack == "corrupt":
        (snapshot / "blobs" / value).write_bytes(b"corrupt")
    elif attack == "undeclared":
        (snapshot / "injected.py").write_text("unexpected")
    elif attack == "missing_blob":
        manifest["files"] = [x for x in manifest["files"] if x["path"] != f"blobs/{value}"]
        (snapshot / "blobs" / value).unlink()
    elif attack == "path":
        manifest["files"][0]["path"] = "../notido.db"
    elif attack == "duplicate":
        manifest["files"].append(manifest["files"][0])
    manifest_path.write_text(json.dumps(manifest))
    target = tmp_path.parent / (tmp_path.name + "-rejected")
    with pytest.raises(ValueError):
        restore(snapshot, target)
    assert not target.exists()


async def test_restart_job_before_operation_claim_is_resumable(service):
    service.bridge.plan = create_plan
    await parsed(service)
    op = await operation(service)
    async with service.db.transaction() as conn:
        await execute(
            conn, "UPDATE jobs SET state='running',owner='old' WHERE kind='execute_operation'"
        )
    await service.db.initialize()
    job = (await service.db.read("SELECT * FROM jobs WHERE kind='execute_operation'"))[0]
    assert job["state"] == "pending" and job["owner"] is None
    assert (await operation(service))["state"] == "validated"
    await service.execute_operation({"operation_id": op["id"]})
    assert len(service.gateway.writes) == 1
