import json

import pytest
from conftest import create_plan, envelope, operation, parsed
from test_queue_dependencies import add_asset

from notido.db import execute


async def saved_target(service):
    service.bridge.plan = create_plan
    await parsed(service)
    first = await operation(service)
    await service.execute_operation({"operation_id": first["id"]})
    task = (await operation(service))["remote_id"]
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "INSERT INTO actor_bindings VALUES ('second-binding','personal','test-instance','actor','second',1,0)",
        )
    return task


async def supplement(service, task, *, message="late-1", data=b"late bytes"):
    event = envelope(
        message, text=f"补充任务 {task}", source_kind="manual_notice", session="second"
    )
    result = await service.intake(event)
    asset = await add_asset(service, result["group_id"], data, "late.zip")
    return event, result, asset


async def test_cross_session_explicit_supplement_uses_original_task_without_model(service):
    task = await saved_target(service)
    event, result, asset = await supplement(service, task)
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "UPDATE sessions SET question=:q,question_expires=9999999999 WHERE id='session'",
            {
                "q": json.dumps(
                    {
                        "question_ref": "old-session-question",
                        "group_id": "old",
                        "questions": ["请选择"],
                        "context": {},
                        "yes_no": False,
                    }
                )
            },
        )
    await service.read_materials({"group_id": result["group_id"]})
    upload = (await service.db.read("SELECT * FROM operations WHERE kind='upload'"))[0]
    plan = json.loads(upload["plan"])
    assert plan["task_id"] == task and plan["asset_id"] == asset
    assert plan["session_key"] == "second" and plan["origin"] == event.reply_origin_ref
    assert len(service.gateway.items) == 1 and len(service.bridge.calls) == 1
    assert (
        "old-session-question"
        in (await service.db.read("SELECT question FROM sessions WHERE id='session'"))[0][
            "question"
        ]
    )
    assert (await service.intake(event))["duplicate"]
    _, another, _ = await supplement(service, task, message="late-duplicate")
    await service.read_materials({"group_id": another["group_id"]})
    assert len(await service.db.read("SELECT * FROM operations WHERE kind='upload'")) == 1
    assert len(service.gateway.items) == 1 and len(service.bridge.calls) == 1


@pytest.mark.parametrize(
    "change", ["account", "config", "revoked", "completed", "title", "unknown"]
)
async def test_supplement_context_changes_keep_original_and_stop_new_uploads(service, change):
    task = await saved_target(service)
    _, result, _ = await supplement(service, task if change != "unknown" else "unknown-task")
    if change in ("account", "config", "revoked"):
        async with service.db.transaction() as conn:
            if change == "revoked":
                await execute(conn, "UPDATE actor_bindings SET enabled=0 WHERE id='second-binding'")
            elif change == "config":
                await execute(conn, "UPDATE settings SET revision=revision+1")
            else:
                await execute(
                    conn,
                    "UPDATE settings SET payload=json_set(payload,'$.credential_generation',2)",
                )
    if change == "completed":
        service.gateway.items[("p1", task)]["status"] = 2
    if change == "title":
        service.gateway.items[("p1", task)]["title"] = "external change"
    await service.read_materials({"group_id": result["group_id"]})
    assert not await service.db.read("SELECT * FROM operations WHERE kind='upload'")
    assets = await service.db.read(
        "SELECT * FROM assets WHERE group_id=:g", {"g": result["group_id"]}
    )
    assert (
        assets[0]["state"] == "ready" and service.blobs.path(f"blobs/{assets[0]['hash']}").exists()
    )
    assert len(service.bridge.calls) == 1 and len(service.gateway.writes) == 1
    question = (await service.db.read("SELECT question FROM sessions WHERE id='second'"))[0][
        "question"
    ]
    assert "SUPPLEMENT_" in question


async def test_forwarded_supplement_words_cannot_bind_target(service):
    task = await saved_target(service)
    value = await service.intake(
        envelope("forward", text=f"补充任务 {task}", source_kind="user_forward", session="second")
    )
    assert not await service.db.read(
        "SELECT * FROM material_task_targets WHERE group_id=:g", {"g": value["group_id"]}
    )


async def test_target_edit_after_scheduling_stops_upload_before_claim(service):
    task = await saved_target(service)
    _, result, _ = await supplement(service, task)
    await service.read_materials({"group_id": result["group_id"]})
    upload = (await service.db.read("SELECT * FROM operations WHERE kind='upload'"))[0]
    service.gateway.items[("p1", task)]["title"] = "external change"
    await service.execute_operation({"operation_id": upload["id"]})
    actual = (await service.db.read("SELECT * FROM operations WHERE kind='upload'"))[0]
    assert actual["state"] == "validated" and actual["paused"] and actual["attempt"] == 0
    assert len(service.gateway.writes) == 1
