import json
import time

from conftest import create_plan, parsed


async def test_multiple_optional_choices_keep_specific_consents_across_rounds(service):
    def plan(payload):
        first = create_plan(
            payload, intent="ingest_notice", obligation="optional", title="参加活动甲"
        )
        second = create_plan(
            payload, intent="ingest_notice", obligation="optional", title="参加活动乙"
        )
        first["tasks"].append(second["tasks"][0])
        first["optional_items"] = ["参加活动甲", "参加活动乙"]
        return first

    service.bridge.plan = plan
    group = await parsed(
        service, text="2027年12月20日自愿参加活动甲或活动乙", source_kind="manual_notice"
    )
    assert not await service.db.read("SELECT * FROM operations")
    first_question = json.loads(
        (await service.db.read("SELECT question FROM sessions"))[0]["question"]
    )
    await service.resolve_question(
        "session", first_question["question_ref"], "参加 参加活动甲", "origin"
    )
    payload = json.loads(
        (
            await service.db.read(
                "SELECT payload FROM jobs WHERE dedupe_key=:k",
                {"k": f"clarify:{first_question['question_ref']}"},
            )
        )[0]["payload"]
    )
    await service.parse_group(payload)
    assert len(await service.db.read("SELECT * FROM operations")) == 1
    second_question = json.loads(
        (await service.db.read("SELECT question FROM sessions"))[0]["question"]
    )
    assert second_question["context"]["clarification"]["participation_titles"] == ["参加活动甲"]
    # Time spent answering does not consume a new processing turn's budget.
    service.processing_deadlines[group["group_id"]] = time.monotonic() - 1
    await service.resolve_question(
        "session", second_question["question_ref"], "参加 参加活动乙", "origin"
    )
    assert group["group_id"] not in service.processing_deadlines
    payload = json.loads(
        (
            await service.db.read(
                "SELECT payload FROM jobs WHERE dedupe_key=:k",
                {"k": f"clarify:{second_question['question_ref']}"},
            )
        )[0]["payload"]
    )
    await service.parse_group(payload)
    assert len(await service.db.read("SELECT * FROM operations")) == 2
    assert (await service.db.read("SELECT question FROM sessions"))[0]["question"] is None
