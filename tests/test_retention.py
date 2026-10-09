import io
import json
import os
import time

import pytest
from conftest import create_plan, envelope, operation, parsed

from notido.db import Database, execute
from notido.keys import key, uid
from notido.materials import AsyncFile
from notido.retention import DAY
from tools.backup import backup, restore


async def completed_group(service, *, task_complete=False):
    service.bridge.plan = create_plan
    group = await parsed(service)
    op = await operation(service)
    await service.execute_operation({"operation_id": op["id"]})
    if task_complete:
        service.gateway.items[("p1", (await operation(service))["remote_id"])]["status"] = 2
    now = time.time()
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE jobs SET state='done'")
        await execute(conn, "UPDATE receipt_records SET state='sent'")
        await execute(conn, "UPDATE material_groups SET last_at=:t", {"t": now - 40 * DAY})
    return group, now


async def asset(service, group, content=b"original", name="original.txt"):
    blob = await service.blobs.save(AsyncFile(io.BytesIO(content)), min_free_bytes=0)
    asset_id = uid()
    async with service.db.transaction() as conn:
        await service.register_blob(conn, blob)
        await execute(
            conn,
            "INSERT INTO assets VALUES (:id,'personal',:g,NULL,:h,:n,:s,'ready',NULL,:t,0)",
            {
                "id": asset_id,
                "g": group,
                "h": blob["hash"],
                "n": name,
                "s": uid(),
                "t": time.time(),
            },
        )
    return blob, asset_id


async def test_processing_complete_is_not_remote_task_complete(service):
    _, now = await completed_group(service)
    result = await service.retention.run(now=now)
    assert result["groups_purged"] == 0
    assert (await service.db.read("SELECT * FROM material_segments"))[0]["text"]
    assert not await service.db.read("SELECT * FROM task_completion_observations")


async def test_complete_observation_survives_restart_and_preserves_30_days(service):
    _, now = await completed_group(service, task_complete=True)
    original_plan = json.loads((await operation(service))["plan"])
    assert (await service.retention.run(now=now))["groups_purged"] == 0
    await service.db.initialize()
    assert (await service.retention.run(now=now + 29 * DAY))["groups_purged"] == 0
    assert (await service.retention.run(now=now + 31 * DAY))["groups_purged"] == 1
    current = json.loads((await operation(service))["plan"])
    assert (
        current["retention_expired"]
        and current["fields"]["title"] == original_plan["fields"]["title"]
    )
    assert current["payload_hash"] == key(original_plan)
    assert (await service.db.read("SELECT * FROM material_segments"))[0]["text"] == ""
    first_version = (await service.db.read("SELECT * FROM operation_plan_versions ORDER BY id"))[0]
    assert first_version["payload_hash"] == key(original_plan) and first_version["expired_at"]
    assert "source_evidence" not in first_version["payload"]
    assert (await service.retention.run(now=now + 60 * DAY))["groups_purged"] == 1
    assert json.loads((await operation(service))["plan"])["fields"] == {}
    assert (await operation(service))["operation_key"]


async def test_reopened_task_resets_completion_countdown(service):
    _, now = await completed_group(service, task_complete=True)
    await service.retention.run(now=now)
    remote = service.gateway.items[("p1", (await operation(service))["remote_id"])]
    remote["status"] = 0
    assert (await service.retention.run(now=now + 31 * DAY))["groups_purged"] == 0
    assert not await service.db.read("SELECT * FROM task_completion_observations")
    remote["status"] = 2
    assert (await service.retention.run(now=now + 33 * DAY))["groups_purged"] == 0


@pytest.mark.parametrize("protection", ["unknown", "draft", "receipt", "read_failed", "account"])
async def test_uncertain_or_active_references_do_not_expire(service, protection):
    _, now = await completed_group(service, task_complete=True)
    async with service.db.transaction() as conn:
        if protection == "unknown":
            await execute(conn, "UPDATE operations SET state='outcome_unknown'")
        elif protection == "draft":
            await execute(conn, "UPDATE material_groups SET state='awaiting_clarification'")
        elif protection == "receipt":
            await execute(conn, "UPDATE receipt_records SET state='unknown'")
        elif protection == "account":
            settings, _ = await service.db.settings()
            settings.account_ref = "another-account"
            await execute(conn, "UPDATE settings SET payload=:p", {"p": settings.model_dump_json()})
    service.gateway.read_error = protection == "read_failed"
    assert (await service.retention.run(now=now + 200 * DAY))["groups_purged"] == 0
    assert (await service.db.read("SELECT text FROM material_segments"))[0]["text"]


async def test_shared_hash_and_backup_tombstones(service, tmp_path):
    service.bridge.plan = {
        "schema_version": 4,
        "intent": "none",
        "ambiguities": [],
        "reason": "仅知晓",
        "safe_summary": "仅知晓",
    }
    group = await parsed(service)
    blob, _ = await asset(service, group["group_id"])
    other = await service.intake(envelope(message="active-group", text="材料待处理"))
    await asset(service, other["group_id"])
    now = time.time()
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE jobs SET state='done'")
        await execute(conn, "UPDATE receipt_records SET state='sent'")
        await execute(
            conn,
            "UPDATE material_groups SET last_at=:t WHERE id=:g",
            {"g": group["group_id"], "t": now - 40 * DAY},
        )
    assert (await service.retention.run(now=now))["groups_purged"] == 1
    assert service.blobs.path(blob["path"]).is_file()
    assert not await service.db.read("SELECT * FROM blob_tombstones")
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "UPDATE material_groups SET state='cancelled',last_at=:t WHERE id=:g",
            {"g": other["group_id"], "t": now - 40 * DAY},
        )
    await service.retention.run(now=now + 2 * DAY)
    assert not (service.root / blob["path"]).exists()
    assert (await service.db.read("SELECT * FROM blob_tombstones"))[0]["hash"] == blob["hash"]
    await service.check_blobs()
    snapshot = tmp_path.parent / (tmp_path.name + "-backup")
    backup(service.root, snapshot)
    target = tmp_path.parent / (tmp_path.name + "-restore")
    restore(snapshot, target)
    db = Database(target)
    await db.initialize()
    assert await db.read("SELECT * FROM blob_tombstones")
    await db.close()
    # Re-supplying the same bytes creates a new live occurrence and heals the tombstone.
    await asset(service, other["group_id"])
    assert not await service.db.read("SELECT * FROM blob_tombstones")
    await service.check_blobs()


async def test_orphan_gc_preserves_active_indexed_and_unmanaged_files(service):
    now = time.time()
    stale = now - 40 * DAY
    staging = service.root / "staging"
    old_staging, active_staging = staging / uid(), staging / uid()
    derived = service.root / "derived"
    old_derived, active_derived = derived / uid(), derived / uid()
    for entry in (old_staging, active_staging):
        entry.write_bytes(b"staging")
        os.utime(entry, (stale, stale))
    for entry in (old_derived, active_derived):
        entry.mkdir()
        (entry / "decoded.png").write_bytes(b"derived")
        os.utime(entry, (stale, stale))
    service.blobs.active_staging.add(active_staging.resolve())
    service.blobs.active_derived.add(active_derived.resolve())
    orphan = service.root / "blobs" / ("f" * 64)
    unmanaged = service.root / "blobs" / "user-kept.txt"
    for entry in (orphan, unmanaged):
        entry.write_bytes(b"unindexed")
        os.utime(entry, (stale, stale))
    group = await service.intake(envelope())
    blob, _ = await asset(service, group["group_id"])
    os.utime(service.root / blob["path"], (stale, stale))
    await service.retention.run(now=now)
    assert not old_staging.exists() and not old_derived.exists() and not orphan.exists()
    assert active_staging.exists() and active_derived.exists() and unmanaged.exists()
    assert (service.root / blob["path"]).exists()
