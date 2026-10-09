"""Read-only restore review with explicit, durable maintainer acknowledgement."""

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime

from .db import execute, one
from .errors import NotiDoError
from .keys import canonical, key, uid
from .models import Query, Settings
from .query import read_scope


class RestoreReview:
    def __init__(self, service):
        self.service, self.db = service, service.db

    def marker_key(self):
        marker = self.service.root / "restore-review.required"
        if not marker.is_file():
            return None
        return key(marker.read_bytes().hex(), marker.stat().st_mtime_ns)

    async def history_fingerprint(self, account_ref, conn=None):
        if conn is None:
            async with self.db.engine.connect() as connection:
                return await self.history_fingerprint(account_ref, connection)
        from sqlalchemy import text

        digest = hashlib.sha256(b"restore-history-v1\n")
        result = await conn.stream(
            text(
                "SELECT id,revision,state,remote_id FROM operations WHERE account_ref=:a ORDER BY id"
            ),
            {"a": account_ref},
        )
        async for partition in result.mappings().partitions(500):
            for row in partition:
                digest.update(canonical(dict(row)).encode("utf-8") + b"\n")
        return digest.hexdigest()

    async def clear_confirmed_marker(self):
        marker_key = self.marker_key()
        if marker_key is None or self.service.maintenance != "RESTORE_REMOTE_REVIEW_REQUIRED":
            return False
        _, revision = await self.db.settings()
        approvals = await self.db.read(
            "SELECT id FROM restore_reviews WHERE marker_key=:m AND state='confirmed' AND config_revision=:r ORDER BY confirmed_at DESC LIMIT 1",
            {"m": marker_key, "r": revision},
        )
        if not approvals:
            return False
        # The durable acknowledgement precedes marker removal. A crash cannot erase the gate
        # before the review is committed; removal failure stays in maintenance until retried.
        (self.service.root / "restore-review.required").unlink()
        if self.service.maintenance == "RESTORE_REMOTE_REVIEW_REQUIRED":
            self.service.maintenance = None
        return True

    async def status(self):
        marker_key = self.marker_key()
        reviews = (
            await self.db.read(
                "SELECT id,state,next_cursor,config_revision,checked_at,revision,payload FROM restore_reviews WHERE marker_key=:m ORDER BY checked_at DESC,id DESC LIMIT 1",
                {"m": marker_key},
            )
            if marker_key
            else []
        )
        return {"required": bool(marker_key), "review": reviews[0] if reviews else None}

    async def previous(self, endpoint, body):
        records = await self.db.read(
            "SELECT * FROM api_requests WHERE endpoint=:e AND target='main' AND request_id=:r",
            {"e": endpoint, "r": body["request_id"]},
        )
        if records:
            if records[0]["fingerprint"] != key(body):
                raise NotiDoError("REQUEST_ID_REUSED", "同一请求编号不能用于不同内容。")
            return json.loads(records[0]["response"])
        return None

    async def check(self, api, body):
        existing = await self.previous("recovery/check", body)
        if existing is not None:
            return existing
        async with self.service.write_lock:
            marker_key = self.marker_key()
            if not marker_key or self.service.maintenance != "RESTORE_REMOTE_REVIEW_REQUIRED":
                raise NotiDoError(
                    "RESTORE_REVIEW_NOT_REQUIRED", "当前没有可复核的恢复门槛；授权维护须先处理。"
                )
            settings, revision = await self.db.settings()
            if revision != body["expected_revision"]:
                raise NotiDoError("REVISION_CONFLICT", "配置已变更，请刷新后重新复核。")
            if not settings.account_ref or not settings.allowed_projects:
                raise NotiDoError("RESTORE_SCOPE_REQUIRED", "请先授权原账号并选择需复核的清单。")
            review_id = body.get("review_id") or uid()
            previous = None
            if body.get("review_id"):
                records = await self.db.read(
                    "SELECT * FROM restore_reviews WHERE id=:id", {"id": review_id}
                )
                previous = records[0] if records else None
                if (
                    not previous
                    or previous["state"] != "checking"
                    or previous["marker_key"] != marker_key
                    or previous["config_revision"] != revision
                    or previous["account_ref"] != settings.account_ref
                ):
                    raise NotiDoError(
                        "RESTORE_REVIEW_STALE", "复核不完整或配置已变更，请开始新的复核。"
                    )
                payload = json.loads(previous["payload"])
                cursor = previous["next_cursor"]
            else:
                payload = {
                    "scope": {},
                    "history": [],
                    "history_fingerprint": None,
                    "manual_review_required": True,
                }
                cursor = ""
            try:
                async with asyncio.timeout(settings.time_budgets.long_seconds):
                    if previous is None:
                        snapshot = await read_scope(
                            self.service.gateway, settings, datetime.now(UTC), Query()
                        )
                        if not snapshot["complete"]:
                            raise NotiDoError(
                                "RESTORE_REMOTE_READ_INCOMPLETE",
                                "允许清单读取不完整，不能解除维护。",
                            )
                        payload["scope"] = {
                            "projects": snapshot["per_project_complete"],
                            "active_tasks": len(snapshot["tasks"]),
                            "tasks": [
                                {
                                    k: t.get(k)
                                    for k in (
                                        "id",
                                        "projectId",
                                        "title",
                                        "dueDate",
                                        "isAllDay",
                                        "status",
                                    )
                                }
                                for t in snapshot["tasks"]
                            ],
                        }
                    fingerprint = await self.history_fingerprint(settings.account_ref)
                    if previous and fingerprint != payload["history_fingerprint"]:
                        raise NotiDoError(
                            "RESTORE_REVIEW_STALE", "本地账本在复核期间发生变化，请开始新的复核。"
                        )
                    payload["history_fingerprint"] = fingerprint
                    pending = await self.db.read(
                        "SELECT id,revision,state,remote_id,account_ref,plan,created_at FROM operations WHERE account_ref=:a AND id>:c ORDER BY id LIMIT 21",
                        {"a": settings.account_ref, "c": cursor},
                    )
                    batch = pending[:20]
                    for record in batch:
                        plan = json.loads(record["plan"])
                        item = {
                            "operation_id": record["id"],
                            "state": record["state"],
                            "remote_id": record["remote_id"],
                            "kind": plan["kind"],
                        }
                        if record["state"] in ("validated", "cancelled", "failed_safe"):
                            item["verification"] = "no_replay"
                        elif plan["project_id"] not in settings.allowed_projects:
                            item["verification"] = "outside_allowed_scope"
                        elif not record["remote_id"]:
                            item["verification"] = "unknown_without_reliable_id"
                        else:
                            try:
                                task_id = (
                                    plan.get("task_id")
                                    if plan["kind"] == "upload"
                                    else record["remote_id"]
                                )
                                deletions = await self.db.read(
                                    "SELECT id FROM operations WHERE kind='delete' AND state='succeeded' AND account_ref=:a AND remote_id=:t AND json_extract(plan,'$.project_id')=:p AND created_at>=:since ORDER BY created_at DESC LIMIT 1",
                                    {
                                        "a": settings.account_ref,
                                        "t": task_id,
                                        "p": plan["project_id"],
                                        "since": record["created_at"],
                                    },
                                )
                                if plan["kind"] == "delete" or deletions:
                                    probe = await self.service.gateway.probe_task(
                                        plan["project_id"], task_id
                                    )
                                    if plan["kind"] == "delete":
                                        item["verification"] = (
                                            "matches" if not probe["exists"] else "external_change"
                                        )
                                    else:
                                        item["verification"] = (
                                            "deleted_by_confirmed_operation"
                                            if not probe["exists"]
                                            else "external_change"
                                        )
                                        item["deletion_operation_id"] = deletions[0]["id"]
                                    item["current"] = {**probe, "deleted": not probe["exists"]}
                                    payload["history"].append(item)
                                    continue
                                actual = (
                                    await self.service.gateway.inspect_upload(
                                        plan, record["remote_id"]
                                    )
                                    if plan["kind"] == "upload"
                                    else await self.service.gateway.get(
                                        plan["project_id"], record["remote_id"]
                                    )
                                )
                                if plan["kind"] == "upload":
                                    matches = (
                                        actual.get("sha256") == plan["hash"]
                                        and actual.get("task_id") == plan["task_id"]
                                    )
                                elif plan["kind"] == "complete":
                                    matches = actual.get("status") == 2
                                else:
                                    matches = self.service.fields_match(actual, plan["fields"])
                                item["verification"] = "matches" if matches else "external_change"
                                item["current"] = {
                                    k: actual.get(k)
                                    for k in (
                                        "id",
                                        "title",
                                        "dueDate",
                                        "isAllDay",
                                        "status",
                                        "sha256",
                                        "task_id",
                                    )
                                    if k in actual
                                }
                                if not matches:
                                    item["differences"] = {
                                        field: {
                                            "planned": key(value) if field == "content" else value,
                                            "current": key(actual.get(field))
                                            if field == "content"
                                            else actual.get(field),
                                        }
                                        for field, value in plan["fields"].items()
                                        if not self.service.fields_match(actual, {field: value})
                                    }
                            except NotiDoError as exc:
                                raise NotiDoError(
                                    "RESTORE_REMOTE_READ_INCOMPLETE",
                                    "历史目标读取未完成，请先核查原目标；不会按查不到重建。",
                                    details={"operation_id": record["id"], "reason": exc.code},
                                ) from exc
                        payload["history"].append(item)
                    payload["other_account_operations"] = (
                        await self.db.read(
                            "SELECT count(*) AS n FROM operations WHERE account_ref!=:a",
                            {"a": settings.account_ref},
                        )
                    )[0]["n"]
                    payload["held_groups"] = (
                        await self.db.read(
                            "SELECT count(*) AS n FROM restored_group_holds WHERE released_at IS NULL"
                        )
                    )[0]["n"]
                    next_cursor = batch[-1]["id"] if len(pending) > len(batch) else None
            except TimeoutError as exc:
                raise NotiDoError(
                    "RESTORE_READ_TIMEOUT", "复核读取预算已用完，未解除维护；请重试当前批次。"
                ) from exc

            async def save(conn, expected):
                current = await one(conn, "SELECT * FROM settings WHERE id='main'")
                api.revision(current, expected)
                if self.marker_key() != marker_key:
                    raise NotiDoError("RESTORE_REVIEW_STALE", "恢复标记已变化，请重新复核。")
                if previous:
                    current_review = await one(
                        conn, "SELECT * FROM restore_reviews WHERE id=:id", {"id": review_id}
                    )
                    api.revision(current_review, previous["revision"])
                    await execute(
                        conn,
                        "UPDATE restore_reviews SET payload=:p,next_cursor=:c,state=:s,checked_at=:t,revision=revision+1 WHERE id=:id",
                        {
                            "p": canonical(payload),
                            "c": next_cursor,
                            "s": "checking" if next_cursor else "ready",
                            "t": time.time(),
                            "id": review_id,
                        },
                    )
                else:
                    await execute(
                        conn,
                        "INSERT INTO restore_reviews VALUES (:id,'personal',:a,:r,:m,:p,:c,:s,:t,:t,NULL,NULL,0)",
                        {
                            "id": review_id,
                            "a": settings.account_ref,
                            "r": revision,
                            "m": marker_key,
                            "p": canonical(payload),
                            "c": next_cursor,
                            "s": "checking" if next_cursor else "ready",
                            "t": time.time(),
                        },
                    )
                return {
                    "review_id": review_id,
                    "next_cursor": next_cursor,
                    "ready": next_cursor is None,
                    "checked": len(batch),
                    "revision": revision,
                }

            return await api.mutate("recovery/check", "main", body, save)

    async def confirm(self, api, request, body):
        if (
            body.get("reviewed_remote_history") is not True
            or body.get("keep_old_operations_paused") is not True
        ):
            raise NotiDoError(
                "RESTORE_ACK_REQUIRED",
                "请核对备份后的新增/修改与未匹配任务，并确认旧操作继续暂停。",
            )
        async with self.service.write_lock:

            async def approve(conn, expected):
                settings_row = await one(conn, "SELECT * FROM settings WHERE id='main'")
                api.revision(settings_row, expected)
                settings = Settings.model_validate_json(settings_row["payload"])
                record = await one(
                    conn, "SELECT * FROM restore_reviews WHERE id=:id", {"id": body["review_id"]}
                )
                if (
                    not record
                    or record["state"] != "ready"
                    or record["next_cursor"] is not None
                    or record["config_revision"] != expected
                    or record["account_ref"] != settings.account_ref
                    or record["marker_key"] != self.marker_key()
                    or record["checked_at"] < time.time() - 900
                    or self.service.maintenance != "RESTORE_REMOTE_REVIEW_REQUIRED"
                ):
                    raise NotiDoError(
                        "RESTORE_REVIEW_STALE", "复核不完整、已过期或配置已变更，请重新复核。"
                    )
                if (
                    await self.history_fingerprint(settings.account_ref, conn)
                    != json.loads(record["payload"])["history_fingerprint"]
                ):
                    raise NotiDoError(
                        "RESTORE_REVIEW_STALE", "本地账本在复核后发生变化，请重新复核。"
                    )
                count = (
                    await execute(
                        conn,
                        "UPDATE operations SET paused=1,revision=revision+1 WHERE state='validated' OR state IN ('outcome_unknown','created_unverified','uploaded_unverified','applied_unverified')",
                    )
                ).rowcount
                await execute(
                    conn,
                    "UPDATE restore_reviews SET state='confirmed',confirmed_at=:t,confirmed_by=:actor,revision=revision+1 WHERE id=:id",
                    {"id": record["id"], "t": time.time(), "actor": request.username},
                )
                return {
                    "confirmed": True,
                    "old_operations_paused": count,
                    "review_id": record["id"],
                    "revision": expected,
                }

            result = await api.mutate("recovery/confirm", "main", body, approve)
            await self.clear_confirmed_marker()
            return result
