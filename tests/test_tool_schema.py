from notido.tool_schema import parameters


def test_native_nested_schema_matches_validator_boundaries():
    schema = parameters("notido_create")
    task = schema["properties"]["task"]
    notice = next(s for s in task["properties"]["notice"]["anyOf"] if s.get("type") == "object")
    assert set(notice["properties"]) == {"group_id", "action_key"}
    assert notice["additionalProperties"] is False
    assert "requirements" in task["properties"] and "action_key" not in task["properties"]
    assert set(task["required"]) == {"request_key", "title"}
    assert task["properties"]["priority"]["enum"] == [0, 1, 3, 5]
    image = task["properties"]["visual_evidence"]["items"]
    assert set(image["required"]) == {"source_id", "location", "quote"}
    assert image["additionalProperties"] is False
    assert "$ref" not in str(schema) and "$defs" not in str(schema)
    schema["properties"]["task"]["properties"].clear()
    assert parameters("notido_create")["properties"]["task"]["properties"]
    assert parameters("framework_other") is None
