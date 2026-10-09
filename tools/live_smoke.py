"""Manual real-account acceptance, restricted to the explicitly created test project."""

import asyncio
import hashlib
import json
from pathlib import Path

from notido.cli import CLIRunner, DidaGateway

PROJECT_ROOT = Path(__file__).resolve().parent.parent


async def main():
    root = PROJECT_ROOT
    private = root / "runtime-data"
    project = json.loads((private / "test-project.json").read_text(encoding="utf-8-sig"))
    if project["name"] != "NotiDo 验收":
        raise RuntimeError("test project name mismatch")
    node = r"D:\Program Files\nodejs\node.exe"
    home = private / "cli-home"
    gateway = DidaGateway(
        CLIRunner(node, str(root / "node_modules/@suibiji/dida-cli/dist/index.js"), home),
        task_extension=CLIRunner(node, str(root / "tools/task-extension.mjs"), home),
        attachment_runner=CLIRunner(node, str(root / "tools/attachment-cli.mjs"), home),
    )
    records = []
    for title, due, all_day in (
        ("--NotiDo 验收 · 全天与参数数据", "2026-12-20T00:00:00+0800", True),
        ("NotiDo 验收 · 时刻与原生附件", "2026-12-21T15:30:00+0800", False),
    ):
        expected = {
            "title": title,
            "content": "脱敏验收：提交 PDF，命名为 学号_姓名。\n<纯文本>；不含个人通知。",
            "dueDate": due,
            "isAllDay": all_day,
            "timeZone": "Asia/Shanghai",
        }
        response = await gateway.write("create", project["id"], expected)
        if response.error or not isinstance(response.value, dict) or not response.value.get("id"):
            raise RuntimeError("task create failed")
        task_id = response.value["id"]
        # Persist ID immediately so a failed later probe never repeats create.
        records.append({"task_id": task_id, "expected": expected})
        (private / "live-smoke.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        actual = await gateway.get(project["id"], task_id)
        from notido.service import Service

        print(
            json.dumps(
                {
                    "check": "native_task_fields",
                    "all_day": all_day,
                    "pass": Service.fields_match(actual, expected),
                },
                ensure_ascii=False,
            )
        )
    task_id = records[-1]["task_id"]
    sample = private / "NotiDo-验收原件.txt"
    sample.write_bytes("NotiDo 原件附件验收\n不含真实通知或身份。\n".encode())
    plan = {
        "project_id": project["id"],
        "task_id": task_id,
        "attachment_id": "6ac60bda0000000000000001",
        "blob_path": str(sample),
        "name": sample.name,
        "hash": hashlib.sha256(sample.read_bytes()).hexdigest(),
    }
    response = await gateway.attachment_runner.call(
        ["task-get", f"--project={project['id']}", f"--task={task_id}"], envelope=True
    )
    print(
        json.dumps(
            {
                "check": "same_account_visibility",
                "pass": not response.error
                and response.value.get("title") == records[-1]["expected"]["title"],
            }
        )
    )
    gateway.attachment_verified = (
        True  # This script is the explicit stage-zero probe, never production setup.
    )
    uploaded = await gateway.upload(plan)
    (private / "attachment-smoke.json").write_text(
        json.dumps({"plan": plan, "response": uploaded.__dict__}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "check": "attachment_upload",
                "side_effect": uploaded.side_effect,
                "error": uploaded.error,
                "reliable_id": bool(uploaded.value and uploaded.value.get("id")),
            }
        )
    )
    if uploaded.value and uploaded.value.get("id"):
        verified = await gateway.inspect_upload(plan, uploaded.value["id"])
        print(
            json.dumps(
                {
                    "check": "attachment_download_hash",
                    "pass": verified["sha256"] == plan["hash"],
                    "size": verified["size"],
                }
            )
        )
        (private / "attachment-verified.json").write_text(
            json.dumps(verified, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    asyncio.run(main())
