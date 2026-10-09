import asyncio
import json

import pytest
from conftest import create_plan, operation, parsed
from test_queue_dependencies import add_asset

from notido.db import execute
from notido.errors import NotiDoError


async def unknown_upload(service):
    service.bridge.plan = create_plan
    group = await parsed(service)
    first = await operation(service)
    await service.execute_operation({"operation_id": first["id"]})
    parent = json.loads(first["plan"])
    remote = (await operation(service))["remote_id"]
    asset = await add_asset(service, group["group_id"], b"upload recovery original")
    async with service.db.transaction() as conn:
        await service.schedule_upload(conn, parent, remote, asset)
        await execute(
            conn,
            "UPDATE operations SET state='outcome_unknown',attempt=1,result=:r WHERE kind='upload'",
            {"r": json.dumps({"side_effect": "unknown", "remote_id": None})},
        )
    return (await service.db.read("SELECT * FROM operations WHERE kind='upload'"))[0]


async def test_preallocated_id_is_only_promoted_after_target_and_hash_verification(service):
    upload = await unknown_upload(service)
    plan = json.loads(upload["plan"])

    async def inspect(candidate, remote_id):
        assert remote_id == plan["attachment_id"]
        return {"sha256": plan["hash"], "task_id": plan["task_id"]}

    service.gateway.inspect_upload = inspect
    await service.reconcile({"operation_id": upload["id"]})
    actual = (await service.db.read("SELECT * FROM operations WHERE kind='upload'"))[0]
    assert actual["state"] == "succeeded" and actual["remote_id"] == plan["attachment_id"]
    assert (await service.db.read("SELECT verified FROM task_attachment_links"))[0]["verified"] == 1
    assert len(service.gateway.writes) == 1  # Only the original task creation; no upload replay.
    count = len(await service.db.read("SELECT * FROM receipt_records"))
    await service.reconcile({"operation_id": upload["id"]})
    assert len(await service.db.read("SELECT * FROM receipt_records")) == count


@pytest.mark.parametrize("failure", ["hash", "target", "not_found"])
async def test_candidate_is_not_evidence_without_matching_original_bytes(service, failure):
    upload = await unknown_upload(service)
    plan = json.loads(upload["plan"])

    async def inspect(candidate, remote_id):
        if failure == "not_found":
            raise NotiDoError("READ_FAILED", "未找到可靠附件")
        return {
            "sha256": "wrong" if failure == "hash" else plan["hash"],
            "task_id": "other" if failure == "target" else plan["task_id"],
        }

    service.gateway.inspect_upload = inspect
    await service.reconcile({"operation_id": upload["id"]})
    actual = (await service.db.read("SELECT * FROM operations WHERE kind='upload'"))[0]
    assert actual["state"] == "outcome_unknown" and actual["remote_id"] is None
    assert not (await service.db.read("SELECT verified FROM task_attachment_links"))[0]["verified"]
    assert len(service.gateway.writes) == 1


async def test_reconcile_and_credential_switch_share_lock(service):
    upload = await unknown_upload(service)
    plan = json.loads(upload["plan"])
    started, release, switched = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def inspect(candidate, remote_id):
        started.set()
        await release.wait()
        assert not switched.is_set()
        return {"sha256": plan["hash"], "task_id": plan["task_id"]}

    async def change_credentials():
        async with service.write_lock:
            switched.set()

    service.gateway.inspect_upload = inspect
    check = asyncio.create_task(service.reconcile({"operation_id": upload["id"]}))
    await asyncio.wait_for(started.wait(), 2)
    switch = asyncio.create_task(change_credentials())
    await asyncio.sleep(0)
    assert not switched.is_set()
    release.set()
    await asyncio.wait_for(asyncio.gather(check, switch), 2)
    assert switched.is_set()


async def test_registered_id_with_different_bytes_is_retained_as_mismatch(service):
    upload = await unknown_upload(service)
    plan = json.loads(upload["plan"])

    async def inspect(candidate, remote_id):
        return {
            "id": plan["attachment_id"],
            "project_id": plan["project_id"],
            "task_id": plan["task_id"],
            "sha256": "wrong",
        }

    service.gateway.inspect_upload = inspect
    await service.reconcile({"operation_id": upload["id"]})
    actual = (await service.db.read("SELECT * FROM operations WHERE kind='upload'"))[0]
    assert actual["state"] == "uploaded_unverified" and actual["remote_id"] == plan["attachment_id"]
    assert json.loads(actual["result"])["error"]["code"] == "RESULT_MISMATCH"
    assert len(service.gateway.writes) == 1
