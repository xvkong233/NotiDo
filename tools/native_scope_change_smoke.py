"""Real native history plus local pending fixtures and production configuration APIs.

Uses separate copies of the latest verified backup, never the live instance.
Pending rows are explicitly fixtures; gateway write/upload are disabled and must
not even be attempted. No Provider calls or remote changes are permitted.
"""

import asyncio
import json
import time
import uuid
from pathlib import Path

from notido.api import PagesAPI
from notido.cli import CLIRunner, DidaGateway
from notido.db import execute
from notido.keys import key
from notido.service import Service
from tools.backup import restore
from tools.container_smoke import ROOT
from tools.live_restore_smoke import NoProvider, ReadOnlyGateway, request
from tools.live_same_name_smoke import save

JOURNAL = ROOT / "runtime-data/native-scope-change-results.json"


async def main():
    source = json.loads(
        (ROOT / "runtime-data/native-outcome-restore-v7-r2-results.json").read_text("utf-8")
    )
    if not source.get("confirmed") or not all(check["pass"] for check in source["checks"]):
        raise RuntimeError("current fully verified backup required")
    state = (
        json.loads(JOURNAL.read_text("utf-8"))
        if JOURNAL.exists()
        else {"source_runtime_version": source["source_runtime_version"], "cases": {}}
    )
    username = json.loads((ROOT / "runtime-data/acceptance-webui.json").read_text())["username"]
    for mode in ("binding", "projects", "authorization"):
        record = state["cases"].setdefault(mode, {"phase": "ready"})
        if record["phase"] == "verified":
            continue
        if record["phase"] != "ready":
            raise RuntimeError("inspect existing isolated fixture; don't repeat mutation")
        target = (
            Path(record["root"])
            if record.get("root")
            else ROOT / "runtime-data" / ("native-scope-" + uuid.uuid4().hex)
        )
        record["root"] = str(target)
        save(JOURNAL, state)
        if not target.exists():
            restore(Path(source["backup_root"]), target)
        home = target / "cli-home"
        gateway = ReadOnlyGateway(
            DidaGateway(
                CLIRunner(
                    r"D:\Program Files\nodejs\node.exe",
                    str(ROOT / "node_modules/@suibiji/dida-cli/dist/index.js"),
                    home,
                )
            )
        )
        bridge = NoProvider()
        service = Service(target, bridge, gateway)
        service.owner_lock.acquire(timeout=0)
        await service.db.initialize()
        try:
            original = (
                await service.db.read(
                    "SELECT * FROM operations WHERE kind='create' AND state='succeeded' AND json_extract(plan,'$.delivery_mode')='framework_tool' ORDER BY created_at DESC LIMIT 1"
                )
            )[0]
            plan = json.loads(original["plan"])
            message = (
                await service.db.read(
                    "SELECT envelope FROM message_records WHERE session_key=:s ORDER BY received_at DESC LIMIT 1",
                    {"s": plan["session_key"]},
                )
            )[0]
            envelope = json.loads(message["envelope"])
            if bridge.instance_id != plan["framework_instance_id"] or (
                key(bridge.instance_id, "webchat", username) != plan["actor_key"]
                or key(bridge.instance_id, envelope["reply_origin_ref"]) != plan["session_key"]
            ):
                raise RuntimeError("actual native actor/session differs from fixture scope")
            identifier = str(uuid.uuid4())
            async with service.db.transaction() as conn:
                await execute(
                    conn,
                    "INSERT INTO operations SELECT :id,user_id,account_ref,NULL,kind,:key,plan,'validated',0,NULL,NULL,0,0,:t,NULL FROM operations WHERE id=:source",
                    {
                        "id": identifier,
                        "key": "scope-fixture:" + identifier,
                        "source": original["id"],
                        "t": time.time(),
                    },
                )
            record.update(
                {
                    "phase": "mutation_sent",
                    "fixture_operation": identifier,
                    "original_operation": original["id"],
                    "fixture": True,
                }
            )
            save(JOURNAL, state)
            api = PagesAPI(service)
            settings, revision = await service.db.settings()
            if mode == "binding":
                await api.bind(
                    request(
                        revision,
                        platform_id="webchat",
                        actor_id=username,
                        origin=envelope["reply_origin_ref"],
                        enabled=False,
                    )
                )
            elif mode == "projects":
                public = settings.model_dump(exclude={"identity", "provider_id"})
                public.update(default_project=None, allowed_projects=[])
                await api.save_settings(request(revision, settings=public))
            else:
                await api.clear_auth(request(revision, confirm="清除当前授权"))
                if any(
                    (home / p).exists()
                    for p in (
                        ".config/dida-cli/config.json",
                        ".config/notido-attachments/config.json",
                    )
                ):
                    raise RuntimeError("isolated authorization files weren't cleared")
            await service.execute_operation({"operation_id": identifier})
            row = (
                await service.db.read("SELECT * FROM operations WHERE id=:id", {"id": identifier})
            )[0]
            if not row["paused"] or row["attempt"] != 0 or row["remote_id"] is not None:
                raise RuntimeError("configuration API didn't pause the pending native fixture")
            # Exercise the execution guard even if a stale worker had observed
            # the row before the API pause. This is local fixture fault injection.
            async with service.db.transaction() as conn:
                await execute(
                    conn, "UPDATE operations SET paused=0 WHERE id=:id", {"id": identifier}
                )
            await service.execute_operation({"operation_id": identifier})
            row = (
                await service.db.read("SELECT * FROM operations WHERE id=:id", {"id": identifier})
            )[0]
            expected = {
                "binding": "CONFIG_OR_AUTHORIZATION_CHANGED",
                "projects": "PROJECT_NOT_ALLOWED",
                "authorization": "ACCOUNT_CHANGED",
            }[mode]
            code = json.loads(row["result"])["error"]["code"]
            if (
                not row["paused"]
                or row["attempt"] != 0
                or code != expected
                or gateway.writes
                or bridge.calls
            ):
                raise RuntimeError("stale native operation reached a side-effect boundary")
            record.update(
                {
                    "phase": "verified",
                    "pass": True,
                    "paused": True,
                    "attempt": 0,
                    "error": code,
                    "remote_write_attempts": gateway.writes,
                    "provider_calls": bridge.calls,
                }
            )
            save(JOURNAL, state)
            print(json.dumps({"case": mode, "pass": True, "remote_write_attempts": 0}), flush=True)
        finally:
            await service.db.close()
            service.owner_lock.release()


if __name__ == "__main__":
    asyncio.run(main())
