"""Faults through the native facade, without the retired planner or receipts."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_native_tools import event, invoke

from notido.api import PagesAPI
from notido.cli import CLIResponse
from notido.db import execute
from notido.errors import NotiDoError
from notido.native import NativeTools
from notido.service import Service


@pytest.mark.parametrize("boundary", ["save_id", "save_verified_result"])
async def test_native_database_failure_after_write_never_recreates(
    native, service, monkeypatch, boundary
):
    import notido.service as execution

    original = execution.execute
    failed = False
    marker = (
        "UPDATE operations SET state=:s,remote_id=:remote"
        if boundary == "save_id"
        else "UPDATE operations SET state=:s,result=:r,checked_at=:t"
    )

    async def fail_once(conn, sql, params=None):
        nonlocal failed
        if marker in sql and not failed:
            failed = True
            raise OSError("injected result persistence failure")
        return await original(conn, sql, params)

    monkeypatch.setattr(execution, "execute", fail_once)
    incoming = event("native-persistence-failure")
    args = {"request_key": "r", "title": "已写但保存结果中断"}
    with pytest.raises(OSError, match="persistence failure"):
        await invoke(native, incoming, "create", args)
    assert failed and len(service.gateway.items) == len(service.gateway.writes) == 1
    row = (await service.db.read("SELECT * FROM operations"))[0]
    assert row["attempt"] == 1
    assert service.maintenance == "RESULT_PERSISTENCE_OR_VERIFICATION_FAILED"
    assert (await invoke(native, incoming, "create", args))["error"] == "MAINTENANCE"
    if boundary == "save_id":
        assert row["state"] == "outcome_unknown" and row["remote_id"] is None
    else:
        assert row["state"] == "created_unverified" and row["remote_id"]
    # Use the actual production startup and native facade against the same
    # persisted DB; the old planner and independent reply pipeline are absent.
    monkeypatch.setattr(execution, "execute", original)
    restarted = Service(service.root, service.bridge, service.gateway)
    await restarted.start()
    try:
        restored = NativeTools(restarted)
        await restarted.reconcile({"operation_id": row["id"]})
        observed = await invoke(restored, incoming, "create", args)
        current = (await restarted.db.read("SELECT * FROM operations"))[0]
        assert observed["operation_id"] == row["id"] and current["attempt"] == 1
        assert observed["state"] == ("outcome_unknown" if boundary == "save_id" else "succeeded")
        assert len(service.gateway.items) == len(service.gateway.writes) == 1
        assert len(await restarted.db.read("SELECT * FROM native_tool_calls")) == 1
        assert not await restarted.db.read("SELECT * FROM receipt_records")
        assert not service.bridge.calls
    finally:
        await restarted.stop()


def page_request(row, request_id):
    async def body():
        return {"request_id": request_id, "expected_revision": row["revision"]}

    return SimpleNamespace(json=body, username="admin")


async def test_native_partial_create_failure_retries_only_failed_action(native, service):
    incoming = event("native-partial")
    first_args = {"request_key": "one", "title": "提交报告"}
    second_args = {"request_key": "two", "title": "提交图片"}
    first = await invoke(native, incoming, "create", first_args)
    service.gateway.write_error = CLIResponse(error="AUTH_REQUIRED", side_effect="none")
    failed = await invoke(native, incoming, "create", second_args)
    assert first["state"] == "succeeded" and failed["state"] == "failed_safe"
    before = len(service.gateway.writes)
    # Framework duplicate calls return actual ledger results, never retry writes.
    assert await invoke(native, incoming, "create", first_args) == first
    assert await invoke(native, incoming, "create", second_args) == failed
    assert len(service.gateway.writes) == before
    rows = {r["id"]: r for r in await service.db.read("SELECT * FROM operations")}
    frozen_plan = rows[failed["operation_id"]]["plan"]
    api = PagesAPI(service)
    with pytest.raises(NotiDoError, match="副作用"):
        await api.retry(
            page_request(rows[first["operation_id"]], "no-retry-success"), first["operation_id"]
        )
    request = page_request(rows[failed["operation_id"]], "retry-failed-only")
    result = await api.retry(request, failed["operation_id"])
    assert await api.retry(request, failed["operation_id"]) == result
    service.gateway.write_error = None
    await service.execute_operation({"operation_id": failed["operation_id"]})
    # Also execute the stale queued job: terminal rows must remain untouched.
    await service.execute_operation({"operation_id": failed["operation_id"]})
    observed = await invoke(
        native, event("check"), "check", {"operation_id": failed["operation_id"]}
    )
    rows = {r["id"]: r for r in await service.db.read("SELECT * FROM operations")}
    assert observed["state"] == "succeeded" and len(service.gateway.items) == 2
    assert rows[first["operation_id"]]["attempt"] == 1
    assert rows[first["operation_id"]]["remote_id"] == first["remote_id"]
    assert rows[failed["operation_id"]]["attempt"] == 2
    assert rows[failed["operation_id"]]["plan"] == frozen_plan
    assert len(service.gateway.writes) == before + 1
    assert not service.bridge.calls and not await service.db.read("SELECT * FROM receipt_records")


async def test_native_partial_query_does_not_claim_complete_or_total(native, service):
    service.gateway.items[("p1", "visible")] = {
        "id": "visible",
        "projectId": "p1",
        "title": "可见任务",
        "status": 0,
    }
    service.gateway.failed_projects.add("p2")
    result = await invoke(native, event(), "query", {})
    assert not result["complete"] and result["total_count"] is None
    assert result["per_project_complete"] == {"p1": True, "p2": False}
    assert result["returned_count"] == 1 and result["tasks"][0]["id"] == "visible"
    assert not service.gateway.writes


async def test_native_unknown_receipt_does_not_promote_same_title_query(native, service):
    service.gateway.write_error = CLIResponse(error="CLI_TIMEOUT", side_effect="unknown")
    incoming = event("unknown-receipt")
    result = await invoke(
        native, incoming, "create", {"request_key": "r", "title": "查询存在不等于创建核验"}
    )
    service.gateway.items[("p1", "existing")] = {
        "id": "existing",
        "projectId": "p1",
        "title": "查询存在不等于创建核验",
        "status": 0,
    }
    queried = await invoke(native, incoming, "query", {"keyword": "查询存在不等于创建核验"})
    assert queried["tasks"][0]["id"] == "existing"
    checked = await invoke(native, incoming, "check", {"operation_id": result["operation_id"]})
    assert checked["state"] == "outcome_unknown" and checked["remote_id"] is None
    policy = checked["receipt_policy"]
    assert not policy["write_verified"] and not policy["same_title_query_verifies_this_operation"]
    assert policy["do_not_recreate_or_reupload"] and policy["do_not_offer_new_request_as_retry"]
    assert len(service.gateway.writes) == 1


async def test_native_verified_receipt_uses_durable_verification(native, service):
    result = await invoke(
        native, event("verified-receipt"), "create", {"request_key": "r", "title": "真正核验的创建"}
    )
    assert result["state"] == "succeeded" and result["verification"]["verified"]
    assert result["receipt_policy"]["write_verified"]
    assert "do_not_offer_new_request_as_retry" not in result["receipt_policy"]
    assert len(service.gateway.items) == len(service.gateway.writes) == 1


async def test_native_query_names_come_from_projects_not_notes(native, service):
    service.gateway.items[("p1", "task")] = {
        "id": "task",
        "projectId": "p1",
        "title": "核查任务",
        "content": "专用退出边界验收",
        "project_name": "远端非权威任务字段",
        "status": 0,
    }
    result = await invoke(native, event("list-name"), "query", {})
    assert result["tasks"][0]["project_name"] == "学习"
    assert result["project_names_complete"]
    assert result["query_scope"] == [
        {"project_id": "p1", "project_name": "学习"},
        {"project_id": "p2", "project_name": "生活"},
    ]
    assert not service.gateway.writes


async def test_native_query_missing_names_keeps_task_read_and_explicit_unknown(native, service):
    from notido.errors import NotiDoError

    async def unavailable():
        raise NotiDoError("READ_FAILED", "清单名称读取失败")

    service.gateway.projects = unavailable
    service.gateway.items[("p1", "task")] = {
        "id": "task",
        "projectId": "p1",
        "title": "核查任务",
        "status": 0,
    }
    result = await invoke(native, event("unknown-list-name"), "query", {})
    assert result["complete"] and result["total_count"] == 1
    assert result["tasks"][0]["project_name"] is None
    assert not result["project_names_complete"]
    assert all(item["project_name"] is None for item in result["query_scope"])
    assert not service.gateway.writes


async def test_native_restore_review_does_not_race_automatic_reconciliation(native, service):
    for index in range(21):
        await invoke(
            native,
            event(f"history-{index}"),
            "create",
            {"request_key": "r", "title": f"历史{index}"},
        )
    service.gateway.write_error = CLIResponse(error="CLI_TIMEOUT", side_effect="unknown")
    unknown = await invoke(
        native, event("unknown-history"), "create", {"request_key": "r", "title": "待核查历史"}
    )
    before = await service.db.read(
        "SELECT id,revision,state,attempt,remote_id FROM operations ORDER BY id"
    )
    (service.root / "restore-review.required").write_text("test-restore-marker", encoding="utf-8")
    restarted = Service(service.root, service.bridge, service.gateway)
    await restarted.start()
    try:
        api = PagesAPI(restarted)
        _, revision = await restarted.db.settings()
        first = await api.recovery_check(page_request({"revision": revision}, "review-first"))
        assert not first["ready"]
        # Let the actual production worker pass its dispatch/claim interval.
        await asyncio.sleep(0.65)

        async def body():
            return {
                "request_id": "review-second",
                "expected_revision": revision,
                "review_id": first["review_id"],
            }

        finished = await api.recovery_check(SimpleNamespace(json=body, username="admin"))
        assert finished["ready"] and first["checked"] + finished["checked"] == 22
        review = json.loads((await api.recovery_status(None))["review"]["payload"])
        assert len(review["history"]) == 22
        assert before == await restarted.db.read(
            "SELECT id,revision,state,attempt,remote_id FROM operations ORDER BY id"
        )
        jobs = await restarted.db.read(
            "SELECT state FROM jobs WHERE kind='reconcile' AND json_extract(payload,'$.operation_id')=:id",
            {"id": unknown["operation_id"]},
        )
        assert jobs and all(job["state"] == "pending" for job in jobs)
        assert len(service.gateway.writes) == 22 and not service.bridge.calls
    finally:
        await restarted.stop()


@pytest.mark.parametrize("change", ["actor", "project", "credential"])
async def test_native_failed_action_cannot_resume_after_scope_changed(native, service, change):
    service.gateway.write_error = CLIResponse(error="AUTH_REQUIRED", side_effect="none")
    failed = await invoke(native, event(), "create", {"request_key": "r", "title": "未写任务"})
    row = (await service.db.read("SELECT * FROM operations"))[0]
    await PagesAPI(service).retry(page_request(row, "retry-before-change"), row["id"])
    settings, _ = await service.db.settings()
    async with service.db.transaction() as conn:
        if change == "actor":
            await execute(conn, "UPDATE actor_bindings SET enabled=0,revision=revision+1")
        else:
            if change == "project":
                settings.allowed_projects = ["p2"]
            else:
                settings.credential_generation += 1
            await execute(
                conn,
                "UPDATE settings SET payload=:p,revision=revision+1",
                {"p": settings.model_dump_json()},
            )
    service.gateway.write_error = None
    await service.execute_operation({"operation_id": failed["operation_id"]})
    actual = (await service.db.read("SELECT * FROM operations"))[0]
    assert actual["paused"] and actual["attempt"] == 1 and actual["remote_id"] is None
    assert len(service.gateway.writes) == 1 and not service.gateway.items
    assert json.loads(actual["result"])["error"]["code"] in {
        "CONFIG_OR_AUTHORIZATION_CHANGED",
        "ACCOUNT_CHANGED",
        "NOT_AUTHORIZED",
        "PROJECT_NOT_ALLOWED",
    }
    assert not service.bridge.calls
