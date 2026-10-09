import json
from datetime import UTC, datetime

from conftest import create_plan, envelope, operation


async def notice(service, message, text, published=None, payload=None):
    event = envelope(message=message, text=text, source_kind="user_forward")
    event.segments[0].published_at = published
    group = await service.intake(event)
    await service.parse_group({"group_id": group["group_id"], **(payload or {})})
    return group


def postponement(payload):
    plan = create_plan(payload, intent="ingest_notice")
    first = plan["tasks"][0]
    plan["tasks"] = [
        {
            "action": "update",
            "target": {"keyword": "提交报告"},
            "patch": {"date_text": "2027年12月21日"},
            "relevance": "applies",
            "obligation": "required",
            "source_evidence": first["source_evidence"],
            "ambiguities": [],
        }
    ]
    return plan


async def initial(service):
    service.bridge.plan = lambda p: create_plan(p, intent="ingest_notice")
    await notice(service, "original", "2027年12月20日提交报告", datetime(2026, 10, 7, tzinfo=UTC))
    first = await operation(service)
    await service.execute_operation({"operation_id": first["id"]})
    return (await operation(service)), next(iter(service.gateway.items.values()))


async def test_postpone_retains_action_identity_and_user_notes(service):
    first, task = await initial(service)
    task["content"] = "用户自己记的说明\n" + task["content"] + "\n用户追加内容"
    service.bridge.plan = postponement
    group = await notice(
        service, "later", "提交报告延期至2027年12月21日", datetime(2026, 10, 8, tzinfo=UTC)
    )
    # Repeated parsing before claim reuses the immutable update operation.
    await service.parse_group({"group_id": group["group_id"]})
    ops = await service.db.read("SELECT * FROM operations ORDER BY created_at")
    assert len(ops) == 2 and ops[1]["action_id"] == first["action_id"]
    assert (await service.db.read("SELECT revision FROM action_items"))[0]["revision"] == 1
    await service.execute_operation({"operation_id": ops[1]["id"]})
    assert len(service.gateway.items) == 1
    assert task["dueDate"].startswith("2027-12-21")
    assert task["content"].startswith("用户自己记的说明\n") and task["content"].endswith(
        "\n用户追加内容"
    )
    assert "PDF 格式" in task["content"] and "2027年12月21日" in task["content"]


async def test_older_or_unknown_source_order_asks(service):
    await initial(service)
    service.bridge.plan = postponement
    await notice(
        service, "older", "提交报告延期至2027年12月21日", datetime(2026, 10, 6, tzinfo=UTC)
    )
    assert len(await service.db.read("SELECT * FROM operations")) == 1
    assert (
        "SOURCE_ORDER_CONFLICT"
        in (await service.db.read("SELECT question FROM sessions"))[0]["question"]
    )
    # Begin the separate unknown-source scenario after the first question expires.
    from notido.db import execute

    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE sessions SET question_expires=0")
    group = await notice(service, "unknown", "提交报告延期至2027年12月21日")
    session = (await service.db.read("SELECT * FROM sessions"))[0]
    question = json.loads(session["question"])
    assert "SOURCE_ORDER_UNKNOWN" in session["question"]
    clarification = service.answer_context(question, "确认这是最新通知")
    assert clarification["source_order_confirmed"]
    await service.parse_group({"group_id": group["group_id"], "clarification": clarification})
    assert len(await service.db.read("SELECT * FROM operations")) == 2


async def test_edited_managed_region_and_completed_tasks_are_not_overwritten(service):
    _, task = await initial(service)
    task["content"] = task["content"].replace("PDF 格式", "用户修改的格式")
    service.bridge.plan = postponement
    await notice(
        service, "later", "提交报告延期至2027年12月21日", datetime(2026, 10, 8, tzinfo=UTC)
    )
    assert len(await service.db.read("SELECT * FROM operations")) == 1
    assert (
        "NOTES_CONFLICT" in (await service.db.read("SELECT question FROM sessions"))[0]["question"]
    )
    task["status"] = 2
    await notice(
        service, "completed", "提交报告延期至2027年12月21日", datetime(2026, 10, 9, tzinfo=UTC)
    )
    assert len(await service.db.read("SELECT * FROM operations")) == 1
