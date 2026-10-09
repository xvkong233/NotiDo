"""Reproducibly export the strict model contract, without importing AstrBot."""

import json
from pathlib import Path

from notido.models import InputEnvelope, OperationResult, Settings
from notido.native import CancelArgs, ChangeArgs, CreateArgs
from notido.native_delete import DeleteArgs
from notido.native_outcome import OutcomeArgs


def main():
    output = Path(__file__).resolve().parent.parent / "schemas"
    output.mkdir(exist_ok=True)
    for name, schema in (
        ("native-create-v1", CreateArgs.model_json_schema()),
        ("native-update-v1", ChangeArgs.model_json_schema()),
        ("native-delete-v1", DeleteArgs.model_json_schema()),
        ("native-cancel-v1", CancelArgs.model_json_schema()),
        ("native-outcome-v1", OutcomeArgs.model_json_schema()),
        ("input-envelope-v1", InputEnvelope.model_json_schema()),
        ("operation-result-v1", OperationResult.model_json_schema()),
        ("settings-v2", Settings.model_json_schema()),
    ):
        if name == "settings-v2":
            for field in ("identity", "provider_id", "silence_seconds", "window_seconds"):
                schema["properties"].pop(field, None)
            schema.get("$defs", {}).pop("Identity", None)
            for field in ("text_seconds", "material_seconds", "long_seconds", "provider_seconds"):
                schema["$defs"]["TimeBudget"]["properties"].pop(field, None)
        (output / f"{name}.json").write_text(
            json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
