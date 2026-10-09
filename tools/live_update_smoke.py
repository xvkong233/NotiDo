"""Continue stage-zero verification using already saved real task IDs; never create again."""

import asyncio
import json
from pathlib import Path

from notido.cli import CLIRunner, DidaGateway
from notido.service import Service

PROJECT_ROOT = Path(__file__).resolve().parent.parent


async def main():
    root = PROJECT_ROOT
    private = root / "runtime-data"
    project = json.loads((private / "test-project.json").read_text(encoding="utf-8-sig"))
    records = json.loads((private / "live-smoke.json").read_text(encoding="utf-8"))
    if project["name"] != "NotiDo 验收":
        raise RuntimeError("Test project mismatch")
    node = r"D:\Program Files\nodejs\node.exe"
    home = private / "cli-home"
    gateway = DidaGateway(
        CLIRunner(node, str(root / "node_modules/@suibiji/dida-cli/dist/index.js"), home),
        task_extension=CLIRunner(node, str(root / "tools/task-extension.mjs"), home),
    )
    checks = []
    first = records[0]["task_id"]
    expected = {
        "dueDate": "2026-12-22T16:45:00+0800",
        "isAllDay": False,
        "timeZone": "Asia/Shanghai",
    }
    result = await gateway.write("update", project["id"], expected, first)
    actual = await gateway.get(project["id"], first)
    checks.append(
        {
            "check": "clear_all_day_and_set_time",
            "pass": not result.error and Service.fields_match(actual, expected),
        }
    )
    clear = await gateway.write("update", project["id"], {"dueDate": None}, first)
    actual = await gateway.get(project["id"], first)
    checks.append(
        {
            "check": "clear_native_due_date",
            "pass": not clear.error and actual.get("dueDate") is None,
        }
    )
    completed = await gateway.write("complete", project["id"], {}, first)
    actual = await gateway.get(project["id"], first)
    checks.append(
        {
            "check": "complete_and_readback",
            "pass": not completed.error and actual.get("status") == 2,
        }
    )
    for check in checks:
        print(json.dumps(check))
    (private / "live-update-verified.json").write_text(
        json.dumps(checks, indent=2), encoding="utf-8"
    )
    if not all(x["pass"] for x in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
