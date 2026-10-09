"""Read-only comparison of synthetic acceptance notes across fixed CLI readers."""

import asyncio
import difflib
import json
import re
import sqlite3
from pathlib import Path

from notido.cli import CLIRunner, DidaGateway
from notido.keys import key
from tools.container_smoke import ROOT


async def main():
    state = json.loads(
        (ROOT / "runtime-data/live-restore-results.json").read_text(encoding="utf-8")
    )
    target = Path(state["restore_root"])
    with sqlite3.connect((target / "notido.db").as_uri() + "?mode=ro", uri=True) as db:
        changed = next(
            row for row in state["review"]["history"] if row["verification"] == "external_change"
        )
        plan = json.loads(
            db.execute(
                "SELECT plan FROM operations WHERE id=?", (changed["operation_id"],)
            ).fetchone()[0]
        )
    project = json.loads((ROOT / "runtime-data/test-project.json").read_text(encoding="utf-8-sig"))
    if plan["project_id"] != project["id"] or "ND-V" not in plan["fields"].get("content", ""):
        raise RuntimeError("synthetic acceptance target required")
    runner = CLIRunner(
        r"D:\Program Files\nodejs\node.exe",
        str(ROOT / "node_modules/@suibiji/dida-cli/dist/index.js"),
        target / "cli-home",
    )
    attachment = CLIRunner(
        r"D:\Program Files\nodejs\node.exe",
        str(ROOT / "tools/attachment-cli.mjs"),
        target / "cli-home",
    )
    current, web = await asyncio.gather(
        DidaGateway(runner).get(plan["project_id"], changed["remote_id"]),
        attachment.call(
            ["task-get", f"--project={plan['project_id']}", f"--task={changed['remote_id']}"],
            envelope=True,
        ),
    )
    if web.error:
        raise RuntimeError(web.error)
    expected = plan["fields"].get("content", "")
    actual = current.get("content") or ""

    def strip_comments(value):
        return re.sub(r"<!--.*?-->", "", value, flags=re.S).strip()

    print(
        json.dumps(
            {
                "expected_length": len(expected),
                "actual_length": len(actual),
                "expected_hash": key(expected),
                "actual_hash": key(actual),
                "changes": [
                    {"kind": tag, "removed": expected[a:b][:160], "inserted": actual[c:d][:160]}
                    for tag, a, b, c, d in difflib.SequenceMatcher(
                        None, expected, actual
                    ).get_opcodes()
                    if tag != "equal"
                ],
                "equal_without_comments": strip_comments(expected) == strip_comments(actual),
                "equal_without_whitespace": re.sub(r"\s", "", expected)
                == re.sub(r"\s", "", actual),
                "web_task_keys": sorted(web.value),
                "web_content_length": len(web.value.get("content") or ""),
                "web_desc_matches_expected": web.value.get("desc") == expected,
                "web_desc_length": len(web.value.get("desc") or ""),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
