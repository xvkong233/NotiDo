import hashlib
import json
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine

from .errors import NotiDoError
from .keys import canonical, uid
from .models import Settings


async def rows(conn, sql: str, params=None):
    return [dict(x) for x in (await conn.execute(text(sql), params or {})).mappings()]


async def one(conn, sql: str, params=None):
    result = await rows(conn, sql, params)
    return result[0] if result else None


async def execute(conn, sql: str, params=None):
    return await conn.execute(text(sql), params or {})


class Database:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{root / 'notido.db'}")

        @event.listens_for(self.engine.sync_engine, "connect")
        def configure(connection, _):
            cursor = connection.cursor()
            for pragma in (
                "foreign_keys=ON",
                "journal_mode=WAL",
                "busy_timeout=5000",
                "synchronous=FULL",
                "secure_delete=ON",
            ):
                cursor.execute(f"PRAGMA {pragma}")
            cursor.close()

    @asynccontextmanager
    async def transaction(self):
        async with self.engine.connect() as conn:
            await execute(conn, "BEGIN IMMEDIATE")
            try:
                yield conn
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise

    async def initialize(self):
        migrations = sorted((Path(__file__).parent / "migrations").glob("[0-9]*.sql"))
        async with self.migration_transaction() as conn:
            await execute(
                conn,
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at REAL NOT NULL)",
            )
            existing = await rows(conn, "SELECT * FROM schema_migrations")
            if any(x["version"] > len(migrations) for x in existing):
                raise NotiDoError(
                    "SCHEMA_TOO_NEW", "数据库版本高于当前插件，请升级插件。", status=503
                )
            for path in migrations:
                version = int(path.name.split("_")[0])
                script = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(script.encode()).hexdigest()
                old = next((x for x in existing if x["version"] == version), None)
                if old:
                    if old["checksum"] != checksum:
                        raise NotiDoError(
                            "MIGRATION_CHANGED", "迁移校验失败，进入维护状态。", status=503
                        )
                    continue
                statement = ""
                for fragment in script.split(";")[:-1]:
                    statement += fragment + ";"
                    if sqlite3.complete_statement(statement):
                        await execute(conn, statement)
                        statement = ""
                if statement.strip() or script.split(";")[-1].strip():
                    raise NotiDoError(
                        "MIGRATION_INVALID", "迁移含未闭合语句，进入维护状态。", status=503
                    )
                await execute(
                    conn,
                    "INSERT INTO schema_migrations VALUES (:v,:c,:t)",
                    {"v": version, "c": checksum, "t": time.time()},
                )
            await execute(
                conn, "INSERT OR IGNORE INTO users VALUES ('personal',:i,0)", {"i": canonical({})}
            )
            await execute(
                conn,
                "INSERT OR IGNORE INTO settings VALUES ('main',:p,0)",
                {"p": Settings().model_dump_json()},
            )
            await execute(
                conn,
                "INSERT OR IGNORE INTO source_registry VALUES ('website','disabled',:p)",
                {"p": canonical({"code": "SOURCE_NOT_IMPLEMENTED"})},
            )
            await execute(
                conn,
                "UPDATE operations SET state='outcome_unknown',revision=revision+1 WHERE state='executing'",
            )
            await execute(
                conn,
                "UPDATE jobs SET state='pending',owner=NULL WHERE state='running' AND kind IN ('read_materials','close_group','parse_group','reconcile')",
            )
            # A crash after job claim but before operation claim has no remote effect.
            # Only validated operations can reuse the execution job; executing became unknown above.
            await execute(
                conn,
                "UPDATE jobs SET state='pending',owner=NULL WHERE state='running' AND kind='execute_operation' AND json_extract(payload,'$.operation_id') IN (SELECT id FROM operations WHERE state='validated')",
            )
            # Reply may already have been sent; no automatic replay after an ambiguous crash.
            await execute(
                conn,
                "UPDATE receipt_records SET state='unknown',revision=revision+1 WHERE id IN (SELECT json_extract(payload,'$.receipt_id') FROM jobs WHERE state='running' AND kind='send_receipt')",
            )
            await execute(
                conn,
                "UPDATE jobs SET state='failed',error='RESTART_REQUIRES_RECONCILIATION' WHERE state='running'",
            )
            await execute(
                conn,
                "UPDATE assets SET state='unavailable',error='MATERIAL_RESEND_REQUIRED' WHERE state='pending'",
            )

    @asynccontextmanager
    async def migration_transaction(self):
        # SQLite requires FK enforcement off before BEGIN when rebuilding a CHECK
        # constraint. This owner-only startup transaction checks every FK before commit.
        async with self.engine.connect() as conn:
            await execute(conn, "PRAGMA foreign_keys=OFF")
            await conn.commit()
            try:
                await execute(conn, "BEGIN IMMEDIATE")
                yield conn
                if await rows(conn, "PRAGMA foreign_key_check"):
                    raise NotiDoError(
                        "MIGRATION_FOREIGN_KEY_FAILED",
                        "迁移后外键核验失败，保留旧库并进入维护。",
                        status=503,
                    )
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise
            finally:
                if conn.in_transaction():
                    await conn.rollback()
                await execute(conn, "PRAGMA foreign_keys=ON")
                await conn.commit()

    async def read(self, sql, params=None):
        async with self.engine.connect() as conn:
            return await rows(conn, sql, params)

    async def settings(self):
        row = (await self.read("SELECT * FROM settings WHERE id='main'"))[0]
        return Settings.model_validate_json(row["payload"]), row["revision"]

    async def job(
        self, conn, kind, dedupe, payload, *, priority=10, available_at=None, defer=False
    ):
        existing = await one(conn, "SELECT id FROM jobs WHERE dedupe_key=:k", {"k": dedupe})
        if existing:
            return existing["id"]
        if (
            kind in ("read_materials", "parse_group")
            and payload.get("group_id")
            and "resume_id" not in payload
        ):
            hold = await one(
                conn,
                "SELECT resume_id FROM restored_group_holds WHERE group_id=:g AND released_at IS NOT NULL",
                {"g": payload["group_id"]},
            )
            if hold:
                payload = {**payload, "resume_id": hold["resume_id"]}
        active = await one(
            conn, "SELECT count(*) AS n FROM jobs WHERE state IN ('pending','running')"
        )
        limit = Settings.model_validate_json(
            (await one(conn, "SELECT payload FROM settings WHERE id='main'"))["payload"]
        ).max_jobs
        # Keep priority slots even when configured below the default capacity.
        reserve = min(10, max(2, limit // 5))
        if active["n"] >= (limit if priority <= 2 else limit - reserve):
            if defer:
                return None
            raise NotiDoError(
                "QUEUE_FULL", "处理队列已满，请稍后重发。", status=429, retryable=True
            )
        await execute(
            conn,
            "INSERT OR IGNORE INTO jobs (id,kind,dedupe_key,payload,state,priority,available_at,created_at) VALUES (:id,:k,:d,:p,'pending',:pri,:a,:t)",
            {
                "id": uid(),
                "k": kind,
                "d": dedupe,
                "p": canonical(payload),
                "pri": priority,
                "a": available_at or time.time(),
                "t": time.time(),
            },
        )

    async def receipt(self, conn, *, origin, body, dedupe, operation_id=None):
        receipt_id = uid()
        result = await execute(
            conn,
            "INSERT OR IGNORE INTO receipt_records (id,user_id,operation_id,dedupe_key,origin,body,state,created_at) VALUES (:id,'personal',:op,:d,:o,:b,'pending',:t)",
            {
                "id": receipt_id,
                "op": operation_id,
                "d": dedupe,
                "o": origin,
                "b": body,
                "t": time.time(),
            },
        )
        if result.rowcount:
            await self.job(
                conn,
                "send_receipt",
                f"receipt:{receipt_id}",
                {"receipt_id": receipt_id},
                priority=1,
                defer=True,
            )

    async def api_mutate(self, endpoint, target, request_id, expected_revision, payload, mutation):
        from .keys import key

        fingerprint = key(payload)
        async with self.transaction() as conn:
            previous = await one(
                conn,
                "SELECT * FROM api_requests WHERE user_id='personal' AND endpoint=:e AND target=:t AND request_id=:r",
                {"e": endpoint, "t": target, "r": request_id},
            )
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise NotiDoError("REQUEST_ID_REUSED", "同一请求编号不能用于不同内容。")
                return json.loads(previous["response"])
            response = await mutation(conn, expected_revision)
            await execute(
                conn,
                "INSERT INTO api_requests VALUES (:id,'personal',:e,:t,:r,:f,:p,:now)",
                {
                    "id": uid(),
                    "e": endpoint,
                    "t": target,
                    "r": request_id,
                    "f": fingerprint,
                    "p": canonical(response),
                    "now": time.time(),
                },
            )
            return response

    async def close(self):
        await self.engine.dispose()
