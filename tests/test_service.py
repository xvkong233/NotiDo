import asyncio
import json
from datetime import UTC, datetime

import pytest
from conftest import create_plan, envelope, operation, parsed

from notido.cli import CLIResponse
from notido.db import execute
from notido.errors import NotiDoError
from notido.models import Patch, Query, Target
from notido.query import read_scope


async def test_authorization_precedes_model_and_gateway(service):
    with pytest.raises(NotiDoError) as error:
        await service.intake(envelope(session="unbound"))
    assert error.value.code == "NOT_AUTHORIZED"
    assert not service.bridge.calls and not service.gateway.writes


async def test_missing_framework_id_blocks(service):
    value = envelope().model_copy(update={"message_key": None})
    with pytest.raises(NotiDoError) as error:
        await service.intake(value)
    assert error.value.code == "FRAMEWORK_ID_UNAVAILABLE"
    assert not await service.db.read("SELECT * FROM message_records")


async def test_revoked_actor_cannot_borrow_another_binding_in_same_session(service):
    service.bridge.plan = create_plan
    await parsed(service)
    op = await operation(service)
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE actor_bindings SET enabled=0 WHERE id='binding'")
        await execute(
            conn,
            "INSERT INTO actor_bindings VALUES ('other','personal','test-instance','another-actor','session',1,0)",
        )
    await service.execute_operation({"operation_id": op["id"]})
    assert not service.gateway.writes
    assert (await operation(service))["paused"] == 1


async def test_create_verify_and_idempotency(service):
    service.bridge.plan = create_plan
    result = await parsed(service)
    op = await operation(service)
    await asyncio.gather(
        service.execute_operation({"operation_id": op["id"]}),
        service.execute_operation({"operation_id": op["id"]}),
    )
    assert len(service.gateway.writes) == 1
    assert (await operation(service))["state"] == "succeeded"
    duplicate = await service.intake(envelope())
    assert duplicate["duplicate"] and duplicate["group_id"] == result["group_id"]
    assert len(service.bridge.calls) == 1
    assert len(await service.db.read("SELECT * FROM notice_task_links")) == 1
    receipt_count = len(await service.db.read("SELECT * FROM receipt_records"))
    await service.reconcile({"operation_id": op["id"]})
    assert len(await service.db.read("SELECT * FROM receipt_records")) == receipt_count


async def test_new_identical_direct_message_is_new_request(service):
    service.bridge.plan = create_plan
    await parsed(service)
    first = await operation(service)
    await service.execute_operation({"operation_id": first["id"]})
    await parsed(service, message="new-message")
    assert len(await service.db.read("SELECT * FROM operations")) == 2


async def test_repeated_notice_reuses_verified_task(service):
    service.bridge.plan = lambda payload: create_plan(payload, intent="ingest_notice")
    await parsed(service, text="2027年12月20日提交报告", source_kind="manual_notice")
    first = await operation(service)
    await service.execute_operation({"operation_id": first["id"]})
    await parsed(
        service, message="new-notice", text="2027年12月20日提交报告", source_kind="manual_notice"
    )
    assert len(await service.db.read("SELECT * FROM operations")) == 1
    assert len(service.gateway.writes) == 1


@pytest.mark.parametrize(
    ("relevance", "obligation", "expect_question"),
    [
        ("not_applies", "required", False),
        ("applies", "informational", False),
        ("unknown", "required", True),
        ("applies", "optional", True),
    ],
)
async def test_notice_dispositions(service, relevance, obligation, expect_question):
    service.bridge.plan = lambda p: create_plan(
        p, intent="ingest_notice", relevance=relevance, obligation=obligation
    )
    await parsed(service, text="2027年12月20日提交报告", source_kind="manual_notice")
    assert not await service.db.read("SELECT * FROM operations")
    session = (await service.db.read("SELECT * FROM sessions"))[0]
    assert bool(session["question"]) == expect_question


async def test_summary_only_outer_instruction_is_enforced(service):
    service.bridge.plan = create_plan
    await parsed(service, text="只总结，不写入：2027年12月20日提交报告")
    assert not await service.db.read("SELECT * FROM operations")


async def test_fabricated_date_and_quote_block(service):
    service.bridge.plan = lambda p: create_plan(p, date_text="2027年12月21日")
    await parsed(service)
    assert not await service.db.read("SELECT * FROM operations")
    assert (
        "DATE_EVIDENCE_INVALID"
        in (await service.db.read("SELECT question FROM sessions"))[0]["question"]
    )


async def test_unknown_forward_date_and_overdue(service):
    service.bridge.plan = lambda p: create_plan(p, intent="ingest_notice", date_text="明天")
    await parsed(service, text="明天提交报告", source_kind="user_forward")
    assert not await service.db.read("SELECT * FROM operations")
    assert (
        "TIME_ANCHOR_UNKNOWN"
        in (await service.db.read("SELECT question FROM sessions"))[0]["question"]
    )
    # Distinct scenarios cannot replace an existing live question.
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE sessions SET question_expires=0")
    service.bridge.plan = lambda p: create_plan(p, date_text="2020年12月20日")
    await parsed(service, message="past", text="记一下 2020年12月20日提交报告")
    assert not await service.db.read("SELECT * FROM operations")
    assert (
        "OVERDUE_CONFIRMATION"
        in (await service.db.read("SELECT question FROM sessions"))[0]["question"]
    )


async def test_without_date_stays_undated(service):
    service.bridge.plan = lambda p: create_plan(p, date_text=None)
    await parsed(service, text="记一下提交报告")
    op = await operation(service)
    assert json.loads(op["plan"])["normalized_date"]["kind"] == "none"
    await service.execute_operation({"operation_id": op["id"]})
    assert not next(iter(service.gateway.items.values())).get("dueDate")


async def test_comparison_keeps_source_endpoint_after_explicit_answer(service):
    # Even if the model omits 前 from date_text, the quoted endpoint still blocks.
    service.bridge.plan = create_plan
    group = await parsed(service, text="记一下 2027年12月20日前提交报告")
    assert not await operation(service)
    question = json.loads((await service.db.read("SELECT question FROM sessions"))[0]["question"])
    clarification = service.answer_context(question, "当天不可提交")
    await service.parse_group({"group_id": group["group_id"], "clarification": clarification})
    plan = json.loads((await operation(service))["plan"])
    assert plan["normalized_date"]["comparison"] == "before"
    assert plan["normalized_date"]["raw_text"] == "2027年12月20日前"
    assert plan["fields"]["dueDate"].startswith("2027-12-20T00:00:00")


async def test_unknown_write_is_never_retried(service):
    service.bridge.plan = create_plan
    await parsed(service)
    op = await operation(service)
    service.gateway.write_error = CLIResponse(error="CLI_TIMEOUT", side_effect="unknown")
    await service.execute_operation({"operation_id": op["id"]})
    assert (await operation(service))["state"] == "outcome_unknown"
    await service.execute_operation({"operation_id": op["id"]})
    await service.reconcile({"operation_id": op["id"]})
    assert len(service.gateway.writes) == 1
    assert (await operation(service))["state"] == "outcome_unknown"


async def test_saved_id_survives_verification_failure(service):
    service.bridge.plan = create_plan
    await parsed(service)
    op = await operation(service)
    service.gateway.read_error = True
    await service.execute_operation({"operation_id": op["id"]})
    after = await operation(service)
    assert after["remote_id"] and after["state"] == "created_unverified"
    await service.execute_operation({"operation_id": op["id"]})
    assert len(service.gateway.writes) == 1
    service.gateway.read_error = False
    await service.reconcile({"operation_id": op["id"]})
    assert (await operation(service))["state"] == "succeeded"


async def test_revocation_pauses_existing_plan(service):
    service.bridge.plan = create_plan
    await parsed(service)
    op = await operation(service)
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE actor_bindings SET enabled=0")
    await service.execute_operation({"operation_id": op["id"]})
    assert not service.gateway.writes
    assert (await operation(service))["paused"] == 1


async def test_reply_retry_does_not_recreate(service):
    service.bridge.plan = create_plan
    await parsed(service)
    op = await operation(service)
    await service.execute_operation({"operation_id": op["id"]})
    receipt = (await service.db.read("SELECT * FROM receipt_records"))[0]
    service.bridge.reply_state = "failed"
    await service.send_receipt({"receipt_id": receipt["id"]})
    assert (await service.db.read("SELECT state FROM receipt_records"))[0]["state"] == "failed"
    service.bridge.reply_state = "sent"
    await service.send_receipt({"receipt_id": receipt["id"]})
    assert len(service.gateway.writes) == 1


async def test_partial_query_and_keyword_cannot_locate(service):
    service.gateway.items[("p1", "task")] = {
        "id": "task",
        "projectId": "p1",
        "title": "报告",
        "status": 0,
    }
    service.gateway.failed_projects.add("p2")
    settings, _ = await service.db.settings()
    result = await read_scope(service.gateway, settings, datetime.now(UTC), Query())
    assert len(result["tasks"]) == 1 and result["total_count"] is None and not result["complete"]
    with pytest.raises(NotiDoError) as error:
        await service.locate(Target(keyword="报告"), "session", settings)
    assert error.value.code == "QUERY_INCOMPLETE"


async def test_changed_page_and_old_selection_invalidated(service):
    await service.intake(envelope())
    for index in range(12):
        service.gateway.items[("p1", str(index))] = {
            "id": str(index),
            "projectId": "p1",
            "title": f"任务 {index:02}",
            "status": 0,
        }
    await service.query("session", "origin", Query())
    session = (await service.db.read("SELECT * FROM sessions"))[0]
    old = json.loads(session["query"])["selection"][0]["selection_ref"]
    service.gateway.items[("p1", "new")] = {
        "id": "new",
        "projectId": "p1",
        "title": "新增",
        "status": 0,
    }
    with pytest.raises(NotiDoError) as error:
        await service.next_page("session", "origin")
    assert error.value.code == "QUERY_CHANGED"
    settings, _ = await service.db.settings()
    with pytest.raises(NotiDoError):
        await service.locate(Target(selection_ref=old), "session", settings)


async def test_date_only_patch_preserves_time(service):
    settings, _ = await service.db.settings()
    fields = await service.patch_fields(
        Patch(date_text="2027-12-25"),
        {"dueDate": "2027-12-20T15:30:00+0800", "isAllDay": False},
        settings,
        datetime.now(UTC),
    )
    assert fields["dueDate"] == "2027-12-25T15:30:00+0800"
    fields = await service.patch_fields(
        Patch(time_text="16:00"),
        {"dueDate": "2027-12-20T15:30:00+0800", "isAllDay": False},
        settings,
        datetime.now(UTC),
    )
    assert fields["dueDate"] == "2027-12-20T16:00:00+0800"


async def test_explicit_collection_expiry_keeps_draft(service):
    result = await service.intake(envelope(text="开始收集"))
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE material_groups SET closes_at=0")
    await service.close_group({"group_id": result["group_id"]})
    assert not await service.db.read("SELECT * FROM operations")
    assert (await service.db.read("SELECT state FROM material_groups"))[0][
        "state"
    ] == "awaiting_clarification"


async def test_restart_executing_becomes_unknown_not_validated(service):
    service.bridge.plan = create_plan
    await parsed(service)
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE operations SET state='executing'")
    await service.db.initialize()
    assert (await operation(service))["state"] == "outcome_unknown"
    assert not service.gateway.writes


async def test_optional_answer_is_specific(service):
    question = {
        "question_ref": "q",
        "context": {"blocked": [{"code": "OPTIONAL_CHOICE"}, {"code": "OVERDUE_CONFIRMATION"}]},
    }
    answer = service.answer_context(question, "是")
    assert "participation_title" not in answer and "allow_overdue" not in answer
    assert service.answer_context(question, "参加 报名比赛")["participation_title"] == "报名比赛"
    assert service.answer_context(question, "补记逾期")["allow_overdue"] is True
