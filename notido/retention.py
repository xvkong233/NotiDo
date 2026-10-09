"""Conservative local retention. Completion is observed, never inferred from group state."""

import asyncio
import json
import re
import shutil
import time
from uuid import UUID

from .db import execute, one, rows
from .keys import canonical, key

DAY = 86400


class Retention:
    def __init__(self, service):
        self.service, self.db = service, service.db

    async def run(self, *, now=None):
        now = time.time() if now is None else now
        settings, config_revision = await self.db.settings()
        policy = settings.retention
        groups = await self.db.read(
            "SELECT g.*,r.body_purged_at,r.summary_purged_at FROM material_groups g LEFT JOIN retention_checks r ON r.group_id=g.id WHERE g.state IN ('completed','cancelled') AND g.last_at<:old AND (r.checked_at IS NULL OR r.checked_at<:day) AND r.summary_purged_at IS NULL AND (r.body_purged_at IS NULL OR g.last_at<:summary) ORDER BY coalesce(r.checked_at,0),g.last_at LIMIT 10",
            {
                "old": now - policy.body_days * DAY,
                "day": now - DAY,
                "summary": now - policy.summary_days * DAY,
            },
        )
        purged = 0
        for group in groups:
            if self.service.stopping or self.service.maintenance:
                break
            async with self.db.transaction() as conn:
                await execute(
                    conn,
                    "INSERT INTO retention_checks(group_id,checked_at) VALUES (:g,:t) ON CONFLICT(group_id) DO UPDATE SET checked_at=excluded.checked_at",
                    {"g": group["id"], "t": now},
                )
            if not group["body_purged_at"] and not await self.eligible(group, settings, now):
                continue
            async with self.service.write_lock:
                async with self.db.transaction() as conn:
                    current_config = await one(
                        conn, "SELECT revision FROM settings WHERE id='main'"
                    )
                    current = await one(
                        conn, "SELECT * FROM material_groups WHERE id=:g", {"g": group["id"]}
                    )
                    if (
                        current_config["revision"] != config_revision
                        or current["revision"] != group["revision"]
                        or await self.protected(conn, group["id"])
                    ):
                        continue
                    summary = now - group["last_at"] >= policy.summary_days * DAY
                    await self.purge(conn, group["id"], now, summary)
                    purged += 1
        async with self.service.write_lock:
            await self.remove_blobs()
        async with self.db.transaction() as conn:
            await execute(
                conn,
                "UPDATE delete_confirmations SET snapshot='{}' WHERE expires_at<=:now AND consumed_by IS NULL AND snapshot!='{}'",
                {"now": now},
            )
            reviews = await rows(
                conn,
                "SELECT id,payload FROM restore_reviews WHERE checked_at<:old AND json_extract(payload,'$.retention_expired') IS NOT 1 LIMIT 10",
                {"old": now - policy.body_days * DAY},
            )
            for review in reviews:
                payload = json.loads(review["payload"])
                expired_review = {
                    "retention_expired": True,
                    "history_fingerprint": payload.get("history_fingerprint"),
                    "history_count": len(payload.get("history", [])),
                    "active_task_count": payload.get("scope", {}).get("active_tasks", 0),
                }
                await execute(
                    conn,
                    "UPDATE restore_reviews SET payload=:p WHERE id=:id",
                    {"id": review["id"], "p": canonical(expired_review)},
                )
        await asyncio.to_thread(self.remove_derived, now, policy.body_days)
        indexed = {row["hash"] for row in await self.db.read("SELECT hash FROM blobs")}
        await asyncio.to_thread(self.remove_orphans, now, policy.body_days, indexed)
        return {"groups_purged": purged, "checked": len(groups), "remote_deletions": 0}

    async def protected(self, conn, group_id):
        group = await one(conn, "SELECT state FROM material_groups WHERE id=:g", {"g": group_id})
        if not group or group["state"] not in ("completed", "cancelled"):
            return True
        active = await one(
            conn,
            "SELECT (SELECT count(*) FROM operations WHERE json_extract(plan,'$.group_id')=:g AND state NOT IN ('succeeded','cancelled')) + (SELECT count(*) FROM jobs WHERE state IN ('pending','running') AND (json_extract(payload,'$.group_id')=:g OR json_extract(payload,'$.operation_id') IN (SELECT id FROM operations WHERE json_extract(plan,'$.group_id')=:g))) + (SELECT count(*) FROM receipt_records WHERE state!='sent' AND operation_id IN (SELECT id FROM operations WHERE json_extract(plan,'$.group_id')=:g)) + (SELECT count(*) FROM assets WHERE group_id=:g AND state='pending') AS n",
            {"g": group_id},
        )
        if active["n"]:
            return True
        extra = await one(
            conn,
            "SELECT (SELECT count(*) FROM receipt_records WHERE state!='sent' AND (dedupe_key LIKE '%'||:g||'%' OR dedupe_key IN (SELECT 'question:'||question_ref FROM question_history WHERE group_id=:g))) + (SELECT count(*) FROM operations WHERE state NOT IN ('succeeded','cancelled') AND action_id IN (SELECT action_id FROM operations WHERE json_extract(plan,'$.group_id')=:g)) + (SELECT count(*) FROM jobs WHERE state IN ('pending','running') AND json_extract(payload,'$.asset_id') IN (SELECT id FROM assets WHERE group_id=:g)) AS n",
            {"g": group_id},
        )
        return bool(extra["n"])

    async def eligible(self, group, settings, now):
        group_id = group["id"]
        async with self.db.transaction() as conn:
            if await self.protected(conn, group_id):
                return False
            # An intended original without a verified target remains recoverable indefinitely.
            incomplete = await one(
                conn,
                "SELECT count(*) AS n FROM assets a WHERE a.group_id=:g AND (EXISTS (SELECT 1 FROM task_attachment_links l WHERE l.asset_id=a.id AND verified=0) OR EXISTS (SELECT 1 FROM operations o,json_each(o.plan,'$.attachment_asset_ids') x WHERE x.value=a.id AND NOT EXISTS (SELECT 1 FROM task_attachment_links l WHERE l.asset_id=a.id AND l.operation_id IN (SELECT id FROM operations WHERE state='succeeded') AND verified=1)))",
                {"g": group_id},
            )
            if incomplete["n"]:
                return False
            refs = await rows(
                conn,
                "SELECT account_ref,project_id,task_id FROM notice_task_links WHERE notice_id IN (SELECT id FROM notice_records WHERE group_id=:g) UNION SELECT account_ref,json_extract(plan,'$.project_id') AS project_id,coalesce(json_extract(plan,'$.task_id'),remote_id) AS task_id FROM operations WHERE json_extract(plan,'$.group_id')=:g AND state='succeeded' UNION SELECT account_ref,project_id,task_id FROM task_attachment_links WHERE asset_id IN (SELECT id FROM assets WHERE group_id=:g)",
                {"g": group_id},
            )
        for ref in refs:
            if ref["account_ref"] != settings.account_ref or not ref["task_id"]:
                return False
            try:
                async with self.service.write_lock:
                    current, _ = await self.db.settings()
                    if (
                        current.account_ref != settings.account_ref
                        or current.credential_generation != settings.credential_generation
                    ):
                        return False
                    deletions = await self.db.read(
                        "SELECT checked_at FROM operations WHERE kind='delete' AND state='succeeded' AND account_ref=:account_ref AND json_extract(plan,'$.project_id')=:project_id AND remote_id=:task_id ORDER BY checked_at DESC LIMIT 1",
                        ref,
                    )
                    if deletions:
                        probe = await self.service.gateway.probe_task(
                            ref["project_id"], ref["task_id"]
                        )
                        if not probe["exists"]:
                            if (
                                now - (deletions[0]["checked_at"] or now)
                                < settings.retention.completed_task_days * DAY
                            ):
                                return False
                            continue
                    task = await self.service.gateway.get(ref["project_id"], ref["task_id"])
            except Exception:
                return False  # Missing/read-failed does not prove completed.
            if task.get("status") != 2:
                async with self.db.transaction() as conn:
                    await execute(
                        conn,
                        "DELETE FROM task_completion_observations WHERE account_ref=:account_ref AND project_id=:project_id AND task_id=:task_id",
                        ref,
                    )
                return False
            async with self.db.transaction() as conn:
                await execute(
                    conn,
                    "INSERT OR IGNORE INTO task_completion_observations VALUES (:account_ref,:project_id,:task_id,:now)",
                    {**ref, "now": now},
                )
                observed = await one(
                    conn,
                    "SELECT observed_completed_at FROM task_completion_observations WHERE account_ref=:account_ref AND project_id=:project_id AND task_id=:task_id",
                    ref,
                )
            if (
                now - observed["observed_completed_at"]
                < settings.retention.completed_task_days * DAY
            ):
                return False
        return True

    async def purge(self, conn, group_id, now, summary):
        expired = {"retention_expired": True}
        await execute(conn, "UPDATE native_group_outcomes SET declaration=NULL WHERE group_id=:g", {"g": group_id})
        await execute(conn, "DELETE FROM native_material_reads WHERE group_id=:g", {"g": group_id})
        await execute(conn, "DELETE FROM native_asset_reads WHERE group_id=:g", {"g": group_id})
        await execute(
            conn, "DELETE FROM native_material_deliveries WHERE group_id=:g", {"g": group_id}
        )
        await execute(
            conn,
            "UPDATE receipt_records SET body='处理记录已过正文保留期。' WHERE state='sent' AND (dedupe_key LIKE '%'||:g||'%' OR dedupe_key IN (SELECT 'question:'||question_ref FROM question_history WHERE group_id=:g))",
            {"g": group_id},
        )
        await execute(
            conn,
            "UPDATE material_segments SET text='',normalized_text='',state='unknown',reason='RETENTION_EXPIRED' WHERE group_id=:g",
            {"g": group_id},
        )
        await execute(
            conn,
            "UPDATE evidence_records SET quote='' WHERE segment_id IN (SELECT id FROM material_segments WHERE group_id=:g)",
            {"g": group_id},
        )
        messages = await rows(
            conn, "SELECT id,envelope FROM message_records WHERE group_id=:g", {"g": group_id}
        )
        for message in messages:
            envelope = json.loads(message["envelope"])
            for segment in envelope.get("segments", []):
                segment["text"] = None
            await execute(
                conn,
                "UPDATE message_records SET envelope=:p WHERE id=:id",
                {"p": canonical(envelope), "id": message["id"]},
            )
        await execute(
            conn,
            "UPDATE media_acquisitions SET recoverable_ref=NULL WHERE asset_id IN (SELECT id FROM assets WHERE group_id=:g)",
            {"g": group_id},
        )
        await execute(
            conn,
            "UPDATE assets SET state='unavailable',error='RETENTION_EXPIRED',name=CASE WHEN :summary THEN '原件已过保留期' ELSE name END,revision=revision+1 WHERE group_id=:g AND error IS NOT 'RETENTION_EXPIRED'",
            {"g": group_id, "summary": summary},
        )
        if summary:
            await execute(
                conn,
                "UPDATE assets SET name='原件已过保留期' WHERE group_id=:g AND error='RETENTION_EXPIRED'",
                {"g": group_id},
            )
        for table, where in (
            ("notice_versions", "notice_id IN (SELECT id FROM notice_records WHERE group_id=:g)"),
            (
                "action_item_versions",
                "action_id IN (SELECT action_id FROM operations WHERE json_extract(plan,'$.group_id')=:g UNION SELECT id FROM action_items WHERE notice_id IN (SELECT id FROM notice_records WHERE group_id=:g))",
            ),
            ("question_history", "group_id=:g"),
        ):
            records = await rows(
                conn, f"SELECT rowid AS row_id,payload FROM {table} WHERE {where}", {"g": group_id}
            )
            for record in records:
                payload = json.loads(record["payload"])
                tombstone = {**expired, "payload_hash": payload.get("payload_hash", key(payload))}
                if not summary:
                    for field in ("notice_summary", "title", "intent"):
                        if field in payload:
                            tombstone[field] = payload[field]
                await execute(
                    conn,
                    f"UPDATE {table} SET payload=:p WHERE rowid=:id",
                    {"p": canonical(tombstone), "id": record["row_id"]},
                )
        operations = await rows(
            conn,
            "SELECT id,plan,result FROM operations WHERE json_extract(plan,'$.group_id')=:g",
            {"g": group_id},
        )
        for op in operations:
            await execute(
                conn,
                "UPDATE delete_confirmations SET snapshot='{}' WHERE consumed_by=:id",
                {"id": op["id"]},
            )
            plan = json.loads(op["plan"])
            minimal = {
                k: plan[k]
                for k in (
                    "plan_id",
                    "kind",
                    "group_id",
                    "session_key",
                    "account_ref",
                    "project_id",
                    "task_id",
                    "hash",
                    "action_id",
                    "notice_id",
                    "origin",
                    "actor_key",
                    "framework_instance_id",
                    "timezone",
                    "delivery_mode",
                )
                if k in plan
            }
            minimal.update(
                {
                    **expired,
                    "payload_hash": plan.get("payload_hash", key(plan)),
                    "fields": {} if summary else {"title": plan.get("fields", {}).get("title")},
                }
            )
            result = json.loads(op["result"] or "{}")
            safe_result = {
                k: result[k]
                for k in (
                    "contract_version",
                    "operation_id",
                    "kind",
                    "status",
                    "side_effect",
                    "account_ref",
                    "remote_id",
                )
                if k in result
            }
            await execute(
                conn,
                "UPDATE operations SET plan=:p,result=:r WHERE id=:id",
                {"p": canonical(minimal), "r": canonical(safe_result), "id": op["id"]},
            )
            versions = await rows(
                conn,
                "SELECT id,payload,payload_hash FROM operation_plan_versions WHERE operation_id=:id",
                {"id": op["id"]},
            )
            for version in versions:
                old = json.loads(version["payload"])
                version_minimal = {"plan_id": old.get("plan_id"), **expired}
                if not summary:
                    version_minimal["fields"] = {"title": old.get("fields", {}).get("title")}
                await execute(
                    conn,
                    "UPDATE operation_plan_versions SET payload=:p,payload_hash=:h,expired_at=coalesce(expired_at,:t) WHERE id=:id",
                    {
                        "p": canonical(version_minimal),
                        "h": version["payload_hash"] or old.get("payload_hash") or key(old),
                        "t": now,
                        "id": version["id"],
                    },
                )
            await execute(
                conn,
                "UPDATE receipt_records SET body=:b WHERE operation_id=:id AND state='sent'",
                {
                    "b": "处理记录已过保留期。"
                    if summary
                    else f"已核验 {plan['kind']} · {plan.get('fields', {}).get('title') or '原件'}",
                    "id": op["id"],
                },
            )
        await execute(
            conn,
            "UPDATE notice_task_links SET snapshot=:p WHERE notice_id IN (SELECT id FROM notice_records WHERE group_id=:g)",
            {"p": canonical(expired), "g": group_id},
        )
        await execute(
            conn,
            "UPDATE sessions SET question=NULL,question_expires=NULL WHERE json_extract(question,'$.group_id')=:g",
            {"g": group_id},
        )
        await execute(
            conn,
            "UPDATE jobs SET payload=:p WHERE state IN ('done','failed') AND json_extract(payload,'$.group_id')=:g",
            {"g": group_id, "p": canonical({"group_id": group_id, **expired})},
        )
        await execute(
            conn,
            "UPDATE api_requests SET response=:p WHERE target=:g OR target IN (SELECT id FROM operations WHERE json_extract(plan,'$.group_id')=:g)",
            {"g": group_id, "p": canonical({"processed": True, **expired})},
        )
        await execute(
            conn,
            "UPDATE retention_checks SET body_purged_at=coalesce(body_purged_at,:t),summary_purged_at=CASE WHEN :summary THEN :t ELSE summary_purged_at END WHERE group_id=:g",
            {"g": group_id, "t": now, "summary": summary},
        )
        # Keep lightweight IDs, hashes and uniqueness keys. They live at least 180 days.
        await execute(
            conn,
            "INSERT OR IGNORE INTO blob_tombstones(hash,removed_at) SELECT hash,:t FROM blobs b WHERE EXISTS (SELECT 1 FROM assets a WHERE a.hash=b.hash AND a.error='RETENTION_EXPIRED') AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.hash=b.hash AND (a.state='ready' OR a.error IS NOT 'RETENTION_EXPIRED'))",
            {"t": now},
        )

    async def remove_blobs(self):
        for blob in await self.db.read(
            "SELECT b.path,b.hash FROM blobs b JOIN blob_tombstones t ON b.hash=t.hash WHERE t.unlinked_at IS NULL ORDER BY t.removed_at LIMIT 100"
        ):
            async with self.db.transaction() as conn:
                marker = await one(
                    conn, "SELECT hash FROM blob_tombstones WHERE hash=:h", {"h": blob["hash"]}
                )
                refs = await one(
                    conn,
                    "SELECT count(*) AS n FROM assets WHERE hash=:h AND (state='ready' OR error IS NOT 'RETENTION_EXPIRED')",
                    {"h": blob["hash"]},
                )
                if not marker or refs["n"]:
                    continue
                path = (self.service.root / blob["path"]).resolve()
                if (
                    path.parent != (self.service.root / "blobs").resolve()
                    or path.name != blob["hash"]
                ):
                    raise ValueError("Invalid blob cleanup path")
                await asyncio.to_thread(path.unlink, missing_ok=True)
                await execute(
                    conn,
                    "UPDATE blob_tombstones SET unlinked_at=:t WHERE hash=:h",
                    {"h": blob["hash"], "t": time.time()},
                )

    def remove_derived(self, now, days):
        root = (self.service.root / "derived").resolve()
        for entry in root.iterdir():
            if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                continue
            if entry.resolve() in self.service.blobs.active_derived:
                continue
            if entry.resolve().parent != root or entry.stat().st_mtime >= now - days * DAY:
                continue
            if entry.is_dir():
                shutil.rmtree(entry)

    def remove_orphans(self, now, days, indexed):
        for folder in ("staging", "blobs"):
            root = (self.service.root / folder).resolve()
            for entry in root.iterdir():
                if (
                    entry.is_symlink()
                    or getattr(entry, "is_junction", lambda: False)()
                    or not entry.is_file()
                ):
                    continue
                if entry.resolve().parent != root or entry.stat().st_mtime >= now - days * DAY:
                    continue
                if folder == "blobs":
                    if not re.fullmatch(r"[0-9a-f]{64}", entry.name) or entry.name in indexed:
                        continue
                else:
                    try:
                        if str(UUID(entry.name)) != entry.name:
                            continue
                    except ValueError:
                        continue
                    if entry.resolve() in self.service.blobs.active_staging:
                        continue
                entry.unlink(missing_ok=True)
