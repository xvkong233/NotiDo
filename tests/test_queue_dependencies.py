import hashlib
import json
import time

import pytest
from conftest import create_plan, operation, parsed

from notido.db import execute
from notido.errors import NotiDoError
from notido.keys import uid


async def add_asset(service, group, data, name="same.txt"):
    digest = hashlib.sha256(data).hexdigest()
    (service.root / "blobs" / digest).write_bytes(data)
    asset = uid()
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "INSERT OR IGNORE INTO blobs VALUES (:h,:s,:p,0)",
            {"h": digest, "s": len(data), "p": f"blobs/{digest}"},
        )
        await execute(
            conn,
            "INSERT INTO assets VALUES (:id,'personal',:g,NULL,:h,:n,:src,'ready',NULL,0,0)",
            {"id": asset, "g": group, "h": digest, "n": name, "src": uid()},
        )
    return asset


async def fill_queue(service, limit):
    async with service.db.transaction() as conn:
        settings, _ = await service.db.settings()
        settings.max_jobs = limit
        await execute(conn, "UPDATE settings SET payload=:p", {"p": settings.model_dump_json()})
        current = len(
            await service.db.read("SELECT * FROM jobs WHERE state IN ('pending','running')")
        )
        for index in range(limit - current):
            await execute(
                conn,
                "INSERT INTO jobs (id,kind,dedupe_key,payload,state,available_at,created_at) VALUES (:id,'close_group',:k,'{}','pending',0,0)",
                {"id": uid(), "k": f"capacity-{index}"},
            )


async def test_full_queue_keeps_verified_result_and_durable_dependencies(service):
    service.bridge.plan = create_plan
    group = await parsed(service)
    first = await operation(service)
    asset = await add_asset(service, group["group_id"], b"original")
    plan = json.loads(first["plan"])
    plan["attachment_asset_ids"] = [asset]
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "UPDATE operations SET plan=:p WHERE id=:id",
            {"id": first["id"], "p": json.dumps(plan)},
        )
    await fill_queue(service, 10)
    await service.execute_operation({"operation_id": first["id"]})
    assert (await operation(service))["state"] == "succeeded"
    assert service.maintenance is None and len(service.gateway.writes) == 1
    upload = (await service.db.read("SELECT * FROM operations WHERE kind='upload'"))[0]
    assert upload["state"] == "validated"
    assert (
        len(await service.db.read("SELECT * FROM jobs WHERE state IN ('pending','running')")) == 10
    )
    assert (await service.db.read("SELECT * FROM receipt_records"))[0]["state"] == "pending"
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE jobs SET state='done'")
    await service.dispatch_pending()
    await service.dispatch_pending()
    active = await service.db.read("SELECT kind FROM jobs WHERE state='pending'")
    assert sorted(x["kind"] for x in active) == ["execute_operation", "send_receipt"]
    assert len(service.gateway.writes) == 1


async def test_job_dedup_works_even_when_capacity_is_full(service):
    async with service.db.transaction() as conn:
        await service.db.job(conn, "close_group", "already", {})
    await fill_queue(service, 10)
    async with service.db.transaction() as conn:
        assert await service.db.job(conn, "close_group", "already", {})
    assert len(await service.db.read("SELECT * FROM jobs WHERE state='pending'")) == 10


async def test_processing_budget_monotonic_expiry_pauses_without_remote_write(service):
    service.bridge.plan = create_plan
    group = await parsed(service)
    op = await operation(service)
    budget = (await service.db.read("SELECT * FROM processing_budgets"))[0]
    assert budget["seconds"] == 60
    service.processing_deadlines[group["group_id"]] = time.monotonic() - 1
    with pytest.raises(NotiDoError) as error:
        await service.remaining_budget(group["group_id"])
    assert error.value.code == "PROCESSING_BUDGET_EXPIRED"
    await service.execute_operation({"operation_id": op["id"]})
    assert not service.gateway.writes and (await operation(service))["paused"] == 1
    # Clearing the in-memory cache simulates a restart; the DB budget is not reset.
    service.processing_deadlines.clear()
    await service.processing_budget(group["group_id"])
    assert (await service.db.read("SELECT * FROM processing_budgets"))[0] == budget


async def test_same_name_different_hash_is_distinguishable_and_original_name_retained(service):
    service.bridge.plan = create_plan
    group = await parsed(service)
    first = await operation(service)
    await service.execute_operation({"operation_id": first["id"]})
    parent = json.loads(first["plan"])
    target = (await operation(service))["remote_id"]
    original = await add_asset(service, group["group_id"], b"first bytes")
    changed = await add_asset(service, group["group_id"], b"second bytes")
    async with service.db.transaction() as conn:
        await service.schedule_upload(conn, parent, target, original)
        await service.schedule_upload(conn, parent, target, changed)
    uploads = await service.db.read(
        "SELECT plan FROM operations WHERE kind='upload' ORDER BY created_at"
    )
    names = [json.loads(x["plan"])["name"] for x in uploads]
    assert names[0] == "same.txt" and names[1].startswith("same-") and names[1].endswith(".txt")
    assert all(x["name"] == "same.txt" for x in await service.db.read("SELECT name FROM assets"))
