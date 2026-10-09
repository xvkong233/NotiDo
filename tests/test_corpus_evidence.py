import json

from acceptance_helpers import returned_evidence, tool_results


def output(*calls):
    entries = []
    for index, (name, value) in enumerate(calls):
        entries += [
            {"kind": "tool_call", "payload": {"id": str(index), "name": name}},
            {
                "kind": "tool_call_result",
                "payload": {"id": str(index), "result": json.dumps(value)},
            },
        ]
    return {"tools": entries}


def test_historical_notice_snapshot_is_not_actual_readback():
    material = {"known_actions": [{"task_id": "linked", "snapshot": "old"}]}
    refs, observed = returned_evidence(
        output(
            ("notido_materials", material),
            ("notido_query", {"tasks": [{"id": "unrelated", "title": "same title"}]}),
        )
    )
    assert not refs and not observed
    _, observed = returned_evidence(
        output(
            ("notido_materials", material),
            ("notido_query", {"tasks": [{"id": "linked", "content": "current"}]}),
        )
    )
    assert observed[0]["actual_fields"]["content"] == "current"


def test_reused_operation_requires_successful_current_tool_result():
    refs, _ = returned_evidence(
        output(
            (
                "notido_create",
                {"operation_id": "ok", "state": "succeeded", "reused_existing": True},
            ),
            ("notido_create", {"operation_id": "unknown", "state": "unknown"}),
            ("notido_check", {"operation_id": "error", "state": "succeeded", "error": "DENIED"}),
            ("notido_cancel", {"operation_id": "cancel", "state": "cancelled"}),
        )
    )
    assert set(refs) == {"ok"}


def test_remote_query_requires_exact_complete_current_notice_marker():
    notice = "a" * 64
    content = f"<!-- NOTIDO:{notice}:START -->\nnotice: {notice}\n<!-- NOTIDO:{notice}:END -->"
    _, observed = returned_evidence(
        output(
            ("notido_materials", {"notice_ref": notice, "known_actions": []}),
            (
                "notido_query",
                {
                    "tasks": [
                        {"id": "match", "content": content},
                        {"id": "plain", "content": notice},
                        {"id": "broken", "content": content.replace(":END", ":BAD")},
                        {"id": "duplicated", "content": content + content},
                    ]
                },
            ),
        )
    )
    assert [r["actual_fields"]["id"] for r in observed] == ["match"]
    assert observed[0]["source_action"]["association"] == "exact_remote_managed_marker"


def test_framework_image_description_is_not_part_of_material_manifest():
    data = output(("notido_materials", {"notice_ref": "n", "items": []}))
    data["tools"][1]["payload"]["result"] += "\n\nImage returned and cached at path='local'."
    assert list(tool_results(data)) == [("notido_materials", {"notice_ref": "n", "items": []})]
    data["tools"][0]["payload"]["name"] = "notido_query"
    assert not list(tool_results(data))
