import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from conftest import envelope
from PIL import Image

from notido.cli import CLIResponse
from notido.db import execute
from notido.models import Segment


def event(message="native-1", **kwargs):
    source = envelope(message=message, **kwargs)
    return SimpleNamespace(envelope=source, message_obj=SimpleNamespace(message_id=message))


async def invoke(native, incoming, operation, arguments):
    return json.loads(await native.invoke(incoming, operation, arguments))


async def test_native_create_multiple_actions_and_repeated_call_are_durable(native, service):
    incoming = event()
    args = {
        "request_key": "report",
        "title": "提交报告",
        "requirements": ["PDF格式"],
        "date_text": "2027年12月20日",
        "time_text": "17:40",
    }
    first = await invoke(native, incoming, "create", args)
    second = await invoke(native, incoming, "create", args)
    assert first == second and first["state"] == "succeeded"
    assert first["actual_fields"]["dueDate"].endswith("17:40:00+0800")
    assert "PDF格式" in first["actual_fields"]["content"]
    other = await invoke(native, incoming, "create", {"request_key": "other", "title": "第二项"})
    assert other["state"] == "succeeded" and other["remote_id"] != first["remote_id"]
    assert len(service.gateway.writes) == 2
    assert not service.bridge.calls
    assert not await service.db.read("SELECT * FROM receipt_records")
    conflict = await invoke(native, incoming, "create", {**args, "title": "不同参数"})
    assert conflict["error"] == "REQUEST_CONFLICT" and len(service.gateway.writes) == 2


async def test_rejected_input_reports_safe_correction_but_never_values(native, service):
    incoming = event("bad-input")
    args = {
        "request_key": "r",
        "title": "报告",
        "action_key": "private-value",
        "unexpected-private-key": "private-value",
    }
    result = await invoke(native, incoming, "create", args)
    assert result["input_rejected"] and result["side_effect"] == "none"
    assert result["safe_to_correct_arguments"]
    assert {v["path"] for v in result["invalid_fields"]} == {"action_key", "<field>"}
    assert "private" not in json.dumps(result)
    assert not service.gateway.writes and not await service.db.read("SELECT * FROM operations")
    fixed = await invoke(native, incoming, "create", {"request_key": "r", "title": "报告"})
    assert fixed["state"] == "succeeded" and len(service.gateway.writes) == 1


async def test_native_query_update_complete_use_real_selection_and_preserve_fields(native, service):
    created = await invoke(
        native, event(), "create", {"request_key": "r", "title": "报告", "notes": "用户备注"}
    )
    incoming = event("native-2")
    query = await invoke(native, incoming, "query", {"keyword": "报告"})
    selection = query["tasks"][0]["selection_ref"]
    changed = await invoke(
        native,
        incoming,
        "update",
        {"request_key": "u", "selection_ref": selection, "patch": {"title": "新版报告"}},
    )
    assert changed["state"] == "succeeded" and changed["actual_fields"]["content"] == "用户备注"
    query = await invoke(native, incoming, "query", {"keyword": "新版报告"})
    selection = query["tasks"][0]["selection_ref"]
    completed = await invoke(
        native, incoming, "complete", {"request_key": "c", "selection_ref": selection}
    )
    assert completed["state"] == "succeeded" and completed["remote_id"] == created["remote_id"]
    assert completed["actual_fields"]["status"] == 2 and not service.bridge.calls


async def test_unknown_native_write_can_only_be_checked_and_never_replayed(native, service):
    service.gateway.write_error = CLIResponse(error="CLI_TIMEOUT", side_effect="unknown")
    incoming = event()
    args = {"request_key": "r", "title": "报告"}
    first = await invoke(native, incoming, "create", args)
    assert first["state"] == "outcome_unknown"
    await invoke(native, incoming, "create", args)
    await invoke(native, incoming, "check", {"operation_id": first["operation_id"]})
    assert len(service.gateway.writes) == 1
    alien = await invoke(
        native,
        event("different-session", session="alien"),
        "check",
        {"operation_id": first["operation_id"]},
    )
    assert alien["error"] == "NOT_AUTHORIZED"


async def test_native_authorization_precedes_decode_files_gateway(native, service):
    service.bridge.normalize_event = lambda event: pytest.fail("unbound content decoded")

    async def forbidden():
        pytest.fail("unbound gateway called")

    service.gateway.projects = forbidden
    result = await invoke(native, event(session="alien"), "projects", {})
    assert result["error"] == "NOT_AUTHORIZED"
    assert not await service.db.read("SELECT * FROM message_records")


async def test_native_materials_deliver_real_images_with_no_model_and_track_unread(
    native, service, tmp_path
):
    original = tmp_path / "original.jpg"
    Image.new("RGB", (24, 16), "red").save(original)
    incoming = event()
    incoming.envelope.segments = [Segment(source_id="picture", kind="image")]

    async def acquire(source):
        assert source == "picture"
        return str(original), "original.jpg"

    service.bridge.acquire_material = acquire
    result = await native.invoke(incoming, "materials", {})
    manifest = json.loads(result.content[0].text)
    assert manifest["complete"] and manifest["assets"][0]["state"] == "ready"
    assert result.content[1].type == "image" and result.content[1].mimeType == "image/png"
    assert base64.b64decode(result.content[1].data).startswith(b"\x89PNG")
    assert not service.bridge.calls
    assert len(await service.db.read("SELECT * FROM native_material_deliveries")) == 1
    assert not await service.db.read("SELECT * FROM jobs WHERE kind='parse_group'")
    image_item = manifest["items"][0]
    assert image_item["evidence_parameter"] == "visual_evidence"
    reference = image_item["evidence_reference"]
    assert reference == {"source_id": "picture", "location": image_item["location"]}
    args = {
        "request_key": "image",
        "title": "图片事项",
        "notice": {"group_id": manifest["group_id"], "action_key": "image"},
        "evidence": [{**reference, "quote": "红色图片"}],
    }
    rejected = await invoke(native, incoming, "create", args)
    assert rejected["input_rejected"] and rejected["side_effect"] == "none"
    assert reference in rejected["visual_evidence_choices"]
    assert not service.gateway.writes
    corrected = await invoke(
        native, incoming, "create", {**args, "evidence": [], "visual_evidence": args["evidence"]}
    )
    assert corrected["state"] == "succeeded" and len(service.gateway.writes) == 1
    plan = json.loads((await service.db.read("SELECT plan FROM operations"))[0]["plan"])
    assert plan["visual_evidence"][0]["verification"] == "astrbot_visual_interpretation"
    # A missing derived preview can be regenerated from immutable saved original bytes.
    cached = json.loads(
        (await service.db.read("SELECT payload FROM native_material_reads"))[0]["payload"]
    )
    from pathlib import Path

    await asyncio.to_thread(Path(cached["items"][0]["image_path"]).unlink)
    rebuilt = await native.invoke(
        event("after-preview-expired"), "materials", {"group_id": manifest["group_id"]}
    )
    rebuilt_manifest = json.loads(rebuilt.content[0].text)
    assert rebuilt_manifest["complete"] and rebuilt.content[1].type == "image"
    assert rebuilt_manifest["assets"][0]["hash"] == manifest["assets"][0]["hash"]
    assert not service.bridge.calls


async def test_native_invalid_arguments_date_and_evidence_do_not_write(native, service):
    for args in [
        {"request_key": "r", "title": "报告", "priority": 2},
        {"request_key": "r", "title": "报告", "date_text": "明天", "time_text": "不确定"},
        {
            "request_key": "r",
            "title": "报告",
            "evidence": [{"source_id": "invented", "location": "message", "quote": "编造"}],
        },
    ]:
        result = await invoke(native, event(), "create", args)
        assert result["state"] == "blocked"
    assert not service.gateway.writes


@pytest.mark.parametrize(
    "patch",
    [
        {"all_day": False},
        {"date_text": "2027-12-25", "all_day": False},
        {"date_text": "2027-12-25", "time_text": "17:40", "all_day": True},
        {"date_text": None, "time_text": "17:40"},
        {"date_text": None, "all_day": True},
    ],
)
async def test_native_update_never_silently_drops_declared_time_or_all_day(native, service, patch):
    created = await invoke(
        native,
        event("dated"),
        "create",
        {
            "request_key": "r",
            "title": "全天报告",
            "date_text": "2027-12-24",
            "all_day": True,
        },
    )
    incoming = event("change-time")
    selected = (await invoke(native, incoming, "query", {"keyword": "全天报告"}))["tasks"][0][
        "selection_ref"
    ]
    refused = await invoke(
        native,
        incoming,
        "update",
        {
            "request_key": "u",
            "selection_ref": selected,
            "patch": patch,
        },
    )
    assert refused["error"] in ("DATE_UNRESOLVED", "DATE_CONTRADICTION")
    assert (
        len(service.gateway.writes) == 1
        and len(await service.db.read("SELECT * FROM operations")) == 1
    )
    assert service.gateway.items[("p1", created["remote_id"])]["isAllDay"] is True
    corrected = await invoke(
        native,
        incoming,
        "update",
        {
            "request_key": "u",
            "selection_ref": selected,
            "patch": {"date_text": "2027-12-25", "time_text": "17:40", "all_day": False},
        },
    )
    assert corrected["state"] == "succeeded" and corrected["actual_fields"]["isAllDay"] is False
    assert corrected["actual_fields"]["dueDate"].startswith("2027-12-25T17:40")
    assert corrected["remote_id"] == created["remote_id"] and len(service.gateway.writes) == 2


async def test_native_date_only_change_preserves_existing_time_and_explicit_all_day_conversion(
    native, service
):
    created = await invoke(
        native,
        event("timed"),
        "create",
        {
            "request_key": "r",
            "title": "有时刻报告",
            "date_text": "2027-12-24",
            "time_text": "17:40",
        },
    )
    incoming = event("change-date")
    selected = (await invoke(native, incoming, "query", {"keyword": "有时刻报告"}))["tasks"][0][
        "selection_ref"
    ]
    changed = await invoke(
        native,
        incoming,
        "update",
        {
            "request_key": "u",
            "selection_ref": selected,
            "patch": {"date_text": "2027-12-25", "all_day": False},
        },
    )
    assert changed["state"] == "succeeded" and changed["actual_fields"]["dueDate"].startswith(
        "2027-12-25T17:40"
    )
    assert changed["actual_fields"].get("isAllDay", False) is False
    selected = (await invoke(native, incoming, "query", {"keyword": "有时刻报告"}))["tasks"][0][
        "selection_ref"
    ]
    converted = await invoke(
        native,
        incoming,
        "update",
        {
            "request_key": "all-day",
            "selection_ref": selected,
            "patch": {"all_day": True},
        },
    )
    assert converted["state"] == "succeeded" and converted["actual_fields"]["isAllDay"] is True
    assert converted["remote_id"] == created["remote_id"]


async def test_native_create_rejects_explicit_all_day_and_time_contradiction(native, service):
    result = await invoke(
        native,
        event(),
        "create",
        {
            "request_key": "r",
            "title": "报告",
            "date_text": "2027-12-24",
            "time_text": "17:40",
            "all_day": True,
        },
    )
    assert result["error"] == "DATE_CONTRADICTION"
    assert not service.gateway.writes and not await service.db.read("SELECT * FROM operations")


@pytest.mark.parametrize(
    "deadline", ["2027年12月24日24:00截止交报告。", "2027-12-24 17:40到期交报告"]
)
async def test_declared_timed_deadline_cannot_be_reduced_to_all_day(native, service, deadline):
    result = await invoke(
        native,
        event(),
        "create",
        {
            "request_key": "r",
            "title": "报告",
            "date_text": "2027-12-24",
            "all_day": True,
            "requirements": [deadline],
        },
    )
    assert result["error"] == "DATE_CONTRADICTION"
    assert not service.gateway.writes and not await service.db.read("SELECT * FROM operations")
    corrected = await invoke(
        native,
        event("corrected"),
        "create",
        {
            "request_key": "r",
            "title": "报告",
            "date_text": "2027-12-24",
            "time_text": "24:00" if "24:00" in deadline else "17:40",
            "all_day": False,
            "requirements": [deadline],
        },
    )
    assert corrected["state"] == "succeeded" and not corrected["actual_fields"].get(
        "isAllDay", False
    )
    assert corrected["actual_fields"]["dueDate"].startswith(
        "2027-12-25T00:00" if "24:00" in deadline else "2027-12-24T17:40"
    )


async def test_date_precision_guard_does_not_treat_filenames_or_old_deadlines_as_current(native):
    result = await invoke(
        native,
        event(),
        "create",
        {
            "request_key": "r",
            "title": "报告",
            "date_text": "2027-12-24",
            "all_day": True,
            "requirements": ["文件名24:00", "2027年12月23日24:00截止（原期限，已延期）"],
        },
    )
    assert result["state"] == "succeeded" and result["actual_fields"]["isAllDay"]


async def test_native_provider_and_identity_settings_are_not_exposed(native, service):
    from notido.api import PagesAPI

    data = await PagesAPI(service).settings(None)
    assert "provider_id" not in data["settings"] and "identity" not in data["settings"]


async def test_native_scope_and_identity_memory_not_manual_profile(native, service):
    args = {"request_key": "r", "title": "报告"}
    first = await invoke(native, event(), "create", args)
    settings, _ = await service.db.settings()
    settings.identity.school = "已废弃的旧配置"
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE settings SET payload=:p", {"p": settings.model_dump_json()})
    checked = await invoke(native, event(), "check", {"operation_id": first["operation_id"]})
    assert checked["state"] == "succeeded"
    blocked = await invoke(native, event("native-3"), "create", {**args, "project_name": "不存在"})
    assert blocked["error"] == "PROJECT_AMBIGUOUS" and len(service.gateway.writes) == 1


async def test_native_external_edit_after_query_requires_new_selection(native, service):
    created = await invoke(native, event(), "create", {"request_key": "r", "title": "报告"})
    incoming = event("new")
    selected = (await invoke(native, incoming, "query", {}))["tasks"][0]["selection_ref"]
    service.gateway.items[("p1", created["remote_id"])]["title"] = "外部改名"
    blocked = await invoke(
        native, incoming, "complete", {"request_key": "c", "selection_ref": selected}
    )
    assert blocked["error"] == "TARGET_CHANGED" and len(service.gateway.writes) == 1


async def test_native_account_change_does_not_expose_or_replay_previous_result(native, service):
    incoming = event()
    args = {"request_key": "r", "title": "报告"}
    first = await invoke(native, incoming, "create", args)
    settings, _ = await service.db.settings()
    settings.account_ref = "account-b"
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "INSERT INTO account_scopes VALUES ('account-b','personal','cn',NULL,1,'paused',0)",
        )
        await execute(conn, "UPDATE settings SET payload=:p", {"p": settings.model_dump_json()})
    blocked = await invoke(native, incoming, "create", args)
    assert blocked["error"] == "ACCOUNT_CHANGED" and "remote_id" not in blocked
    assert len(service.gateway.writes) == 1 and first["state"] == "succeeded"


async def test_native_query_scope_paging_refresh_and_expired_selection(native, service):
    for index in range(13):
        service.gateway.items[("p1", str(index))] = {
            "id": str(index),
            "projectId": "p1",
            "title": f"报告{index}",
            "status": 0,
        }
    service.gateway.items[("p2", "other")] = {
        "id": "other",
        "projectId": "p2",
        "title": "生活事项",
        "status": 0,
    }
    incoming = event()
    first = await invoke(native, incoming, "query", {"project_name": "学习"})
    assert first["total_count"] == 13 and first["returned_count"] == 10
    second = await invoke(
        native, incoming, "query", {"project_name": "学习", "offset": first["next_offset"]}
    )
    assert second["returned_count"] == 3 and not second["refreshed"]
    assert (
        await invoke(
            native,
            incoming,
            "complete",
            {"request_key": "c", "selection_ref": first["tasks"][0]["selection_ref"]},
        )
    )["error"] == "SELECTION_EXPIRED"
    service.gateway.items[("p1", "new")] = {
        "id": "new",
        "projectId": "p1",
        "title": "新出现的报告",
        "status": 0,
    }
    refreshed = await invoke(native, incoming, "query", {"project_name": "学习", "offset": 10})
    assert refreshed["refreshed"] and refreshed["returned_count"] == 10


async def test_native_material_paging_tracks_actual_deliveries_and_rejects_unread_quotes(
    native, service
):
    incoming = event()
    text = "A" * 49000 + "结尾重要要求"
    incoming.envelope.segments[0].text = text
    result = await native.invoke(incoming, "materials", {})
    manifest = json.loads(result.content[0].text)
    assert not manifest["complete"] and manifest["next_offset"] == 4
    source = manifest["items"][0]
    args = {
        "request_key": "r",
        "title": "报告",
        "evidence": [
            {
                "source_id": source["source_id"],
                "location": source["location"],
                "quote": "结尾重要要求",
            }
        ],
    }
    assert (await invoke(native, incoming, "create", args))["error"] == "EVIDENCE_INVALID"
    final = await native.invoke(
        event("later-message"), "materials", {"group_id": manifest["group_id"], "offset": 4}
    )
    assert json.loads(final.content[0].text)["complete"]
    assert (await invoke(native, incoming, "create", args))["state"] == "succeeded"


async def test_native_material_group_reference_cannot_cross_sessions(native, service):
    manifest = json.loads((await native.invoke(event(), "materials", {})).content[0].text)
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "INSERT INTO actor_bindings VALUES ('other','personal','test-instance','actor','other',1,0)",
        )
    result = await invoke(
        native, event("other-msg", session="other"), "materials", {"group_id": manifest["group_id"]}
    )
    assert result["error"] == "MATERIAL_NOT_FOUND"


async def test_native_material_block_budget_reports_omitted_ranges(native, service):
    settings, _ = await service.db.settings()
    settings.materials.block_characters = 1000
    settings.materials.block_overlap = 100
    settings.materials.blocks = 2
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE settings SET payload=:p", {"p": settings.model_dump_json()})
    incoming = event(text="原文" * 2000)
    result = await native.invoke(incoming, "materials", {})
    manifest = json.loads(result.content[0].text)
    assert len(manifest["items"]) == 2 and not manifest["complete"]
    assert manifest["items"][1]["start"] == 900
    assert manifest["unknowns"][0]["start"] == 1800
    assert manifest["unknowns"][0]["end"] == 4000
    assert manifest["unknowns"][0]["reason"] == "TEXT_BLOCK_LIMIT"


async def test_native_message_action_count_is_bounded(native, service):
    incoming = event()
    for index in range(10):
        assert (
            await invoke(
                native, incoming, "create", {"request_key": str(index), "title": f"行动{index}"}
            )
        )["state"] == "succeeded"
    result = await invoke(native, incoming, "create", {"request_key": "extra", "title": "超限行动"})
    assert result["error"] == "ACTION_LIMIT" and len(service.gateway.writes) == 10


async def test_native_overdue_needs_explicit_confirm_and_keeps_original_date(native, service):
    args = {"request_key": "r", "title": "补记旧报告", "date_text": "2020年1月2日"}
    assert (await invoke(native, event(), "create", args))["error"] == "OVERDUE_CONFIRMATION"
    result = await invoke(native, event(), "create", {**args, "allow_overdue": True})
    assert result["state"] == "succeeded" and result["actual_fields"]["dueDate"].startswith(
        "2020-01-02"
    )


async def test_native_shutdown_waits_for_active_write_and_rejects_new_calls(native, service):
    started, release = asyncio.Event(), asyncio.Event()
    original = service.gateway.write

    async def write(*args, **kwargs):
        started.set()
        await release.wait()
        return await original(*args, **kwargs)

    service.gateway.write = write
    call = asyncio.create_task(
        invoke(native, event(), "create", {"request_key": "r", "title": "报告"})
    )
    await asyncio.wait_for(started.wait(), 3)
    stop = asyncio.create_task(service.stop())
    await asyncio.sleep(0)
    assert service.stopping and not stop.done()
    assert (await invoke(native, event("later"), "projects", {}))["error"] == "MAINTENANCE"
    release.set()
    assert (await call)["state"] == "succeeded"
    await stop
    assert len(service.gateway.writes) == 1
