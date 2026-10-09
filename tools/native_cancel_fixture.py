"""Stage a paused synthetic local operation from an actual native test origin.

Called only by opted-in acceptance scripts. No worker is started and no Gateway
exists. The queued operation and paused flag commit atomically, so it cannot be
claimed between staging and the native cancellation test.
"""

import asyncio
import json
import sys
from pathlib import Path

from notido.db import execute, one
from notido.keys import key
from notido.service import Service

DATA_ROOT = Path("/AstrBot/data/plugin_data/astrbot_plugin_notido")


async def stage(payload):
    class Bridge:
        instance_id = "notido-cancel-fixture"

    service = Service(DATA_ROOT, Bridge(), None)
    service.native_ai = True
    try:
        settings, revision = await service.db.settings()
        saved = (
            await service.db.read(
                "SELECT * FROM operations WHERE id=:id", {"id": payload["saved_operation_id"]}
            )
        )[0]
        origin = json.loads(saved["plan"])
        if (
            saved["state"] != "succeeded"
            or saved["kind"] != "create"
            or saved["account_ref"] != settings.account_ref
            or origin.get("delivery_mode") != "framework_tool"
            or origin["project_id"] not in settings.allowed_projects
            or not origin["fields"]["title"].startswith("NotiDo 验收 · D19")
        ):
            raise RuntimeError("actual verified D19 fixture origin required")
        action_id = key("native-cancel-fixture", saved["id"], payload["request_id"])
        plan = service.base_plan(
            {"id": origin["group_id"], "session_id": origin["session_key"]},
            origin["origin"],
            settings,
            revision,
        )
        plan.update(
            {
                "kind": "create",
                "project_id": origin["project_id"],
                "project_name": "NotiDo 验收",
                "fields": {"title": "NotiDo 验收 · D19 · 尚未开始本地操作"},
                "action_id": action_id,
                "delivery_mode": "framework_tool",
                "ai_owner": "astrbot",
                "fixture": "D19 paused local cancellation",
            }
        )
        async with service.db.transaction() as conn:
            await execute(
                conn,
                "INSERT OR IGNORE INTO action_items VALUES (:id,'personal',NULL,:id,0)",
                {"id": action_id},
            )
            await execute(
                conn,
                "INSERT OR IGNORE INTO action_item_versions VALUES (:id,:id,0,:p,0)",
                {"id": action_id, "p": json.dumps(plan)},
            )
            operation_id = await service.persist_operation(conn, plan)
            await execute(
                conn,
                "UPDATE operations SET paused=1 WHERE id=:id AND state='validated' AND attempt=0 AND remote_id IS NULL",
                {"id": operation_id},
            )
            actual = await one(
                conn,
                "SELECT id,state,paused,attempt,remote_id FROM operations WHERE id=:id",
                {"id": operation_id},
            )
        if (
            actual["state"] != "validated"
            or not actual["paused"]
            or actual["attempt"] != 0
            or actual["remote_id"] is not None
        ):
            raise RuntimeError("fixture already changed; never recreate or replay")
        print(json.dumps(actual))
    finally:
        await service.db.close()


if __name__ == "__main__":
    asyncio.run(stage(json.load(sys.stdin)))
