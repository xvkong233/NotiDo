"""Fresh offline snapshot includes native task revisions, attachments, and material caches."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from notido.cli import CLIRunner, DidaGateway
from notido.models import InputEnvelope
from notido.native import NativeTools
from notido.service import Service
from tools.container_smoke import ROOT
from tools.live_restore_smoke import NoProvider, ReadOnlyGateway, prepare, verify
from tools.live_same_name_smoke import save


async def main(journal, state, target):
    await verify(journal, state, target)
    home = target / "cli-home"
    gateway = ReadOnlyGateway(
        DidaGateway(
            CLIRunner(
                r"D:\Program Files\nodejs\node.exe",
                str(ROOT / "node_modules/@suibiji/dida-cli/dist/index.js"),
                home,
            ),
            task_extension=CLIRunner(
                r"D:\Program Files\nodejs\node.exe", str(ROOT / "tools/task-extension.mjs"), home
            ),
            attachment_runner=CLIRunner(
                r"D:\Program Files\nodejs\node.exe", str(ROOT / "tools/attachment-cli.mjs"), home
            ),
            attachment_verified=True,
        )
    )
    bridge = NoProvider()
    bridge.identity = lambda event: event.identity
    bridge.normalize_event = lambda event: (_ for _ in ()).throw(
        AssertionError("existing material must use durable envelope")
    )
    bridge.release_material_refs = lambda envelope: None
    service = Service(target, bridge, gateway)
    await service.start()
    try:
        rows = await service.db.read("SELECT group_id,payload FROM native_material_reads")
        visual_groups = [
            row
            for row in rows
            if any("image_path" in item for item in json.loads(row["payload"])["items"])
        ]
        if len(visual_groups) < 3:
            raise RuntimeError("fresh backup must include PNG/PDF/DOCX native material caches")
        native = NativeTools(service)
        for group in visual_groups:
            record = (
                await service.db.read(
                    "SELECT envelope FROM message_records WHERE group_id=:g LIMIT 1",
                    {"g": group["group_id"]},
                )
            )[0]
            envelope = InputEnvelope.model_validate_json(record["envelope"])
            incoming = SimpleNamespace(
                identity={
                    "i": envelope.framework_instance_id,
                    "a": envelope.actor_key,
                    "s": envelope.session_key,
                },
                message_obj=SimpleNamespace(message_id=envelope.message_key),
            )
            offset, had_image = 0, False
            while True:
                result = await native.invoke(
                    incoming, "materials", {"group_id": group["group_id"], "offset": offset}
                )
                if isinstance(result, str):
                    raise RuntimeError("restored material delivery failed")
                had_image |= any(item.type == "image" for item in result.content)
                manifest = json.loads(result.content[0].text)
                if manifest["next_offset"] is None:
                    break
                if manifest["next_offset"] <= offset:
                    raise RuntimeError("restored material pagination did not advance")
                offset = manifest["next_offset"]
            original_unknowns = json.loads(group["payload"])["unknowns"]
            if (
                not had_image
                or manifest["complete"] is bool(original_unknowns)
                or any(asset["state"] != "ready" for asset in manifest["assets"])
            ):
                raise RuntimeError("restored material delivery was incomplete")
            if [(u.get("asset_id"), u.get("reason")) for u in manifest["unknowns"]] != [
                (u.get("asset_id"), u.get("reason")) for u in original_unknowns
            ]:
                raise RuntimeError("restore changed unknown ranges instead of preserving them")
            cached = json.loads(
                (
                    await service.db.read(
                        "SELECT payload FROM native_material_reads WHERE group_id=:g",
                        {"g": group["group_id"]},
                    )
                )[0]["payload"]
            )
            if any(
                not Path(item["image_path"]).is_relative_to(target / "derived")
                for item in cached["items"]
                if "image_path" in item
            ):
                raise RuntimeError("restored preview escaped isolated restore root")
        if gateway.writes or bridge.calls:
            raise RuntimeError("restore must never write remote data or call AI")
        state["checks"].append(
            {
                "check": "native_materials_rebuilt_from_restored_originals",
                "pass": True,
                "groups": len(visual_groups),
            }
        )
        if any(
            item.get("verification") == "deleted_by_confirmed_operation"
            for item in state["review"]["history"]
        ):
            state["checks"].append(
                {"check": "confirmed_deletion_history_native_tombstone_readback", "pass": True}
            )
        save(journal, state)
        print(json.dumps(state["checks"]))
    finally:
        await service.stop()


if __name__ == "__main__":
    if sys.argv[1:] == ["--complex-history"]:
        asyncio.run(main(*prepare("native-complex-restore-results.json", "notido-native-v5")))
        sys.exit(0)
    journal = (
        "native-delete-restore-results.json"
        if sys.argv[1:] == ["--deleted-history"]
        else "native-restore-results.json"
    )
    asyncio.run(main(*prepare(journal)))
