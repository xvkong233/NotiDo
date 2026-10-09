import pytest
from test_native_tools import event, invoke
from test_restore_review import request

from notido.api import PagesAPI
from notido.cli import CLIResponse
from notido.db import execute
from notido.errors import NotiDoError
from notido.retention import DAY


async def deletion(native):
    created = await invoke(
        native, event("create-delete"), "create", {"request_key": "create", "title": "专用删除测试"}
    )
    incoming = event("preview-delete", text="请删除专用删除测试")
    selected = (await invoke(native, incoming, "query", {}))["tasks"][0]["selection_ref"]
    preview = await invoke(
        native, incoming, "delete", {"request_key": "delete", "selection_ref": selected}
    )
    assert preview["state"] == "awaiting_confirmation"
    return created, incoming, preview


async def test_native_recurring_create_has_native_rule_and_exact_first_time(native, service):
    result = await invoke(
        native,
        event(),
        "create",
        {
            "request_key": "weekly",
            "title": "每周报告",
            "date_text": "2027年12月20日",
            "time_text": "17:00",
            "recurrence": {"frequency": "weekly", "weekdays": ["MO"], "count": 5},
        },
    )
    assert result["state"] == "succeeded"
    assert result["actual_fields"]["repeatFlag"] == "RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=MO;COUNT=5"
    assert result["actual_fields"]["repeatFrom"] == "2" and len(service.gateway.writes) == 1


@pytest.mark.parametrize(
    "arguments",
    [
        {"recurrence": {"frequency": "daily"}},
        {"date_text": "2027年12月21日", "recurrence": {"frequency": "weekly", "weekdays": ["MO"]}},
        {
            "date_text": "2027年12月20日",
            "recurrence": {"frequency": "daily", "count": 3, "end_date": "2027-12-25"},
        },
        {"date_text": "2027年12月20日", "recurrence": {"frequency": "monthly", "month_days": [0]}},
        {
            "date_text": "2027年12月20日",
            "recurrence": {"frequency": "weekly", "weekdays": ["MO"], "repeat_from": "completion"},
        },
        {"date_text": "2027年12月20日", "recurrence": {"frequency": "monthly", "month_days": [-1]}},
        {"date_text": "2027年12月20日", "recurrence": {"frequency": "yearly", "months": [1]}},
    ],
)
async def test_unresolved_or_contradictory_repeat_never_degrades_to_one_off(
    native, service, arguments
):
    result = await invoke(
        native, event(), "create", {"request_key": "r", "title": "周期任务", **arguments}
    )
    assert result["state"] == "blocked" and not service.gateway.writes


async def test_delete_requires_a_later_explicit_message_and_is_idempotent(native, service):
    created, incoming, preview = await deletion(native)
    args = {
        "request_key": "delete",
        "confirmation_ref": preview["confirmation_ref"],
        "confirm": True,
    }
    assert (await invoke(native, incoming, "delete", args))[
        "error"
    ] == "SECOND_CONFIRMATION_REQUIRED"
    assert (await invoke(native, event("not-consent", text="先不要删除"), "delete", args))[
        "error"
    ] == "SECOND_CONFIRMATION_REQUIRED"
    confirmed = event("confirmed-delete", text="确认删除")
    result = await invoke(native, confirmed, "delete", args)
    assert result["state"] == "succeeded" and result["actual_fields"]["deleted"]
    assert result["remote_id"] == created["remote_id"]
    assert (await invoke(native, confirmed, "delete", args))["operation_id"] == result[
        "operation_id"
    ]
    assert [row[0] for row in service.gateway.writes] == ["create", "delete"]


@pytest.mark.parametrize(
    "change,error",
    [
        ("edit", "TARGET_CHANGED"),
        ("expire", "CONFIRMATION_EXPIRED"),
        ("scope", "PROJECT_NOT_ALLOWED"),
    ],
)
async def test_delete_confirmation_changes_never_write(native, service, change, error):
    created, _, preview = await deletion(native)
    if change == "edit":
        service.gateway.items[("p1", created["remote_id"])]["content"] = "用户追加说明"
    elif change == "expire":
        async with service.db.transaction() as conn:
            await execute(conn, "UPDATE delete_confirmations SET expires_at=0")
    else:
        settings, _ = await service.db.settings()
        settings.allowed_projects = []
        async with service.db.transaction() as conn:
            await execute(conn, "UPDATE settings SET payload=:p", {"p": settings.model_dump_json()})
    result = await invoke(
        native,
        event("confirm-delete", text="确认删除"),
        "delete",
        {"request_key": "delete", "confirmation_ref": preview["confirmation_ref"], "confirm": True},
    )
    assert result["error"] == error and len(service.gateway.writes) == 1


async def test_delete_unknown_cannot_be_replayed_or_promoted_without_absence_proof(native, service):
    _, _, preview = await deletion(native)
    service.gateway.write_error = CLIResponse(error="CLI_TIMEOUT", side_effect="unknown")
    args = {
        "request_key": "delete",
        "confirmation_ref": preview["confirmation_ref"],
        "confirm": True,
    }
    result = await invoke(native, event("confirmed-delete", text="确认删除"), "delete", args)
    assert result["state"] == "outcome_unknown"
    checked = await invoke(
        native, event("check-delete"), "check", {"operation_id": result["operation_id"]}
    )
    assert checked["state"] == "outcome_unknown" and not checked["actual_fields"]["deleted"]
    await invoke(native, event("another-confirm", text="确认删除"), "delete", args)
    assert len(service.gateway.writes) == 2


async def test_delete_failed_safe_cannot_retry_from_dashboard(native, service):
    _, _, preview = await deletion(native)
    service.gateway.write_error = CLIResponse(error="CLI_DELETE_REJECTED", side_effect="none")
    result = await invoke(
        native,
        event("confirm", text="确认删除"),
        "delete",
        {
            "request_key": "delete",
            "confirmation_ref": preview["confirmation_ref"],
            "confirm": True,
        },
    )
    row = (
        await service.db.read(
            "SELECT * FROM operations WHERE id=:id", {"id": result["operation_id"]}
        )
    )[0]
    with pytest.raises(NotiDoError) as caught:
        await PagesAPI(service).retry(request(row["revision"]), row["id"])
    assert caught.value.code == "DELETE_CONFIRMATION_REQUIRED"
    assert len(service.gateway.writes) == 2


async def test_latest_delete_preview_invalidates_prior_target(native, service):
    _, _, preview = await deletion(native)
    await invoke(
        native, event("create-other"), "create", {"request_key": "other", "title": "另一个目标"}
    )
    incoming = event("preview-other", text="改为删除另一个目标")
    selected = (await invoke(native, incoming, "query", {"keyword": "另一个目标"}))["tasks"][0][
        "selection_ref"
    ]
    latest = await invoke(
        native, incoming, "delete", {"request_key": "other", "selection_ref": selected}
    )
    assert latest["state"] == "awaiting_confirmation"
    blocked = await invoke(
        native,
        event("confirm", text="确认删除"),
        "delete",
        {
            "request_key": "delete",
            "confirmation_ref": preview["confirmation_ref"],
            "confirm": True,
        },
    )
    assert blocked["error"] == "CONFIRMATION_EXPIRED" and len(service.gateway.writes) == 2


async def test_deleted_history_restore_checks_absence_without_replay(native, service):
    _, _, preview = await deletion(native)
    result = await invoke(
        native,
        event("confirm", text="确认删除"),
        "delete",
        {
            "request_key": "delete",
            "confirmation_ref": preview["confirmation_ref"],
            "confirm": True,
        },
    )
    assert result["state"] == "succeeded"
    (service.root / "restore-review.required").write_text("restore review", encoding="utf-8")
    service.maintenance = "RESTORE_REMOTE_REVIEW_REQUIRED"
    _, revision = await service.db.settings()
    api = PagesAPI(service)
    review = await api.recovery_check(request(revision))
    import json

    history = json.loads((await api.recovery_status(None))["review"]["payload"])["history"]
    assert review["ready"] and {row["verification"] for row in history} == {
        "matches",
        "deleted_by_confirmed_operation",
    }
    assert all(row["current"]["deleted"] for row in history)
    assert len(service.gateway.writes) == 2


async def test_deleted_evidence_and_confirmation_snapshots_follow_retention(native, service):
    _, _, preview = await deletion(native)
    result = await invoke(
        native,
        event("confirm", text="确认删除"),
        "delete",
        {
            "request_key": "delete",
            "confirmation_ref": preview["confirmation_ref"],
            "confirm": True,
        },
    )
    assert result["state"] == "succeeded"
    import time

    # Native groups close only after AstrBot explicitly records its conclusion.
    for group in await service.db.read("SELECT group_id FROM native_group_outcomes"):
        conclusion = await invoke(
            native,
            event("confirm", text="确认删除"),
            "outcome",
            {"group_id": group["group_id"], "state": "completed"},
        )
        assert conclusion["state"] == "completed"
    now = time.time()
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE jobs SET state='done'")
    await service.retention.run(now=now + 31 * DAY)
    ticket = (await service.db.read("SELECT * FROM delete_confirmations"))[0]
    assert ticket["snapshot"] == "{}"
    rows = await service.db.read("SELECT plan FROM operations")
    import json

    assert all(json.loads(row["plan"])["retention_expired"] for row in rows)
    assert len(service.gateway.writes) == 2


async def test_unused_expired_confirmation_drops_body_but_cannot_execute(native, service):
    _, incoming, preview = await deletion(native)
    import time

    await service.retention.run(now=time.time() + 601)
    assert (await service.db.read("SELECT snapshot FROM delete_confirmations"))[0][
        "snapshot"
    ] == "{}"
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE delete_confirmations SET expires_at=0")
    result = await invoke(
        native,
        event("confirm", text="确认删除"),
        "delete",
        {
            "request_key": "delete",
            "confirmation_ref": preview["confirmation_ref"],
            "confirm": True,
        },
    )
    assert result["error"] == "CONFIRMATION_EXPIRED" and len(service.gateway.writes) == 1


async def test_delete_crash_before_remote_id_storage_only_reconciles(native, service):
    _, _, preview = await deletion(native)
    result = await invoke(
        native,
        event("confirm", text="确认删除"),
        "delete",
        {
            "request_key": "delete",
            "confirmation_ref": preview["confirmation_ref"],
            "confirm": True,
        },
    )
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "UPDATE operations SET state='executing',remote_id=NULL,result=NULL WHERE id=:id",
            {"id": result["operation_id"]},
        )
    await service.db.initialize()
    checked = await invoke(
        native, event("check"), "check", {"operation_id": result["operation_id"]}
    )
    assert checked["state"] == "succeeded" and checked["remote_id"] == result["remote_id"]
    assert checked["actual_fields"]["deleted"] and len(service.gateway.writes) == 2


async def test_soft_deleted_detail_cannot_be_used_by_old_selection(native, service):
    created, _, preview = await deletion(native)
    old_detail = dict(service.gateway.items[("p1", created["remote_id"])])
    await invoke(
        native,
        event("confirm", text="确认删除"),
        "delete",
        {
            "request_key": "delete",
            "confirmation_ref": preview["confirmation_ref"],
            "confirm": True,
        },
    )

    async def tombstone_get(project, task):
        return old_detail

    service.gateway.get = tombstone_get
    session = (await service.db.read("SELECT query FROM sessions WHERE id='session'"))[0]
    import json

    selection = json.loads(session["query"])["selection"][0]["selection_ref"]
    attempted = await invoke(
        native,
        event("edit-deleted"),
        "update",
        {
            "request_key": "update",
            "selection_ref": selection,
            "patch": {"title": "不得复活"},
        },
    )
    assert attempted["error"] == "TARGET_DELETED" and len(service.gateway.writes) == 2
