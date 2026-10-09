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


async def paused(service):
    service.bridge.plan = create_plan
    await parsed(service)
    op = await operation(service)
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "UPDATE operations SET paused=1,revision=revision+1 WHERE id=:id",
            {"id": op["id"]},
        )
        plan = json.loads(op["plan"])
        await service.ask(
            conn, plan["group_id"], ["原计划已暂停，请核验。"], {"operation_id": op["id"]}
        )
    return await operation(service)


async def test_revalidate_preserves_immutable_history_and_stable_operation_key(service):
    op = await paused(service)
    old = json.loads(op["plan"])
    api = PagesAPI(service)
    req = request(op["revision"], confirm=True)
    result = await api.revalidate(req, op["id"])
    assert result == await api.revalidate(req, op["id"])
    current = await operation(service)
    new = json.loads(current["plan"])
    versions = await service.db.read("SELECT * FROM operation_plan_versions ORDER BY id")
    assert len(versions) == 2
    assert json.loads(versions[0]["payload"]) == old
    assert new["fields"] == old["fields"] and new["normalized_date"] == old["normalized_date"]
    assert new["supersedes_plan_id"] == old["plan_id"] and new["plan_id"] != old["plan_id"]
    assert current["operation_key"] == op["operation_key"] and not current["paused"]
    assert (await service.db.read("SELECT question FROM sessions"))[0]["question"] is None
    await service.execute_operation({"operation_id": op["id"]})
    assert (await operation(service))["state"] == "succeeded" and len(service.gateway.writes) == 1
    assert (await service.db.read("SELECT state FROM material_groups"))[0]["state"] == "completed"


async def test_current_identity_requires_explicit_applicability_consent(service):
    op = await paused(service)
    settings, _ = await service.db.settings()
    settings.identity.roles = ["班委"]
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "UPDATE settings SET payload=:p,revision=revision+1",
            {"p": settings.model_dump_json()},
        )
    api = PagesAPI(service)
    with pytest.raises(NotiDoError) as caught:
        await api.revalidate(request(op["revision"], confirm=True), op["id"])
    assert caught.value.code == "APPLICABILITY_CONFIRMATION_REQUIRED"
    await api.revalidate(
        request(op["revision"], "explicit", confirm=True, confirm_applicability=True), op["id"]
    )
    await service.execute_operation({"operation_id": op["id"]})
    assert len(service.gateway.writes) == 1


@pytest.mark.parametrize(
    "state,attempt",
    [("outcome_unknown", 1), ("created_unverified", 1), ("succeeded", 1), ("validated", 1)],
)
async def test_started_or_unknown_operations_never_revalidate(service, state, attempt):
    op = await paused(service)
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "UPDATE operations SET state=:s,attempt=:a WHERE id=:id",
            {"s": state, "a": attempt, "id": op["id"]},
        )
    with pytest.raises(NotiDoError) as caught:
        await PagesAPI(service).revalidate(request(op["revision"], confirm=True), op["id"])
    assert caught.value.code == "PLAN_REVALIDATION_UNSAFE"
    assert len(await service.db.read("SELECT * FROM operation_plan_versions")) == 1
    assert not service.gateway.writes


async def test_account_change_never_reuses_old_plan(service):
    op = await paused(service)
    settings, _ = await service.db.settings()
    settings.account_ref = "other-account"
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE settings SET payload=:p", {"p": settings.model_dump_json()})
    with pytest.raises(NotiDoError) as caught:
        await PagesAPI(service).revalidate(request(op["revision"], confirm=True), op["id"])
    assert caught.value.code == "ACCOUNT_CHANGED" and not service.gateway.writes


async def test_expired_question_continues_with_new_reference_and_old_context(service):
    service.bridge.plan = lambda payload: create_plan(payload, obligation="optional")
    group = await parsed(service)
    session = (await service.db.read("SELECT * FROM sessions"))[0]
    previous = json.loads(session["question"])
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE sessions SET question_expires=:t", {"t": time.time() - 1})
    group_row = (await service.db.read("SELECT * FROM material_groups"))[0]
    api = PagesAPI(service)
    req = request(group_row["revision"])
    result = await api.continue_notice(req, group["group_id"])
    assert result == await api.continue_notice(req, group["group_id"])
    fresh = result["question"]
    assert fresh["question_ref"] != previous["question_ref"]
    assert fresh["context"] == previous["context"] and fresh["questions"] == previous["questions"]
    with pytest.raises(NotiDoError) as caught:
        await service.resolve_question(
            "session", previous["question_ref"], "参加 提交报告", "origin"
        )
    assert caught.value.code == "QUESTION_EXPIRED"
    await service.resolve_question("session", fresh["question_ref"], "参加 提交报告", "origin")
    job = (
        await service.db.read(
            "SELECT * FROM jobs WHERE dedupe_key=:d", {"d": f"clarify:{fresh['question_ref']}"}
        )
    )[0]
    await service.parse_group(json.loads(job["payload"]))
    assert len(await service.db.read("SELECT * FROM operations")) == 1


async def test_continuation_does_not_displace_an_active_question(service):
    service.bridge.plan = lambda payload: create_plan(payload, obligation="optional")
    group = await parsed(service)
    row = (await service.db.read("SELECT * FROM material_groups"))[0]
    with pytest.raises(NotiDoError) as caught:
        await PagesAPI(service).continue_notice(request(row["revision"]), group["group_id"])
    assert caught.value.code == "ACTIVE_QUESTION_EXISTS"


async def test_reauthorization_cannot_clear_restore_review_gate(service):
    marker = service.root / "restore-review.required"
    marker.write_text("remote history review required", encoding="utf-8")
    api = PagesAPI(service)
    api.mark_auth_commit_unknown()
    api.clear_auth_commit_unknown()
    assert service.maintenance == "RESTORE_REMOTE_REVIEW_REQUIRED"
    assert marker.exists()
    assert not (service.root / "maintenance.required").exists()
