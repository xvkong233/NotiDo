import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from notido.db import Database, execute
from notido.errors import NotiDoError


def old_database(root):
    root.mkdir()
    with closing(sqlite3.connect(root / "notido.db")) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,applied_at REAL NOT NULL)"
        )
        for path in sorted((Path(__file__).parents[1] / "notido/migrations").glob("*.sql"))[:7]:
            script = path.read_text(encoding="utf-8")
            conn.executescript(script)
            conn.execute(
                "INSERT INTO schema_migrations VALUES (?,?,0)",
                (int(path.name[:3]), hashlib.sha256(script.encode()).hexdigest()),
            )
        conn.execute("INSERT INTO users VALUES ('personal','{}',0)")
        conn.execute(
            "INSERT INTO account_scopes VALUES ('account','personal','cn',NULL,1,'active',0)"
        )
        conn.execute("INSERT INTO sessions(id,user_id) VALUES ('session','personal')")
        conn.execute(
            "INSERT INTO operations VALUES ('op','personal','account',NULL,'create','key','{\"kind\":\"create\"}','succeeded',0,'task','{}',1,0,0,0)"
        )
        conn.execute(
            "INSERT INTO native_tool_calls VALUES ('call','session','message','create','request','fingerprint','op',0)"
        )
        conn.execute(
            "INSERT INTO receipt_records VALUES ('receipt','personal','op','dedupe','origin','saved','sent',1,0,0)"
        )
        conn.commit()


async def test_existing_seven_upgrade_preserves_history_foreign_keys_and_triggers(tmp_path):
    root = tmp_path / "old"
    old_database(root)
    db = Database(root)
    try:
        await db.initialize()
        assert len(await db.read("SELECT * FROM schema_migrations")) == 10
        assert not await db.read("PRAGMA foreign_key_check")
        assert (await db.read("PRAGMA foreign_keys"))[0]["foreign_keys"] == 1
        assert (await db.read("SELECT * FROM native_tool_calls"))[0]["operation_id"] == "op"
        assert (await db.read("SELECT * FROM receipt_records"))[0]["body"] == "saved"
        assert len(await db.read("SELECT * FROM operation_plan_versions")) == 1
        async with db.transaction() as conn:
            await execute(
                conn,
                "UPDATE operations SET plan=:plan WHERE id='op'",
                {"plan": '{"kind":"create","revision":1}'},
            )
            await execute(
                conn,
                "INSERT INTO operations SELECT 'delete','personal','account',NULL,'delete','delete-key','{\"kind\":\"delete\"}','validated',0,'task',NULL,0,0,0,NULL",
            )
        assert len(await db.read("SELECT * FROM operation_plan_versions")) == 3
    finally:
        await db.close()


async def test_invalid_existing_fk_rolls_back_whole_migration(tmp_path):
    root = tmp_path / "old"
    old_database(root)
    with closing(sqlite3.connect(root / "notido.db")) as conn:
        conn.execute("UPDATE native_tool_calls SET operation_id='missing'")
        conn.commit()
    db = Database(root)
    try:
        with pytest.raises(NotiDoError) as caught:
            await db.initialize()
        assert caught.value.code == "MIGRATION_FOREIGN_KEY_FAILED"
        assert len(await db.read("SELECT * FROM schema_migrations")) == 7
        assert not await db.read(
            "SELECT name FROM sqlite_master WHERE name IN ('operations_new','delete_confirmations')"
        )
        assert (await db.read("SELECT sql FROM sqlite_master WHERE name='operations'"))[0][
            "sql"
        ].find("'delete'") == -1
        assert len(await db.read("SELECT * FROM operation_plan_versions")) == 1
        assert (await db.read("PRAGMA foreign_keys"))[0]["foreign_keys"] == 1
    finally:
        await db.close()
