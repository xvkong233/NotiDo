import json
from datetime import UTC, datetime

import pytest
from test_native_tools import event, invoke


async def material(native, message, text, published=None):
    incoming = event(message, text=text, source_kind="user_forward")
    incoming.envelope.segments[0].source_id = f"source-{message}"
    incoming.envelope.segments[0].published_at = published
    result = await native.invoke(incoming, "materials", {})
    data = json.loads(result.content[0].text)
    source = data["items"][0]
    evidence = {"source_id": source["source_id"], "location": source["location"], "quote": text}
    return incoming, data, evidence


async def initial(native):
    incoming, manifest, evidence = await material(
        native, "initial", "2027年12月20日提交报告，PDF格式", datetime(2026, 10, 7, tzinfo=UTC)
    )
    args = {
        "request_key": "report",
        "title": "提交报告",
        "notes": "用户原有备注",
        "requirements": ["PDF格式"],
        "date_text": "2027年12月20日",
        "evidence": [evidence],
        "notice": {"group_id": manifest["group_id"], "action_key": "report"},
    }
    result = await invoke(native, incoming, "create", args)
    assert result["state"] == "succeeded"
    return result, args


@pytest.mark.parametrize("template", ["学号姓名", "学号_姓名.pdf"])
async def test_filename_template_is_preserved_and_safe_correction_keeps_key(
    native, service, template
):
    incoming, manifest, evidence = await material(
        native, "filename", f"本人须交报告，文件名为「{template}」，PDF格式。"
    )
    args = {
        "request_key": "filename",
        "title": "交报告",
        "evidence": [evidence],
        "notice": {"group_id": manifest["group_id"], "action_key": "report"},
        "requirements": ["文件名：学号-姓名.pdf"],
    }
    rejected = await invoke(native, incoming, "create", args)
    assert rejected["error"] == "FILENAME_REQUIREMENT_CHANGED"
    assert rejected["side_effect"] == "none" and rejected["safe_to_correct_arguments"]
    assert not service.gateway.writes and not await service.db.read("SELECT * FROM operations")
    result = await invoke(
        native, incoming, "create", {**args, "requirements": [f"文件名：{template}", "PDF格式"]}
    )
    assert result["state"] == "succeeded" and len(service.gateway.writes) == 1
    assert f"文件名：{template}" in result["actual_fields"]["content"]


async def test_declared_unknown_original_time_cannot_be_preconverted_to_absolute(native, service):
    incoming, manifest, evidence = await material(native, "unknown-original", "班委明天17:00交材料")
    result = await invoke(
        native,
        incoming,
        "create",
        {
            "request_key": "r",
            "title": "交材料",
            "date_text": "2027-12-21",
            "time_text": "17:00",
            "requirements": ["原发布时间未知"],
            "evidence": [evidence],
            "notice": {"group_id": manifest["group_id"], "action_key": "r"},
        },
    )
    assert result["error"] == "TIME_ANCHOR_UNKNOWN"
    assert not service.gateway.writes and not await service.db.read("SELECT * FROM operations")


async def test_unknown_original_time_does_not_block_independent_absolute_deadline(native):
    incoming, manifest, evidence = await material(
        native, "absolute-original", "2027年12月21日17:00交材料"
    )
    result = await invoke(
        native,
        incoming,
        "create",
        {
            "request_key": "r",
            "title": "交材料",
            "date_text": "2027-12-21",
            "time_text": "17:00",
            "notes": "原发布时间未知",
            "evidence": [evidence],
            "notice": {"group_id": manifest["group_id"], "action_key": "r"},
        },
    )
    assert result["state"] == "succeeded"


async def revision(native, text="提交报告延期至2027年12月21日", published=None):
    incoming, manifest, evidence = await material(native, "revision", text, published)
    selected = (await invoke(native, incoming, "query", {"keyword": "提交报告"}))["tasks"][0]
    args = {
        "request_key": "revision",
        "selection_ref": selected["selection_ref"],
        "patch": {"date_text": "2027年12月21日"},
        "notice": {"group_id": manifest["group_id"], "evidence": [evidence]},
    }
    return incoming, args


async def test_native_duplicate_notice_reads_original_without_creating_again(native, service):
    first, args = await initial(native)
    task = service.gateway.items[("p1", first["remote_id"])]
    task["content"] += "\n用户新增备注"
    incoming, manifest, evidence = await material(
        native, "duplicate", "2027年12月20日提交报告，PDF格式", datetime(2026, 10, 7, tzinfo=UTC)
    )
    assert manifest["known_actions"][0]["action_key"] == "report"
    second = await invoke(
        native,
        incoming,
        "create",
        {
            **args,
            "evidence": [evidence],
            "notice": {"group_id": manifest["group_id"], "action_key": "report"},
        },
    )
    assert second["state"] == "succeeded" and second["reused_existing"]
    assert second["remote_id"] == first["remote_id"] and len(service.gateway.writes) == 1
    assert second["actual_fields"]["content"].endswith("用户新增备注")
    assert len(await service.db.read("SELECT * FROM native_tool_calls")) == 2
    changed_key = await invoke(
        native,
        event("new-key"),
        "create",
        {
            **args,
            "request_key": "another",
            "notice": {"group_id": manifest["group_id"], "action_key": "different"},
            "evidence": [evidence],
        },
    )
    assert changed_key["error"] == "NOTICE_ACTION_KEY_CHANGED" and len(service.gateway.writes) == 1


async def test_native_notice_relative_date_uses_original_material_not_followup_clock(native):
    incoming, manifest, evidence = await material(
        native, "source", "明天提交报告", datetime(2027, 12, 20, tzinfo=UTC)
    )
    later = event("followup")
    result = await invoke(
        native,
        later,
        "create",
        {
            "request_key": "report",
            "title": "提交报告",
            "date_text": "明天",
            "evidence": [evidence],
            "notice": {"group_id": manifest["group_id"], "action_key": "report"},
        },
    )
    assert result["state"] == "succeeded" and result["actual_fields"]["dueDate"].startswith(
        "2027-12-21"
    )


async def test_precision_check_uses_original_anchor_before_rejecting_downgrade(native, service):
    incoming, manifest, evidence = await material(
        native,
        "timed-source",
        "原通知2027年12月20日发布，明天24:00截止交材料",
        datetime(2027, 12, 20, tzinfo=UTC),
    )
    result = await invoke(
        native,
        event("later"),
        "create",
        {
            "request_key": "r",
            "title": "交材料",
            "date_text": "明天",
            "all_day": True,
            "requirements": ["2027年12月21日24:00截止交材料"],
            "evidence": [evidence],
            "notice": {"group_id": manifest["group_id"], "action_key": "materials"},
        },
    )
    assert result["error"] == "DATE_CONTRADICTION"
    assert not service.gateway.writes and not await service.db.read("SELECT * FROM operations")


async def test_native_notice_postponement_preserves_notes_and_action_versions(native, service):
    first, _ = await initial(native)
    task = service.gateway.items[("p1", first["remote_id"])]
    task["content"] += "\n用户追加文字\n![file](attachment/original.pdf)"
    incoming, args = await revision(native, published=datetime(2026, 10, 8, tzinfo=UTC))
    result = await invoke(native, incoming, "update", args)
    assert result["state"] == "succeeded" and result["remote_id"] == first["remote_id"]
    assert task["dueDate"].startswith("2027-12-21") and task["content"].startswith("用户原有备注")
    assert task["content"].endswith("用户追加文字\n![file](attachment/original.pdf)")
    assert "PDF格式" in task["content"] and "2027年12月21日" in task["content"]
    operations = await service.db.read("SELECT action_id FROM operations ORDER BY created_at")
    assert operations[0]["action_id"] == operations[1]["action_id"]
    assert len(await service.db.read("SELECT * FROM action_item_versions")) == 2
    assert len(await service.db.read("SELECT * FROM notice_versions")) == 2
    await invoke(native, incoming, "update", args)
    assert len(service.gateway.writes) == 2


@pytest.mark.parametrize(
    "published,error",
    [
        (None, "SOURCE_ORDER_UNKNOWN"),
        (datetime(2026, 10, 6, tzinfo=UTC), "SOURCE_ORDER_CONFLICT"),
    ],
)
async def test_native_notice_source_order_blocks_until_explicit_confirmation(
    native, service, published, error
):
    await initial(native)
    incoming, args = await revision(native, published=published)
    assert (await invoke(native, incoming, "update", args))["error"] == error
    assert len(service.gateway.writes) == 1
    args["notice"]["source_order_confirmed"] = True
    assert (await invoke(native, incoming, "update", args))["state"] == "succeeded"


@pytest.mark.parametrize(
    "change,error",
    [("notes", "NOTES_CONFLICT"), ("date", "TARGET_CHANGED"), ("complete", "TARGET_CHANGED")],
)
async def test_native_notice_external_changes_are_not_overwritten(native, service, change, error):
    first, _ = await initial(native)
    task = service.gateway.items[("p1", first["remote_id"])]
    if change == "notes":
        task["content"] = task["content"].replace("PDF格式", "用户改变格式")
    elif change == "date":
        task["dueDate"] = "2027-12-29T00:00:00+0800"
    else:
        task["status"] = 2
    if change == "complete":
        task["status"] = 0
    incoming, args = await revision(native, published=datetime(2026, 10, 8, tzinfo=UTC))
    if change == "complete":
        task["status"] = 2
    assert (await invoke(native, incoming, "update", args))["error"] == error
    assert len(service.gateway.writes) == 1
