import json

import pytest
from conftest import create_plan, envelope, operation, parsed

from notido.chunks import material_blocks
from notido.db import execute
from notido.errors import NotiDoError
from notido.materials import BlobStore
from notido.models import MaterialBudget


def segment(text, location="page:1", source="source"):
    return {
        "text": text,
        "location": location,
        "source_id": source,
        "state": "read",
        "reason": None,
    }


def test_blocks_preserve_evidence_and_cross_boundary_overlap():
    text = "甲" * 11780 + "截止日期为2027年12月20日" + "乙" * 18000
    materials, scope = material_blocks([segment(text)], MaterialBudget())
    assert scope["complete"] and scope["blocks"] == 3
    assert materials[0]["text"][-500:] == materials[1]["text"][:500]
    assert "截止日期为2027年12月20日" in materials[0]["text"]
    assert "截止日期为2027年12月20日" in materials[1]["text"]
    for part in materials:
        assert part["text"] == text[part["start"] : part["end"]]
        assert len(part["text"]) <= 12000 and part["location"] == "page:1"
    assert (
        "".join(p["text"] if i == 0 else p["text"][500:] for i, p in enumerate(materials)) == text
    )


def test_many_paragraphs_share_blocks_and_unknowns_remain():
    segments = [segment("段落" * 80, f"block:{i}") for i in range(100)]
    unknown = {**segment("", "page:unread"), "state": "unknown", "reason": "SCAN_UNREADABLE"}
    parts, scope = material_blocks([*segments, unknown], MaterialBudget())
    assert scope["blocks"] == 2 and parts[-1] == unknown
    for original in segments:
        coverage = set()
        for part in parts[:-1]:
            if part["location"] == original["location"]:
                coverage.update(range(part["start"], part["end"]))
                assert part["text"] == original["text"][part["start"] : part["end"]]
        assert coverage == set(range(len(original["text"])))


def test_block_overflow_reports_exact_unread_source_range():
    with pytest.raises(NotiDoError) as caught:
        material_blocks([segment("甲" * 13000)], MaterialBudget(blocks=1))
    assert caught.value.code == "MODEL_INPUT_LIMIT"
    assert caught.value.details["unread_ranges"] == [
        {
            "source_id": "source",
            "location": "page:1",
            "start": 12000,
            "end": 13000,
        }
    ]


async def test_configured_admission_and_processing_budget(service):
    settings, _ = await service.db.settings()
    settings.materials.message_characters = 20
    settings.time_budgets.text_seconds = 12
    async with service.db.transaction() as conn:
        await execute(conn, "UPDATE settings SET payload=:p", {"p": settings.model_dump_json()})
    with pytest.raises(NotiDoError, match="消息超过材料预算"):
        await service.intake(envelope(text="甲" * 21))
    assert not await service.db.read("SELECT * FROM material_groups")
    group = await service.intake(envelope(text="记一下提交报告"))
    await service.processing_budget(group["group_id"])
    record = (await service.db.read("SELECT * FROM processing_budgets"))[0]
    assert record["seconds"] == 12


async def test_completed_group_ignores_old_close_read_and_parse_jobs(service):
    service.bridge.plan = create_plan
    group = await parsed(service)
    op = await operation(service)
    await service.execute_operation({"operation_id": op["id"]})
    before = (await service.db.read("SELECT * FROM material_groups"))[0]
    assert before["state"] == "completed"
    await service.close_group({"group_id": group["group_id"]})
    await service.read_materials({"group_id": group["group_id"]})
    await service.parse_group({"group_id": group["group_id"]})
    after = (await service.db.read("SELECT * FROM material_groups"))[0]
    assert after == before and len(service.bridge.calls) == 1


async def test_delayed_close_keeps_existing_question(service):
    service.bridge.plan = lambda payload: create_plan(payload, ambiguities=["需要确认"])
    group = await parsed(service)
    before = json.loads((await service.db.read("SELECT question FROM sessions"))[0]["question"])
    await service.close_group({"group_id": group["group_id"]})
    assert (await service.db.read("SELECT state FROM material_groups"))[0][
        "state"
    ] == "awaiting_clarification"
    assert (
        json.loads((await service.db.read("SELECT question FROM sessions"))[0]["question"])
        == before
    )


async def test_document_character_budget_records_location(tmp_path):
    original = tmp_path / "document.txt"
    original.write_text("甲" * 1001, encoding="utf-8")
    result = await BlobStore(tmp_path).read(
        original, "document.txt", budget=MaterialBudget(document_characters=1000)
    )
    assert result["segments"] == [] and result["unknowns"] == ["TEXT_LIMIT:text"]
