import json
from datetime import UTC, datetime

import pytest
from test_native_notices import initial, material
from test_native_tools import event, invoke

from notido.db import execute


async def reprovide(native, service):
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "INSERT INTO actor_bindings VALUES ('reprovided','personal','test-instance','actor','new-session',1,0)",
        )
    incoming = event(
        "reprovided",
        text="2027年12月20日提交报告，PDF格式",
        source_kind="user_forward",
        session="new-session",
    )
    incoming.envelope.segments[0].published_at = datetime(2026, 10, 7, tzinfo=UTC)
    manifest = json.loads((await native.invoke(incoming, "materials", {})).content[0].text)
    return incoming, manifest


async def pending(native, incoming):
    return await invoke(
        native,
        incoming,
        "outcome",
        {
            "state": "awaiting_materials",
            "pending_reason": "另一项缺少附件",
        },
    )


async def test_reprovided_notice_create_reuse_counts_saved_work_in_current_session(native, service):
    original, args = await initial(native)
    incoming, manifest = await reprovide(native, service)
    item = manifest["items"][0]
    args = {
        **args,
        "evidence": [
            {"source_id": item["source_id"], "location": item["location"], "quote": item["text"]}
        ],
        "notice": {"group_id": manifest["group_id"], "action_key": "report"},
    }
    reused = await invoke(native, incoming, "create", args)
    assert reused["operation_id"] == original["operation_id"] and reused["reused_existing"]
    assert reused["related_material_group_ids"] == []
    assert (await pending(native, incoming))["state"] == "partially_done"
    assert len(service.gateway.writes) == 1 and not service.bridge.calls


async def test_query_reprovided_notice_records_verified_source_without_writing(native, service):
    original, _ = await initial(native)
    incoming, _ = await reprovide(native, service)
    assert (await pending(native, incoming))["state"] == "awaiting_materials"
    # An unrelated user note does not invalidate the managed notice region.
    service.gateway.items[("p1", original["remote_id"])]["content"] += "\n用户备注"
    queried = await invoke(native, incoming, "query", {"keyword": "提交报告"})
    assert queried["notice_readbacks"] == [
        {
            "operation_id": original["operation_id"],
            "task_id": original["remote_id"],
            "project_id": "p1",
            "verified": True,
            "time_kind": "deadline",
        }
    ]
    assert (await pending(native, incoming))["state"] == "partially_done"
    assert len(service.gateway.writes) == 1 and not service.bridge.calls


async def test_same_session_related_material_remains_available(native, service):
    original, args = await initial(native)
    incoming, manifest, evidence = await material(
        native,
        "same-session-repeat",
        "2027年12月20日提交报告，PDF格式",
        datetime(2026, 10, 7, tzinfo=UTC),
    )
    reused = await invoke(
        native,
        incoming,
        "create",
        {
            **args,
            "evidence": [evidence],
            "notice": {"group_id": manifest["group_id"], "action_key": "report"},
        },
    )
    assert reused["related_material_group_ids"] == [original["material_group_id"]]
    checked = await invoke(native, incoming, "check", {"operation_id": original["operation_id"]})
    assert checked["related_material_group_ids"] == reused["related_material_group_ids"]
    assert len(service.gateway.writes) == 1


async def test_exact_query_reads_source_target_beyond_keyword_first_page(native, service):
    original, _ = await initial(native)
    incoming, _ = await reprovide(native, service)
    actual = service.gateway.items[("p1", original["remote_id"])]
    for index in range(12):
        duplicate = {**actual, "id": f"older-{index}", "dueDate": "2027-12-19T00:00:00+0800"}
        service.gateway.items[("p1", duplicate["id"])] = duplicate
    first = await invoke(native, incoming, "query", {"keyword": "提交报告"})
    assert first["has_more"] and len(first["tasks"]) == 10
    assert first["notice_readbacks"] == []
    assert (await pending(native, incoming))["state"] == "awaiting_materials"
    exact = await invoke(native, incoming, "query", {"task_id": original["remote_id"]})
    assert [task["id"] for task in exact["tasks"]] == [original["remote_id"]]
    assert not exact["has_more"] and exact["total_count"] == 1
    assert exact["notice_readbacks"][0]["operation_id"] == original["operation_id"]
    assert (await pending(native, incoming))["state"] == "partially_done"
    assert len(service.gateway.writes) == 1


async def test_exact_query_cannot_expand_allowed_scope_or_include_completed(native, service):
    original, _ = await initial(native)
    incoming, _ = await reprovide(native, service)
    actual = service.gateway.items[("p1", original["remote_id"])]
    service.gateway.items[("alien", "foreign")] = {**actual, "id": "foreign", "projectId": "alien"}
    foreign = await invoke(native, incoming, "query", {"task_id": "foreign"})
    assert foreign["tasks"] == [] and foreign["notice_readbacks"] == []
    actual["status"] = 2
    completed = await invoke(native, incoming, "query", {"task_id": original["remote_id"]})
    assert completed["tasks"] == [] and completed["notice_readbacks"] == []
    assert (await pending(native, incoming))["state"] == "awaiting_materials"
    assert len(service.gateway.writes) == 1


@pytest.mark.parametrize(
    "problem",
    [
        "same_title",
        "unknown",
        "unverified",
        "date_changed",
        "region_changed",
        "not_returned",
        "other_actor",
        "other_account",
        "not_read",
    ],
)
async def test_query_cannot_credit_unrelated_unverified_or_unread_notice(native, service, problem):
    original, _ = await initial(native)
    incoming, manifest = await reprovide(native, service)
    actual = service.gateway.items[("p1", original["remote_id"])]
    keyword = "提交报告"
    async with service.db.transaction() as conn:
        if problem == "same_title":
            del service.gateway.items[("p1", original["remote_id"])]
            service.gateway.items[("p1", "unrelated")] = {**actual, "id": "unrelated"}
        elif problem == "unknown":
            await execute(
                conn,
                "UPDATE operations SET state='outcome_unknown' WHERE id=:id",
                {"id": original["operation_id"]},
            )
        elif problem == "unverified":
            await execute(
                conn,
                "UPDATE operations SET result='{}' WHERE id=:id",
                {"id": original["operation_id"]},
            )
        elif problem == "date_changed":
            actual["dueDate"] = "2027-12-21T00:00:00+0800"
        elif problem == "region_changed":
            actual["content"] = actual["content"].replace("PDF格式", "Word格式")
        elif problem == "not_returned":
            keyword = "不匹配的另一项"
        elif problem in ("other_actor", "other_account"):
            if problem == "other_account":
                await execute(
                    conn,
                    "INSERT INTO account_scopes VALUES ('alien-account','personal','cn',NULL,1,'retired',0)",
                )
            row = await service.db.read(
                "SELECT plan FROM operations WHERE id=:id", {"id": original["operation_id"]}
            )
            plan = json.loads(row[0]["plan"])
            plan["actor_key"] = "alien" if problem == "other_actor" else plan["actor_key"]
            await execute(
                conn,
                "UPDATE operations SET plan=:p,account_ref=:a WHERE id=:id",
                {
                    "id": original["operation_id"],
                    "p": json.dumps(plan),
                    "a": "alien-account" if problem == "other_account" else "account-a",
                },
            )
        elif problem == "not_read":
            await execute(
                conn,
                "DELETE FROM native_material_deliveries WHERE group_id=:g",
                {"g": manifest["group_id"]},
            )
    queried = await invoke(native, incoming, "query", {"keyword": keyword})
    assert queried["notice_readbacks"] == []
    assert (await pending(native, incoming))["state"] == "awaiting_materials"
    assert len(service.gateway.writes) == 1 and not service.bridge.calls
