import json
from types import SimpleNamespace

import pytest
from conftest import create_plan, envelope, parsed

from notido.api import PagesAPI
from notido.db import execute
from notido.errors import NotiDoError


async def question(service):
    return json.loads(
        (await service.db.read("SELECT question FROM sessions WHERE id='session'"))[0]["question"]
    )


async def test_independent_new_request_completes_without_filling_old_question(service):
    service.bridge.plan = lambda p: create_plan(p, ambiguities=["请选择报告格式"])
    old = await parsed(service)
    old_question = await question(service)
    service.bridge.plan = lambda p: create_plan(p, date_text=None, title="买牛奶")
    new = await parsed(service, message="independent", text="记一下买牛奶")
    assert service.bridge.calls[-1]["question"] is None
    op = (await service.db.read("SELECT * FROM operations"))[0]
    await service.execute_operation({"operation_id": op["id"]})
    assert await question(service) == old_question
    groups = {x["id"]: x["state"] for x in await service.db.read("SELECT * FROM material_groups")}
    assert groups[old["group_id"]] == "awaiting_clarification"
    assert groups[new["group_id"]] == "completed"
    await service.parse_group({"group_id": new["group_id"]})
    assert len(service.bridge.calls) == 2 and len(service.gateway.writes) == 1


async def test_second_ambiguous_request_is_retained_without_displacing_first(service):
    service.bridge.plan = lambda p: create_plan(p, ambiguities=["请选择格式"])
    await parsed(service)
    first = await question(service)
    second = await parsed(service, message="another", text="记一下 2027年12月20日交另一个报告")
    assert await question(service) == first
    history = await service.db.read(
        "SELECT * FROM question_history WHERE group_id=:g", {"g": second["group_id"]}
    )
    assert len(history) == 1
    receipt = (
        await service.db.read("SELECT * FROM receipt_records ORDER BY created_at DESC LIMIT 1")
    )[0]
    assert "另有活动问题" in receipt["body"] and "回答请引用" not in receipt["body"]
    group = (
        await service.db.read(
            "SELECT * FROM material_groups WHERE id=:g", {"g": second["group_id"]}
        )
    )[0]

    async def body():
        return {"request_id": "activate-second", "expected_revision": group["revision"]}

    request = SimpleNamespace(json=body, username="admin")
    with pytest.raises(NotiDoError) as error:
        await PagesAPI(service).continue_notice(request, group["id"])
    assert error.value.code == "ACTIVE_QUESTION_EXISTS"
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE sessions SET question_expires=0 WHERE id='session'")
    value = await PagesAPI(service).continue_notice(request, group["id"])
    assert value["question"]["question_ref"] not in (
        first["question_ref"],
        history[0]["question_ref"],
    )
    assert (await question(service))["group_id"] == second["group_id"]


async def test_cancel_keeps_written_item_and_only_cancels_unstarted_item(service):
    from test_partial_results import two_items

    service.bridge.plan = two_items
    await parsed(service)
    operations = await service.db.read("SELECT * FROM operations ORDER BY created_at,id")
    await service.execute_operation({"operation_id": operations[0]["id"]})
    result = await service.intake(envelope("cancel", text="取消"))
    assert "1 个尚未开始" in result["message"]
    assert "提交报告" in result["message"] and "真实 ID：" in result["message"]
    actual = await service.db.read("SELECT * FROM operations ORDER BY created_at,id")
    assert [x["state"] for x in actual] == ["succeeded", "cancelled"]
    await service.execute_operation({"operation_id": operations[1]["id"]})
    assert len(service.gateway.writes) == 1 and len(service.gateway.items) == 1
