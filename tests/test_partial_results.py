import copy
import json
from types import SimpleNamespace

import pytest
from conftest import create_plan, parsed
from test_queue_dependencies import add_asset

from notido.api import PagesAPI
from notido.cli import CLIResponse
from notido.errors import NotiDoError


def request(row, request_id):
    async def body():
        return {"request_id": request_id, "expected_revision": row["revision"]}

    return SimpleNamespace(json=body, username="admin")


def two_items(payload):
    plan = create_plan(payload, title="提交报告")
    second = copy.deepcopy(plan["tasks"][0])
    second["title"] = "提交图片"
    second["requirements"] = ["PNG 原图"]
    plan["tasks"].append(second)
    return plan


async def test_partial_create_retry_preserves_success_plan_and_receipts(service):
    service.bridge.plan = two_items
    await parsed(service, text="记一下 2027年12月20日提交报告 PDF 格式和提交图片 PNG 原图")
    operations = await service.db.read("SELECT * FROM operations ORDER BY created_at,id")
    assert len(operations) == 2
    first, second = operations
    await service.execute_operation({"operation_id": first["id"]})
    service.gateway.write_error = CLIResponse(error="AUTH_REQUIRED", side_effect="none")
    await service.execute_operation({"operation_id": second["id"]})
    actual = await service.db.read("SELECT * FROM operations ORDER BY created_at,id")
    assert [x["state"] for x in actual] == ["succeeded", "failed_safe"]
    saved_id = actual[0]["remote_id"]
    assert len(service.gateway.items) == 1
    api = PagesAPI(service)
    with pytest.raises(NotiDoError) as error:
        await api.retry(request(actual[0], "do-not-repeat-success"), first["id"])
    assert error.value.code == "UNSAFE_RETRY"
    value = await api.retry(request(actual[1], "retry-only-failure"), second["id"])
    assert value == await api.retry(request(actual[1], "retry-only-failure"), second["id"])
    service.gateway.write_error = None
    await service.execute_operation({"operation_id": second["id"]})
    actual = await service.db.read("SELECT * FROM operations ORDER BY created_at,id")
    assert all(x["state"] == "succeeded" for x in actual)
    assert actual[0]["remote_id"] == saved_id and actual[0]["attempt"] == 1
    assert actual[1]["plan"] == second["plan"] and actual[1]["attempt"] == 2
    assert len(service.gateway.items) == 2 and len(service.bridge.calls) == 1
    receipts = await service.db.read("SELECT * FROM receipt_records")
    assert len(receipts) == 3
    assert any("未执行：提交图片" in x["body"] for x in receipts)
    assert all("清单：学习" in x["body"] for x in receipts)
    assert any("要求：PNG 原图" in x["body"] for x in receipts)
    for receipt in receipts:
        await service.send_receipt({"receipt_id": receipt["id"]})
    count = len(service.gateway.writes)
    await service.send_receipt({"receipt_id": receipts[0]["id"]})
    assert len(service.bridge.receipts) == 3 and len(service.gateway.writes) == count


async def test_attachment_quota_failure_retries_only_that_original(service):
    service.bridge.plan = create_plan
    group = await parsed(service)
    task = (await service.db.read("SELECT * FROM operations"))[0]
    shared = await add_asset(service, group["group_id"], b"shared original", "common.zip")
    exclusive = await add_asset(service, group["group_id"], b"exclusive original", "report.pdf")
    plan = json.loads(task["plan"])
    plan["attachment_asset_ids"] = [shared, exclusive]
    from notido.db import execute

    async with service.db.transaction() as conn:
        await execute(
            conn,
            "UPDATE operations SET plan=:p WHERE id=:id",
            {"p": json.dumps(plan), "id": task["id"]},
        )
    await service.execute_operation({"operation_id": task["id"]})
    uploads = await service.db.read(
        "SELECT * FROM operations WHERE kind='upload' ORDER BY created_at,id"
    )
    calls, uploaded = [], {}
    quota = True

    async def upload(candidate):
        calls.append(candidate)
        if candidate["asset_id"] == exclusive and quota:
            return CLIResponse(error="ATTACHMENT_QUOTA", side_effect="none")
        uploaded[candidate["attachment_id"]] = candidate
        return CLIResponse(value={"id": candidate["attachment_id"]}, side_effect="applied")

    async def inspect(candidate, remote_id):
        actual = uploaded[remote_id]
        return {"sha256": actual["hash"], "task_id": actual["task_id"]}

    service.gateway.attachment_capability = lambda: {"supported": True}
    service.gateway.upload, service.gateway.inspect_upload = upload, inspect
    for row in uploads:
        await service.execute_operation({"operation_id": row["id"]})
    actual = await service.db.read(
        "SELECT * FROM operations WHERE kind='upload' ORDER BY created_at,id"
    )
    assert [x["state"] for x in actual] == ["succeeded", "failed_safe"]
    assert len(service.gateway.items) == 1 and len(service.gateway.writes) == 1
    quota = False
    await PagesAPI(service).retry(request(actual[1], "quota-resolved"), actual[1]["id"])
    await service.execute_operation({"operation_id": actual[1]["id"]})
    assert [x["asset_id"] for x in calls] == [shared, exclusive, exclusive]
    assert len(uploaded) == 2 and len(service.gateway.items) == 1
    assets = await service.db.read("SELECT * FROM assets")
    assert all(x["state"] == "ready" and x["hash"] for x in assets)
    receipts = await service.db.read("SELECT body FROM receipt_records")
    assert any("ATTACHMENT_QUOTA" in x["body"] for x in receipts)
    assert sum("原件已上传并核验" in x["body"] for x in receipts) == 2
    assert all(
        "关联任务 ID：" in x["body"]
        for x in receipts
        if "common.zip" in x["body"] or "report.pdf" in x["body"]
    )
