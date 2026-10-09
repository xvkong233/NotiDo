"""Expose the existing validators as AstrBot-native function parameter schemas."""

from copy import deepcopy

from .models import Query
from .native import CancelArgs, ChangeArgs, CreateArgs
from .native_delete import DeleteArgs
from .native_outcome import OutcomeArgs

MODELS = {
    "notido_create": ("task", CreateArgs),
    "notido_update": ("change", ChangeArgs),
    "notido_query": ("query", Query),
    "notido_cancel": ("cancellation", CancelArgs),
    "notido_delete": ("deletion", DeleteArgs),
    "notido_record_outcome": ("outcome", OutcomeArgs),
}


def parameters(name):
    if name not in MODELS:
        return None
    argument, model = MODELS[name]
    raw = model.model_json_schema()
    definitions = raw.get("$defs", {})

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            definition = definitions[value["$ref"].removeprefix("#/$defs/")]
            return expand({**definition, **{k: v for k, v in value.items() if k != "$ref"}})
        return {k: expand(v) for k, v in value.items() if k != "$defs"}

    schema = expand(deepcopy(raw))
    if name == "notido_query":
        schema["properties"]["offset"] = {
            "type": "integer",
            "minimum": 0,
            "default": 0,
            "description": "后续页使用上次返回的next_offset；范围变化会刷新。",
        }
    if name == "notido_create":
        properties = schema["properties"]
        properties["priority"]["enum"] = [0, 1, 3, 5]
        properties["evidence"]["description"] = (
            "仅可读文字逐字依据；source_id/location原样复制材料项evidence_reference，图像解读不放这里。"
        )
        properties["visual_evidence"]["description"] = (
            "真实已交付图像的解读；source_id/location原样复制图像项evidence_reference，不能用asset_id或缓存路径替代。"
        )
    return {
        "type": "object",
        "properties": {argument: schema},
        "required": [argument],
        "additionalProperties": False,
    }
