"""Prepare a separate acceptance runtime with an empty NotiDo business ledger.

Copies the authorized local framework and account setup, never prints secrets,
never alters the original runtime, and leaves all remote tasks untouched.
"""

import argparse
import asyncio
import json
import os
import shutil
import sqlite3

from notido.db import Database, execute
from notido.models import Settings
from tools.container_smoke import ROOT


def isolate_framework_config(config):
    config = json.loads(json.dumps(config))
    for platform in config.get("platform", []):
        platform["enable"] = False
    return config


async def main(batch="v3"):
    source = ROOT / "runtime-data/astrbot-acceptance"
    if batch not in ("v3", "v4", "v5", "v6", "v7"):
        raise RuntimeError("known isolated batch required")
    target = ROOT / f"runtime-data/astrbot-native-{batch}"
    marker = target / "notido-fresh-setup.json"
    if marker.exists():
        print(json.dumps({"prepared": True, "reused_existing_setup": True}))
        return
    if target.exists():
        raise RuntimeError("incomplete setup exists; inspect without overwriting")
    project = json.loads((ROOT / "runtime-data/test-project.json").read_text(encoding="utf-8-sig"))
    plugin = source / "plugin_data/astrbot_plugin_notido"
    with sqlite3.connect(f"file:{(plugin / 'notido.db').as_posix()}?mode=ro", uri=True) as conn:
        settings = Settings.model_validate_json(
            conn.execute("SELECT payload FROM settings WHERE id='main'").fetchone()[0]
        )
        scope = conn.execute(
            "SELECT * FROM account_scopes WHERE id=?", (settings.account_ref,)
        ).fetchone()
    if (
        project["name"] != "NotiDo 验收"
        or settings.allowed_projects != [project["id"]]
        or not scope
    ):
        raise RuntimeError("same authorized dedicated account/list scope required")
    with sqlite3.connect(f"file:{(source / 'data_v4.db').as_posix()}?mode=ro", uri=True) as conn:
        if conn.execute("SELECT count(*) FROM cron_jobs").fetchone()[0]:
            raise RuntimeError(
                "scheduled jobs cannot be cloned into the isolated acceptance runtime"
            )
    target.mkdir()
    for relative in ("cmd_config.json", "config/astrbot_plugin_notido_config.json"):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
        os.chmod(destination, 0o600)
    framework = target / "cmd_config.json"
    framework.write_text(
        json.dumps(
            isolate_framework_config(json.loads(framework.read_text(encoding="utf-8-sig"))),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    # Online SQLite backup gives a consistent snapshot, including credentials
    # and native personas/preferences. New corpus sessions never select old chats.
    with sqlite3.connect(
        f"file:{(source / 'data_v4.db').as_posix()}?mode=ro", uri=True
    ) as original:
        with sqlite3.connect(target / "data_v4.db") as copied:
            original.backup(copied)
    config = json.loads(
        (target / "config/astrbot_plugin_notido_config.json").read_text(encoding="utf-8-sig")
    )
    config["data_root"] = "/AstrBot/data/plugin_data/astrbot_plugin_notido"
    isolated_config = target / "config/astrbot_plugin_notido_config.json"
    isolated_config.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(isolated_config, 0o600)
    destination_plugin = target / "plugin_data/astrbot_plugin_notido"
    destination_plugin.mkdir(parents=True)
    auth_source = plugin / "cli-home"
    for relative in (".config/dida-cli/config.json", ".config/notido-attachments/config.json"):
        destination = destination_plugin / "cli-home" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(auth_source / relative, destination)
        os.chmod(destination, 0o600)
    database = Database(destination_plugin)
    try:
        await database.initialize()
        async with database.transaction() as conn:
            await execute(
                conn,
                "INSERT INTO account_scopes VALUES (:id,:user,:region,:hint,:gen,:state,:created)",
                dict(
                    zip(
                        ("id", "user", "region", "hint", "gen", "state", "created"),
                        scope,
                        strict=True,
                    )
                ),
            )
            await execute(conn, "UPDATE settings SET payload=:p", {"p": settings.model_dump_json()})
        if await database.read("SELECT id FROM operations LIMIT 1"):
            raise RuntimeError("fresh native acceptance ledger must be empty")
    finally:
        await database.close()
    marker.write_text(
        json.dumps(
            {
                "prepared": True,
                "scope": "same authorized dedicated account/list",
                "business_ledger": "empty",
                "remote_changes": False,
            }
        ),
        encoding="utf-8",
    )
    os.chmod(marker, 0o600)
    print(json.dumps({"prepared": True, "business_ledger_empty": True, "remote_changes": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", choices=["v3", "v4", "v5", "v6", "v7"], default="v3")
    asyncio.run(main(parser.parse_args().batch))
