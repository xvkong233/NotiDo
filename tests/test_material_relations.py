import copy
import json

from conftest import create_plan, envelope, parsed
from test_queue_dependencies import add_asset


async def test_unread_material_cannot_be_finished_by_a_model_summary(service):
    group = await service.intake(envelope(text="请记录图片通知中的必做事项"))
    async with service.db.transaction() as conn:
        await service.add_segment(
            conn, group["group_id"], "picture", "image:unread", "", "unknown", "PROVIDER_TIMEOUT"
        )
    service.bridge.plan = {
        "schema_version": 4,
        "intent": "none",
        "ambiguities": [],
        "reason": "图片未读出",
        "safe_summary": "没有识别到任务",
    }
    await service.parse_group({"group_id": group["group_id"]})
    assert not await service.db.read("SELECT * FROM operations")
    assert (await service.db.read("SELECT state FROM material_groups"))[0][
        "state"
    ] == "awaiting_clarification"
    question = json.loads((await service.db.read("SELECT question FROM sessions"))[0]["question"])
    assert question["context"]["blocked"][0]["code"] == "MATERIAL_UNREAD"
    assert question["context"]["blocked"][0]["locations"][0]["reason"] == "PROVIDER_TIMEOUT"


async def test_known_irrelevant_notice_does_not_ask_to_log_its_disposition(service):
    def plan(payload):
        value = create_plan(payload, intent="ingest_notice", relevance="not_applies")
        value["ambiguities"] = ["是否记录不适用状态？"]
        return value

    service.bridge.plan = plan
    await parsed(service, source_kind="manual_notice")
    assert not await service.db.read("SELECT * FROM operations")
    assert (await service.db.read("SELECT state FROM material_groups"))[0]["state"] == "completed"
    assert (await service.db.read("SELECT question FROM sessions"))[0]["question"] is None


async def test_shared_and_exclusive_assets_map_to_only_their_verified_tasks(service):
    group = await service.intake(
        envelope(
            text="记一下 2027年12月20日交报告和交图片，common.zip 共用，report.pdf 给报告，image.png 给图片"
        )
    )
    shared = await add_asset(service, group["group_id"], b"common", "common.zip")
    report = await add_asset(service, group["group_id"], b"report", "report.pdf")
    image = await add_asset(service, group["group_id"], b"image", "image.png")

    def plan(payload):
        value = create_plan(payload, title="交报告")
        value["tasks"][0]["attachment_asset_ids"] = [shared, report]
        second = copy.deepcopy(value["tasks"][0])
        second["title"] = "交图片"
        second["attachment_asset_ids"] = [shared, image]
        value["tasks"].append(second)
        return value

    service.bridge.plan = plan
    await service.parse_group({"group_id": group["group_id"]})
    tasks = await service.db.read(
        "SELECT * FROM operations WHERE kind='create' ORDER BY created_at,id"
    )
    for task in tasks:
        await service.execute_operation({"operation_id": task["id"]})
    links = await service.db.read("SELECT * FROM task_attachment_links")
    assert len(links) == 4
    assets_by_task = {}
    for link in links:
        assets_by_task.setdefault(link["task_id"], set()).add(link["asset_id"])
    tasks = await service.db.read(
        "SELECT * FROM operations WHERE kind='create' ORDER BY created_at,id"
    )
    assert assets_by_task[tasks[0]["remote_id"]] == {shared, report}
    assert assets_by_task[tasks[1]["remote_id"]] == {shared, image}
    assert len(service.gateway.items) == 2


async def test_missing_referenced_attachment_blocks_only_dependent_item(service):
    def plan(payload):
        value = create_plan(payload, title="交报告")
        first = value["tasks"][0]
        first["source_evidence"][0]["quote"] = "2027年12月20日交报告，提交要求详见附件"
        second = copy.deepcopy(first)
        second["title"] = "交学费"
        second["source_evidence"][0]["quote"] = "2027年12月20日交学费"
        second["requirements"] = []
        value["tasks"].append(second)
        return value

    service.bridge.plan = plan
    await parsed(
        service, text="记一下 2027年12月20日交报告，提交要求详见附件；2027年12月20日交学费"
    )
    operations = await service.db.read("SELECT * FROM operations")
    assert len(operations) == 1 and json.loads(operations[0]["plan"])["fields"]["title"] == "交学费"
    await service.execute_operation({"operation_id": operations[0]["id"]})
    assert len(service.gateway.items) == 1
    session = (await service.db.read("SELECT question FROM sessions"))[0]
    assert "REQUIRED_ASSET_MISSING" in session["question"]


async def test_ambiguous_file_relation_does_not_assign_it_to_an_arbitrary_task(service):
    group = await service.intake(envelope())
    asset = await add_asset(service, group["group_id"], b"unassigned original")

    def plan(payload):
        value = create_plan(payload, title="任务一")
        second = copy.deepcopy(value["tasks"][0])
        second["title"] = "任务二"
        second["attachment_asset_ids"] = [asset]
        second["ambiguities"] = ["此文件属于任务一还是任务二？"]
        value["tasks"].append(second)
        return value

    service.bridge.plan = plan
    await service.parse_group({"group_id": group["group_id"]})
    task = (await service.db.read("SELECT * FROM operations"))[0]
    await service.execute_operation({"operation_id": task["id"]})
    assert len(service.gateway.items) == 1
    assert not await service.db.read("SELECT * FROM task_attachment_links")
    assert "此文件属于" in (await service.db.read("SELECT question FROM sessions"))[0]["question"]
