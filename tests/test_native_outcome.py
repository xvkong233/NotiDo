import json
from types import SimpleNamespace

from conftest import envelope

from notido.cli import CLIResponse
from notido.db import execute
from notido.models import Segment


def event(message="outcome-1"):
    return SimpleNamespace(
        envelope=envelope(message=message), message_obj=SimpleNamespace(message_id=message)
    )


async def invoke(native, incoming, operation, args):
    return json.loads(await native.invoke(incoming, operation, args))


async def test_native_admission_and_saved_task_need_astrbot_conclusion(native, service):
    incoming = event()
    created = await invoke(native, incoming, "create", {"request_key": "one", "title": "报告"})
    group = created["material_group_id"]
    assert (await service.db.read("SELECT state FROM material_groups WHERE id=:g", {"g": group}))[
        0
    ]["state"] == "collecting"
    pending = await invoke(
        native,
        incoming,
        "outcome",
        {"state": "awaiting_clarification", "pending_reason": "另一个自愿事项尚未决定参加"},
    )
    assert pending["state"] == "partially_done" and not pending["remote_write"]
    assert pending["has_saved_work"] and pending["state_source"] == "local_ledger"
    assert "declared_state" not in pending
    stored = (
        await service.db.read(
            "SELECT declaration FROM native_group_outcomes WHERE group_id=:g", {"g": group}
        )
    )[0]
    assert json.loads(stored["declaration"])["state"] == "awaiting_clarification"
    # Neither the transport simplification nor the declaration can erase the
    # independently saved task from the notification's actual progress.
    assert (await service.db.read("SELECT state FROM material_groups WHERE id=:g", {"g": group}))[
        0
    ]["state"] == "partially_done"
    finished = await invoke(
        native, event("answer"), "outcome", {"group_id": group, "state": "completed"}
    )
    assert finished["state"] == "completed"
    assert len(service.gateway.writes) == 1 and not service.bridge.calls
    assert not await service.db.read("SELECT * FROM question_history")
    again = await invoke(native, incoming, "create", {"request_key": "two", "title": "另一份报告"})
    assert again["state"] == "succeeded"
    assert (await service.db.read("SELECT state FROM material_groups WHERE id=:g", {"g": group}))[
        0
    ]["state"] == "collecting"


async def test_native_zero_action_outcomes_are_explicit_and_idempotent(native, service):
    incoming = event()
    result = await native.invoke(incoming, "materials", {})
    group = json.loads(result.content[0].text)["group_id"]
    waiting = await invoke(
        native,
        incoming,
        "outcome",
        {"state": "awaiting_clarification", "pending_reason": "是否参加"},
    )
    assert waiting["state"] == "awaiting_clarification"
    finished = await invoke(native, incoming, "outcome", {"state": "completed"})
    revision = (
        await service.db.read("SELECT revision FROM material_groups WHERE id=:g", {"g": group})
    )[0]["revision"]
    assert finished["state"] == "completed"
    assert finished == await invoke(native, incoming, "outcome", {"state": "completed"})
    assert (
        revision
        == (
            await service.db.read("SELECT revision FROM material_groups WHERE id=:g", {"g": group})
        )[0]["revision"]
    )
    assert not service.gateway.writes and not service.bridge.calls


async def test_native_outcome_cannot_hide_unknown_or_failed_operations(native, service):
    incoming = event()
    service.gateway.write_error = CLIResponse(error="CLI_TIMEOUT", side_effect="unknown")
    created = await invoke(native, incoming, "create", {"request_key": "one", "title": "报告"})
    finished = await invoke(native, incoming, "outcome", {"state": "completed"})
    assert finished["state"] == "awaiting_clarification"
    assert finished["unresolved_operations"][0]["operation_id"] == created["operation_id"]
    assert len(service.gateway.writes) == 1
    service.gateway.write_error = None
    saved = await invoke(native, incoming, "create", {"request_key": "two", "title": "独立项"})
    assert saved["state"] == "succeeded"
    finished = await invoke(native, incoming, "outcome", {"state": "completed"})
    assert finished["state"] == "partially_done"


async def test_native_outcome_requires_scope_and_credential_generation(native, service):
    incoming = event()
    created = await invoke(native, incoming, "create", {"request_key": "one", "title": "报告"})
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "INSERT INTO actor_bindings VALUES ('other','personal','test-instance','other','session',1,0)",
        )
    alien = event("alien")
    alien.envelope.actor_key = "other"
    args = {"group_id": created["material_group_id"], "state": "completed"}
    assert (await invoke(native, alien, "outcome", args))["error"] == "MATERIAL_NOT_FOUND"
    settings, _ = await service.db.settings()
    settings.credential_generation += 1
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE settings SET payload=:p", {"p": settings.model_dump_json()})
    assert (await invoke(native, incoming, "outcome", args))["error"] == "MATERIAL_NOT_FOUND"


async def test_native_outcome_cannot_complete_unread_or_damaged_material(native, service, tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf")
    incoming = event()
    incoming.envelope.segments.append(Segment(source_id="file", kind="file"))

    async def acquire(source):
        return str(path), "broken.pdf"

    service.bridge.acquire_material = acquire
    await native.invoke(incoming, "materials", {})
    completed = await invoke(native, incoming, "outcome", {"state": "completed"})
    assert completed["state"] == "awaiting_materials" and completed["unknown_materials"]
    assert not service.gateway.writes


async def test_native_attachment_obligation_needs_actual_verified_link(native, service, tmp_path):
    incoming = event()
    path = tmp_path / "notice.txt"
    path.write_text("本人须提交报告。", encoding="utf-8")
    incoming.envelope.segments.append(Segment(source_id="file", kind="file"))

    async def acquire(source):
        return str(path), "notice.txt"

    service.bridge.acquire_material = acquire
    manifest = json.loads((await native.invoke(incoming, "materials", {})).content[0].text)
    created = await invoke(native, incoming, "create", {"request_key": "one", "title": "报告"})
    attachment = {
        "asset_id": manifest["assets"][0]["id"],
        "project_id": "p1",
        "task_id": created["remote_id"],
    }
    args = {"state": "completed", "attachments": [attachment]}
    pending = await invoke(native, incoming, "outcome", args)
    assert pending["state"] == "task_saved_attachments_pending" and pending[
        "pending_attachments"
    ] == [attachment]
    invalid = await invoke(
        native,
        incoming,
        "outcome",
        {**args, "attachments": [{**attachment, "task_id": "arbitrary"}]},
    )
    assert invalid["error"] == "ATTACHMENT_TARGET_INVALID"
    blob = (
        await service.db.read(
            "SELECT hash FROM assets WHERE id=:id", {"id": attachment["asset_id"]}
        )
    )[0]["hash"]
    async with service.db.transaction() as conn:
        parent = (
            await service.db.read(
                "SELECT plan FROM operations WHERE id=:id", {"id": created["operation_id"]}
            )
        )[0]["plan"]
        await execute(
            conn,
            "INSERT INTO operations SELECT 'upload','personal',account_ref,NULL,'upload','upload',:plan,'uploaded_unverified',0,'attachment',NULL,1,0,0,NULL FROM operations WHERE id=:id",
            {"plan": parent, "id": created["operation_id"]},
        )
        await execute(
            conn,
            "INSERT INTO task_attachment_links VALUES ('link','account-a','p1',:task,:hash,:asset,'attachment','upload',0)",
            {"task": attachment["task_id"], "hash": blob, "asset": attachment["asset_id"]},
        )
    pending = await invoke(native, incoming, "outcome", args)
    assert pending["pending_attachments"]
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE operations SET state='succeeded' WHERE id='upload'")
        await execute(conn, "UPDATE task_attachment_links SET verified=1 WHERE id='link'")
    finished = await invoke(native, incoming, "outcome", args)
    assert finished["state"] == "completed" and not finished["pending_attachments"]


async def test_native_unresolved_reason_and_schema_are_strict(native, service):
    incoming = event()
    await invoke(native, incoming, "query", {})
    for args in (
        {"state": "awaiting_clarification"},
        {"state": "completed", "pending_reason": "缺件"},
        {"state": "completed", "new_memory": "private"},
    ):
        result = await invoke(native, incoming, "outcome", args)
        assert result["error"] == "INVALID_ARGUMENTS" and result["side_effect"] == "none"
    assert not service.gateway.writes


async def test_native_delete_preview_cannot_be_declared_complete(native, service):
    from test_recurrence_delete import deletion

    _, incoming, preview = await deletion(native)
    concluded = await invoke(native, incoming, "outcome", {"state": "completed"})
    assert concluded["state"] == "awaiting_clarification"
    assert concluded["awaiting_delete_confirmation"]
    assert len(service.gateway.writes) == 1
    assert preview["material_group_id"] == concluded["group_id"]


async def test_direct_clarification_can_record_without_other_tools_or_new_question_machine(
    native, service
):
    incoming = event("unknown-project")
    result = await invoke(
        native,
        incoming,
        "outcome",
        {"state": "awaiting_clarification", "pending_reason": "请选择真实清单"},
    )
    assert result["state"] == "awaiting_clarification" and not result["remote_write"]
    assert len(await service.db.read("SELECT * FROM material_groups")) == 1
    assert result == await invoke(
        native,
        incoming,
        "outcome",
        {"state": "awaiting_clarification", "pending_reason": "请选择真实清单"},
    )
    assert not service.gateway.writes and not service.bridge.calls
    assert not await service.db.read("SELECT * FROM question_history")


async def test_confirmed_delete_returns_original_group_for_native_conclusion(native, service):
    from test_native_tools import event as delete_event
    from test_recurrence_delete import deletion

    _, incoming, preview = await deletion(native)
    await invoke(
        native,
        incoming,
        "outcome",
        {"state": "awaiting_clarification", "pending_reason": "等待确认删除"},
    )
    confirmed = delete_event("confirmed-delete", text="确认删除")
    args = {
        "request_key": "delete",
        "confirmation_ref": preview["confirmation_ref"],
        "confirm": True,
    }
    result = await invoke(native, confirmed, "delete", args)
    assert result["related_material_group_ids"] == [preview["material_group_id"]]
    assert (await invoke(native, confirmed, "delete", args))[
        "related_material_group_ids"
    ] == result["related_material_group_ids"]
    # A saved deletion is a fact, but resolving the original semantic question
    # remains an explicit AstrBot declaration rather than parsing its reply.
    for group in [*result["related_material_group_ids"], result["material_group_id"]]:
        conclusion = await invoke(
            native, confirmed, "outcome", {"group_id": group, "state": "completed"}
        )
        assert conclusion["state"] == "completed" and not conclusion["awaiting_delete_confirmation"]
    assert [row[0] for row in service.gateway.writes] == ["create", "delete"]
