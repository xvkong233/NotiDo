"""Installed AstrBot File/material contract; actual decoder files, zero AI/Dida.

Reuses existing acceptance originals without asking a model to interpret them.
All authorization, cache and budget writes are confined to a temporary fixture DB.
"""

import argparse
import asyncio
import base64
import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path

from astrbot.api.message_components import File
from PIL import Image

from notido.db import execute
from notido.models import Settings
from tools.handler_contract_smoke import ContextFixture, event
from tools.native_material_recovery_contract import NoNetwork, decoded, load_plugin


async def main(fixtures):
    checks = []
    context, gateway = ContextFixture(), NoNetwork()
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        plugin = load_plugin()(
            context, {"data_root": str(folder / "data"), "instance_id": "read-budget-fixture"}
        )
        plugin.service.gateway = gateway
        await plugin.initialize()
        try:
            incoming = event("实际文件读取", "read-budget")
            identity = plugin.service.bridge.identity(incoming)
            settings = Settings(
                account_ref="read-budget-fixture", credential_generation=1, min_free_bytes=0
            )
            async with plugin.service.db.transaction() as conn:
                await execute(
                    conn,
                    "INSERT INTO account_scopes VALUES ('read-budget-fixture','personal','cn',NULL,1,'active',0)",
                )
                await execute(
                    conn, "UPDATE settings SET payload=:p", {"p": settings.model_dump_json()}
                )
                await execute(
                    conn,
                    "INSERT INTO actor_bindings VALUES ('fixture','personal',:i,:a,:s,1,0)",
                    identity,
                )
            jpg = folder / "original.jpg"
            Image.new("RGB", (40, 30), "red").save(jpg)
            originals = [
                ("jpg", jpg),
                ("docx", fixtures / "M02-表格与嵌图.docx"),
                ("six-pages", fixtures / "M01-后页扫描通知.pdf"),
                ("over-pages", fixtures / "M09-超页关键材料.pdf"),
            ]
            for label, path in originals:
                before = hashlib.sha256(path.read_bytes()).hexdigest()
                incoming = event("实际文件读取", f"read-budget-{label}")
                incoming.message_obj.message.append(File(name=path.name, file=str(path)))
                public_items, images, offsets = [], [], []
                offset = 0
                while True:
                    result = await plugin.tools.invoke(incoming, "materials", {"offset": offset})
                    public = decoded(result)
                    public_items.extend(public["items"])
                    images.extend(
                        base64.b64decode(item.data)
                        for item in result.content
                        if item.type == "image"
                    )
                    offsets.append(offset)
                    if public["next_offset"] is None:
                        break
                    offset = public["next_offset"]
                budget = public["read_budget"]
                common = (
                    before == hashlib.sha256(path.read_bytes()).hexdigest()
                    and 0 < budget["charged_seconds"] < budget["limit_seconds"] == 180
                    and all(image.startswith(b"\x89PNG") for image in images)
                )
                if label == "jpg":
                    good = (
                        public["complete"]
                        and len(images) == 1
                        and Image.open(io.BytesIO(images[0])).size == (40, 30)
                    )
                elif label == "docx":
                    with zipfile.ZipFile(path) as archive:
                        embedded = next(
                            x for x in archive.namelist() if x.startswith("word/media/")
                        )
                        expected = Image.open(io.BytesIO(archive.read(embedded))).convert("RGB")
                    good = (
                        public["complete"]
                        and len(images) == 1
                        and any(item["location"] == "embedded:1" for item in public_items)
                    )
                    actual = Image.open(io.BytesIO(images[0]))
                    good = (
                        good
                        and actual.size == expected.size
                        and actual.tobytes() == expected.tobytes()
                    )
                elif label == "six-pages":
                    good = (
                        public["complete"]
                        and len(images) == 6
                        and {
                            item["location"]
                            for item in public_items
                            if item.get("evidence_parameter") == "visual_evidence"
                        }
                        == {f"page:{i}" for i in range(1, 7)}
                    )
                else:
                    good = (
                        not public["complete"]
                        and len(images) == 30
                        and any(
                            item["reason"] == "PDF_PAGE_LIMIT:31-31" for item in public["unknowns"]
                        )
                    )
                replay = decoded(await plugin.tools.invoke(incoming, "materials", {"offset": 0}))
                checks.append(
                    {
                        "check": f"actual_{label}_decoder_and_persistent_cache",
                        "pass": bool(common and good and replay["read_budget"] == budget),
                        "delivered_images": len(images),
                        "page_calls": len(offsets),
                        "charged_seconds": budget["charged_seconds"],
                    }
                )
            checks.append(
                {
                    "check": "no_model_network_or_operations",
                    "pass": not context.models
                    and not gateway.calls
                    and not await plugin.service.db.read("SELECT * FROM operations"),
                }
            )
            checks.append(
                {
                    "check": "migration_10_foreign_keys",
                    "pass": len(await plugin.service.db.read("SELECT * FROM schema_migrations"))
                    == 10
                    and not await plugin.service.db.read("PRAGMA foreign_key_check"),
                }
            )
        finally:
            await plugin.terminate()
    print(
        json.dumps(
            {"checks": checks, "provider_calls": 0, "dida_calls": 0}, ensure_ascii=False, indent=2
        )
    )
    if not all(check["pass"] for check in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, required=True)
    asyncio.run(main(parser.parse_args().fixtures))
