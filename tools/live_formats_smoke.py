"""Opt-in real-account original-format probe, with durable no-replay attempt records."""

import asyncio
import hashlib
import json
import zipfile
from pathlib import Path

from PIL import Image
from pypdf import PdfWriter

from notido.cli import CLIRunner, DidaGateway
from notido.keys import uid

PROJECT_ROOT = Path(__file__).resolve().parent.parent


async def main():
    root = PROJECT_ROOT
    private = root / "runtime-data"
    project = json.loads((private / "test-project.json").read_text(encoding="utf-8-sig"))
    if project["name"] != "NotiDo 验收":
        raise RuntimeError("dedicated project required")
    task = json.loads((private / "live-smoke.json").read_text(encoding="utf-8"))[-1]["task_id"]
    node = r"D:\Program Files\nodejs\node.exe"
    gateway = DidaGateway(
        CLIRunner(
            node, str(root / "node_modules/@suibiji/dida-cli/dist/index.js"), private / "cli-home"
        ),
        attachment_runner=CLIRunner(
            node, str(root / "tools/attachment-cli.mjs"), private / "cli-home"
        ),
    )
    gateway.attachment_verified = True
    journal = private / "live-format-results.json"
    if journal.exists():
        records = json.loads(journal.read_text(encoding="utf-8"))
    else:
        paths = [private / f"NotiDo-验收原件.{suffix}" for suffix in ("png", "pdf", "zip")]
        Image.new("RGB", (240, 120), (80, 140, 120)).save(paths[0])
        pdf = PdfWriter()
        pdf.add_blank_page(width=200, height=200)
        with paths[1].open("wb") as file:
            pdf.write(file)
        with zipfile.ZipFile(paths[2], "w") as archive:
            archive.writestr("sample.txt", b"NotiDo original-format acceptance only\n")
        records = [
            {
                "plan": {
                    "project_id": project["id"],
                    "task_id": task,
                    "attachment_id": uid().replace("-", "")[:24],
                    "blob_path": str(path),
                    "name": path.name,
                    "hash": hashlib.sha256(path.read_bytes()).hexdigest(),
                },
                "attempted": False,
            }
            for path in paths
        ]
        journal.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    await gateway.get(project["id"], task)
    for record in records:
        plan = record["plan"]
        if plan["project_id"] != project["id"] or plan["task_id"] != task:
            raise RuntimeError("journal target mismatch")
        if not record["attempted"]:
            record["attempted"] = True
            journal.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
            response = await gateway.upload(plan)
            record["response"] = response.__dict__
            journal.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        # Even a crashed upload is inspected by its preallocated ID; never uploaded twice.
        actual = await gateway.inspect_upload(plan, plan["attachment_id"])
        record["verified"] = actual["sha256"] == plan["hash"] and actual["task_id"] == task
        record["actual"] = actual
        journal.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "format": Path(plan["name"]).suffix,
                    "hash_verified": record["verified"],
                    "size": actual["size"],
                }
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
