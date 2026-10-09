import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from conftest import create_plan, operation, parsed

from notido.api import PagesAPI
from notido.db import execute
from notido.errors import NotiDoError


def request(revision, request_id="request", **values):
    async def body():
        return {"request_id": request_id, "expected_revision": revision, **values}

    return SimpleNamespace(username="admin", json=body)


async def restored(service, *, execute_task=True):
    service.bridge.plan = create_plan
    await parsed(service)
    if execute_task:
        await service.execute_operation({"operation_id": (await operation(service))["id"]})
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE operations SET paused=1 WHERE state='validated'")
    marker = service.root / "restore-review.required"
    marker.write_text("restore review required", encoding="utf-8")
    service.maintenance = "RESTORE_REMOTE_REVIEW_REQUIRED"
    return PagesAPI(service)


async def confirmation(api, service, review, **overrides):
    _, revision = await service.db.settings()
    body = {
        "review_id": review["review_id"],
        "reviewed_remote_history": True,
        "keep_old_operations_paused": True,
        **overrides,
    }
    return await api.recovery_confirm(request(revision, "confirm", **body))


async def test_review_reads_remote_then_explicit_ack_preserves_old_pause(service):
    api = await restored(service, execute_task=False)
    _, revision = await service.db.settings()
    req = request(revision)
    review = await api.recovery_check(req)
    assert review["ready"] and review["checked"] == 1
    assert review == await api.recovery_check(req)
    assert service.maintenance and not service.gateway.writes
    with pytest.raises(NotiDoError) as caught:
        await confirmation(api, service, review, reviewed_remote_history=False)
    assert caught.value.code == "RESTORE_ACK_REQUIRED"
    result = await confirmation(api, service, review)
    assert result["confirmed"] and service.maintenance is None
    assert not (service.root / "restore-review.required").exists()
    assert (await operation(service))["paused"] == 1
    await service.execute_operation({"operation_id": (await operation(service))["id"]})
    assert not service.gateway.writes
    assert result == await confirmation(api, service, review)


async def test_external_change_recorded_without_overwrite(service):
    api = await restored(service)
    old = await operation(service)
    service.gateway.items[("p1", old["remote_id"])]["title"] = "远端维护者的新标题"
    service.gateway.items[("p1", old["remote_id"])]["content"] = "私密外部备注"
    _, revision = await service.db.settings()
    review = await api.recovery_check(request(revision))
    stored = (await api.recovery_status(None))["review"]
    history = json.loads(stored["payload"])["history"]
    assert history[0]["verification"] == "external_change"
    assert history[0]["differences"]["title"]["current"] == "远端维护者的新标题"
    assert "私密外部备注" not in stored["payload"]
    await confirmation(api, service, review)
    assert len(service.gateway.writes) == 1
    assert service.gateway.items[("p1", old["remote_id"])]["title"] == "远端维护者的新标题"


@pytest.mark.parametrize("change", ["config", "ledger", "marker", "expired"])
async def test_stale_review_never_removes_gate(service, change):
    api = await restored(service)
    _, revision = await service.db.settings()
    review = await api.recovery_check(request(revision))
    async with service.db.transaction() as conn:
        if change == "config":
            await execute(conn, "UPDATE settings SET revision=revision+1")
        elif change == "ledger":
            await execute(conn, "UPDATE operations SET revision=revision+1")
        elif change == "expired":
            await execute(
                conn, "UPDATE restore_reviews SET checked_at=:t", {"t": time.time() - 901}
            )
    if change == "marker":
        (service.root / "restore-review.required").write_text(
            "a different restore", encoding="utf-8"
        )
    with pytest.raises(NotiDoError) as caught:
        await confirmation(api, service, review)
    assert caught.value.code == "RESTORE_REVIEW_STALE"
    assert (service.root / "restore-review.required").exists() and service.maintenance


async def test_incomplete_or_failed_read_keeps_maintenance(service):
    api = await restored(service)
    service.gateway.failed_projects.add("p2")
    _, revision = await service.db.settings()
    with pytest.raises(NotiDoError) as caught:
        await api.recovery_check(request(revision))
    assert caught.value.code == "RESTORE_REMOTE_READ_INCOMPLETE"
    assert not await service.db.read("SELECT * FROM restore_reviews")
    service.gateway.failed_projects.clear()
    service.gateway.read_error = True
    with pytest.raises(NotiDoError) as caught:
        await api.recovery_check(request(revision, "history-failed"))
    assert caught.value.code == "RESTORE_REMOTE_READ_INCOMPLETE"
    assert service.maintenance and len(service.gateway.writes) == 1


async def test_unknown_without_id_stays_unknown_and_paused_after_ack(service):
    api = await restored(service, execute_task=False)
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE operations SET state='outcome_unknown',paused=0,attempt=1")
    _, revision = await service.db.settings()
    review = await api.recovery_check(request(revision))
    history = json.loads((await api.recovery_status(None))["review"]["payload"])["history"]
    assert history[0]["verification"] == "unknown_without_reliable_id"
    await confirmation(api, service, review)
    op = await operation(service)
    assert op["state"] == "outcome_unknown" and op["paused"]
    await service.execute_operation({"operation_id": op["id"]})
    assert not service.gateway.writes


async def test_history_is_read_in_twenty_record_batches(service):
    for index in range(23):
        service.bridge.plan = create_plan
        await parsed(service, message=f"batch-{index}")
    api = await restored(service, execute_task=False)
    _, revision = await service.db.settings()
    review = await api.recovery_check(request(revision))
    assert review["checked"] == 20 and not review["ready"]
    with pytest.raises(NotiDoError) as caught:
        await confirmation(api, service, review)
    assert caught.value.code == "RESTORE_REVIEW_STALE"
    review = await api.recovery_check(
        request(revision, "next-batch", review_id=review["review_id"])
    )
    assert review["checked"] == 4 and review["ready"]
    assert len(json.loads((await api.recovery_status(None))["review"]["payload"])["history"]) == 24


async def test_durable_confirmation_recovers_marker_removal_failure(service, monkeypatch):
    api = await restored(service, execute_task=False)
    _, revision = await service.db.settings()
    review = await api.recovery_check(request(revision))
    real_clear = service.restore_review.clear_confirmed_marker

    async def fail_clear():
        raise OSError("simulated marker unlink failure")

    monkeypatch.setattr(service.restore_review, "clear_confirmed_marker", fail_clear)
    with pytest.raises(OSError):
        await confirmation(api, service, review)
    assert service.maintenance and (service.root / "restore-review.required").exists()
    assert (await service.db.read("SELECT state FROM restore_reviews"))[0]["state"] == "confirmed"
    monkeypatch.setattr(service.restore_review, "clear_confirmed_marker", real_clear)
    await service.start()
    assert service.maintenance is None and not (service.root / "restore-review.required").exists()
    await service.stop()


async def test_restore_holds_unplanned_group_until_explicit_reprocessing(service, monkeypatch):
    from conftest import envelope

    service.bridge.plan = create_plan
    completed = asyncio.Event()
    original_reply = service.bridge.reply

    async def reply(origin, body):
        result = await original_reply(origin, body)
        if body.startswith("已保存并核验"):
            completed.set()
        return result

    monkeypatch.setattr(service.bridge, "reply", reply)
    group = await service.intake(envelope())
    marker = service.root / "restore-review.required"
    marker.write_text("restore", encoding="utf-8")
    await service.start()
    assert service.maintenance == "RESTORE_REMOTE_REVIEW_REQUIRED"
    assert await service.db.read("SELECT * FROM restored_group_holds WHERE released_at IS NULL")
    await service.parse_group({"group_id": group["group_id"]})
    assert not service.bridge.calls and not service.gateway.writes
    api = PagesAPI(service)
    _, revision = await service.db.settings()
    review = await api.recovery_check(request(revision))
    await confirmation(api, service, review)
    await service.parse_group({"group_id": group["group_id"]})
    assert not service.bridge.calls
    row = (await service.db.read("SELECT * FROM material_groups"))[0]
    with pytest.raises(NotiDoError) as caught:
        await api.continue_notice(request(row["revision"], "no-confirm"), group["group_id"])
    assert caught.value.code == "RESTORE_REPROCESS_CONFIRMATION"
    await api.continue_notice(
        request(row["revision"], "explicit-reprocess", confirm_restore_reprocess=True),
        group["group_id"],
    )
    # Exercise the actual worker, including the new read/parse resumption keys.
    await asyncio.wait_for(completed.wait(), 5)
    assert len(service.bridge.calls) == 1 and len(service.gateway.writes) == 1
    assert (await operation(service))["state"] == "succeeded"
    await service.stop()
