import asyncio

import pytest
from test_native_tools import event, invoke
from test_plan_recovery import request

from notido.api import PagesAPI
from notido.db import execute
from notido.keys import uid


async def pending(native, service, message="pending-cancel", session="session", paused=True):
    incoming = event(message, session=session)
    settings, revision = await service.db.settings()
    envelope, group = await native.admit(incoming, service.bridge.identity(incoming), settings)
    plan = service.base_plan({"id": group, "session_id": session}, "origin", settings, revision)
    plan.update(
        {
            "kind": "create",
            "project_id": "p1",
            "fields": {"title": "未执行操作"},
            "action_id": uid(),
            "delivery_mode": "framework_tool",
        }
    )
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "INSERT INTO action_items VALUES (:id,'personal',NULL,:id,0)",
            {"id": plan["action_id"]},
        )
        operation_id = await service.persist_operation(conn, plan)
        if paused:
            await execute(conn, "UPDATE operations SET paused=1 WHERE id=:id", {"id": operation_id})
    return operation_id


async def test_native_cancel_reuses_page_transition_and_never_writes_remote(native, service):
    saved = await invoke(
        native,
        event("saved-before-cancel"),
        "create",
        {"request_key": "saved", "title": "保留已写任务"},
    )
    operation_id = await pending(native, service)
    incoming = event("cancel-current", text="取消未执行操作")
    listing = await invoke(native, incoming, "cancel", {"query_only": True})
    assert listing["pending"][0]["operation_id"] == operation_id
    assert len(listing["existing_results"]) == 1
    cancelled = await invoke(native, incoming, "cancel", {})
    assert cancelled["state"] == "cancelled" and not cancelled["remote_write"]
    assert cancelled["existing_results"][0]["remote_id"] == saved["remote_id"]
    repeated = await invoke(native, incoming, "cancel", {"operation_id": operation_id})
    assert repeated["revision"] == cancelled["revision"]
    await service.execute_operation({"operation_id": operation_id})
    assert (
        len(service.gateway.writes) == 1
        and service.gateway.items[("p1", saved["remote_id"])]["status"] == 0
    )
    assert not await service.db.read(
        "SELECT * FROM jobs WHERE kind='execute_operation' AND state='pending' AND json_extract(payload,'$.operation_id')=:id",
        {"id": operation_id},
    )
    # The authenticated page uses exactly the same state transition.
    another = await pending(native, service, "page-cancel")
    await PagesAPI(service).cancel(request(0, request_id=uid()), another)
    assert (await service.db.read("SELECT state FROM operations WHERE id=:id", {"id": another}))[0][
        "state"
    ] == "cancelled"


async def test_local_cancel_requires_unique_target_and_owner_scope(native, service):
    one = await pending(native, service, "one")
    two = await pending(native, service, "two")
    incoming = event("choose-cancel")
    result = await invoke(native, incoming, "cancel", {})
    assert result["state"] == "selection_required" and len(result["pending"]) == 2
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "UPDATE operations SET plan=json_set(plan,'$.actor_key','other') WHERE id=:id",
            {"id": one},
        )
    refused = await invoke(native, incoming, "cancel", {"operation_id": one})
    assert refused["error"] == "OPERATION_NOT_FOUND"
    assert (await invoke(native, incoming, "cancel", {"operation_id": two}))["state"] == "cancelled"
    assert not service.gateway.writes


@pytest.mark.parametrize(
    "state,attempt,remote",
    [
        ("executing", 1, None),
        ("outcome_unknown", 1, None),
        ("succeeded", 1, "remote"),
        ("validated", 1, None),
        ("validated", 0, "remote"),
    ],
)
async def test_cancel_rejects_started_unknown_and_saved_results(
    native, service, state, attempt, remote
):
    operation_id = await pending(native, service)
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "UPDATE operations SET state=:s,attempt=:a,remote_id=:r WHERE id=:id",
            {"id": operation_id, "s": state, "a": attempt, "r": remote},
        )
    result = await invoke(
        native, event("cancel-too-late"), "cancel", {"operation_id": operation_id}
    )
    assert result["error"] == "OPERATION_IN_PROGRESS"
    assert (
        await service.db.read("SELECT state FROM operations WHERE id=:id", {"id": operation_id})
    )[0]["state"] == state
    assert not service.gateway.writes


async def test_cancel_and_worker_claim_race_has_one_consistent_outcome(native, service):
    operation_id = await pending(native, service, paused=False)
    cancelled, _ = await asyncio.gather(
        invoke(native, event("race-cancel"), "cancel", {"operation_id": operation_id}),
        service.execute_operation({"operation_id": operation_id}),
    )
    row = (await service.db.read("SELECT * FROM operations WHERE id=:id", {"id": operation_id}))[0]
    assert (row["state"] == "cancelled" and not service.gateway.writes) or (
        row["state"] == "succeeded"
        and len(service.gateway.writes) == 1
        and cancelled["error"] == "OPERATION_IN_PROGRESS"
    )


async def test_no_pending_cancel_keeps_unknown_results_and_strict_arguments(native, service):
    result = await invoke(native, event("none"), "cancel", {})
    assert result["state"] == "nothing_to_cancel" and not result["remote_write"]
    assert (await invoke(native, event("bad-cancel"), "cancel", {"delete": True}))[
        "error"
    ] == "INVALID_ARGUMENTS"
    assert not service.gateway.writes


async def test_cancel_does_not_choose_from_truncated_scope_and_explicit_id_still_works(
    native, service
):
    settings, _ = await service.db.settings()
    settings.max_jobs = 300
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE settings SET payload=:p", {"p": settings.model_dump_json()})
    ids = [await pending(native, service, f"scope-{index}") for index in range(102)]
    async with service.db.transaction() as conn:
        for operation_id in ids[:100]:
            await execute(
                conn, "UPDATE operations SET attempt=1 WHERE id=:id", {"id": operation_id}
            )
    result = await invoke(native, event("partial-scope"), "cancel", {})
    assert result["state"] == "selection_required" and result["pending_complete"] is False
    assert result["existing_results_complete"] is False and len(result["existing_results"]) == 20
    assert len(result["pending"]) == 1
    assert (await service.db.read("SELECT state FROM operations WHERE id=:id", {"id": ids[100]}))[
        0
    ]["state"] == "validated"
    result = await invoke(
        native, event("explicit-outside-page"), "cancel", {"operation_id": ids[101]}
    )
    assert result["state"] == "cancelled" and not service.gateway.writes
