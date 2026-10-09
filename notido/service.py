import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock, Timeout

from .dates import native_fields, normalize, overdue
from .db import Database, execute, one, rows
from .errors import NotiDoError
from .keys import canonical, key, uid
from .materials import AsyncFile, BlobStore
from .models import InputEnvelope, Query
from .query import read_scope, task_date
from .recovery import RestoreReview
from .retention import Retention
from .sources import SourceRegistry


class Service:
    def __init__(self, root: Path, bridge, gateway):
        self.root, self.bridge, self.gateway = root, bridge, gateway
        self.native_ai = True
        self.db, self.blobs = Database(root), BlobStore(root)
        self.sources = SourceRegistry()
        self.owner = uid()
        self.owner_lock = FileLock(str(root / "worker.lock"))
        self.wakeup = asyncio.Event()
        self.stopping = False
        self.write_lock = asyncio.Lock()
        self.material_limit = asyncio.Semaphore(2)
        self.running = set()
        self.worker = None
        self.maintenance = None
        self.processing_deadlines = {}
        self.retention = Retention(self)
        self.retention_status = None
        self.restore_review = RestoreReview(self)

    async def retired_flow(self, *args, **kwargs):
        raise NotiDoError(
            "ASTRBOT_NATIVE_FLOW_REQUIRED", "请在 AstrBot 当前对话使用滴答函数工具或 /notido 指令。"
        )

    intake = collection_command = close_group = read_materials = parse_group = supplement_group = (
        resolve_question
    ) = query = next_page = retired_flow

    async def start(self):
        try:
            self.owner_lock.acquire(timeout=0)
        except Timeout as exc:
            raise NotiDoError(
                "WORKER_OWNER_EXISTS", "另一个 NotiDo worker 正在使用数据目录。", status=503
            ) from exc
        try:
            await self.db.initialize()
            if self.native_ai:
                # Legacy plans were made without AstrBot's memory. Preserve history,
                # but require a new native conversation before any outstanding write.
                async with self.db.transaction() as conn:
                    await execute(
                        conn,
                        "UPDATE operations SET paused=1,revision=revision+1 WHERE state='validated' AND json_extract(plan,'$.delivery_mode') IS NOT 'framework_tool'",
                    )
                    await execute(
                        conn,
                        "UPDATE jobs SET state='failed',error='ASTRBOT_NATIVE_FLOW_REQUIRED' WHERE state IN ('pending','running') AND kind IN ('close_group','read_materials','parse_group')",
                    )
            await self.check_blobs()
            if (self.root / "restore-review.required").exists():
                self.maintenance = "RESTORE_REMOTE_REVIEW_REQUIRED"
                async with self.db.transaction() as conn:
                    await execute(
                        conn,
                        "INSERT OR IGNORE INTO restored_group_holds(group_id,created_at) SELECT id,:t FROM material_groups WHERE state NOT IN ('completed','cancelled')",
                        {"t": time.time()},
                    )
                    await execute(
                        conn,
                        "UPDATE material_groups SET state='awaiting_clarification',revision=revision+1 WHERE state IN ('collecting','awaiting_materials') AND id IN (SELECT group_id FROM restored_group_holds WHERE released_at IS NULL)",
                    )
            if (self.root / "maintenance.required").exists():
                self.maintenance = "AUTH_COMMIT_UNKNOWN"
            await self.restore_review.clear_confirmed_marker()
            async with self.db.transaction() as conn:
                unresolved = await rows(
                    conn,
                    "SELECT id,revision FROM operations WHERE state IN ('outcome_unknown','created_unverified','uploaded_unverified','applied_unverified')",
                )
                for operation in unresolved:
                    await self.db.job(
                        conn,
                        "reconcile",
                        f"restart-check:{operation['id']}:{operation['revision']}",
                        {"operation_id": operation["id"]},
                        priority=2,
                    )
            self.worker = asyncio.create_task(self.run(), name="notido-worker")
        except BaseException:
            self.owner_lock.release()
            raise

    async def check_blobs(self):
        invalid = await self.db.read(
            "SELECT a.id FROM assets a JOIN blob_tombstones t ON a.hash=t.hash WHERE a.state='ready'"
        )
        if invalid:
            raise NotiDoError("BLOB_CORRUPT", "过期原件仍有活动引用，进入维护状态。", status=503)
        for blob in await self.db.read(
            "SELECT b.* FROM blobs b WHERE NOT EXISTS (SELECT 1 FROM blob_tombstones t WHERE t.hash=b.hash)"
        ):
            path = self.blobs.path(blob["path"])
            if (
                not path.is_file()
                or path.stat().st_size != blob["size"]
                or await asyncio.to_thread(self.blobs.hash_file, path) != blob["hash"]
            ):
                raise NotiDoError(
                    "BLOB_CORRUPT", "数据库引用原件缺失或校验失败，进入维护状态。", status=503
                )

    async def stop(self):
        self.stopping = True
        self.wakeup.set()
        if self.worker:
            try:
                await asyncio.wait_for(self.worker, 120)
            except TimeoutError:
                for task in self.running:
                    task.cancel()
                await asyncio.gather(*self.running, return_exceptions=True)
                self.worker.cancel()
                await asyncio.gather(self.worker, return_exceptions=True)
        elif self.running:
            try:
                await asyncio.wait_for(asyncio.gather(*self.running, return_exceptions=True), 120)
            except TimeoutError:
                for task in self.running:
                    task.cancel()
                await asyncio.gather(*self.running, return_exceptions=True)
        await self.db.close()
        self.owner_lock.release()

    async def register_blob(self, conn, blob):
        # GC may have removed an earlier, unindexed save while its caller waited for this
        # transaction. Reject that occurrence rather than committing a broken ready asset.
        self.blobs.path(blob["path"])
        await execute(
            conn,
            "INSERT OR IGNORE INTO blobs VALUES (:hash,:size,:path,:t)",
            {**blob, "t": time.time()},
        )
        await execute(conn, "DELETE FROM blob_tombstones WHERE hash=:h", {"h": blob["hash"]})

    async def maintain_retention(self):
        try:
            self.retention_status = {**await self.retention.run(), "checked_at": time.time()}
        except Exception:
            self.retention_status = {"error": "RETENTION_CHECK_FAILED", "checked_at": time.time()}

    async def authorized(self, envelope):
        binding = await self.db.read(
            "SELECT * FROM actor_bindings WHERE instance_id=:i AND actor_key=:a AND session_key=:s AND enabled=1",
            {
                "i": envelope.framework_instance_id,
                "a": envelope.actor_key,
                "s": envelope.session_key,
            },
        )
        return bool(binding)

    async def add_segment(self, conn, group, source, location, content, state="read", reason=None):
        from .policy import normalize_text

        await execute(
            conn,
            "INSERT OR IGNORE INTO material_segments VALUES (:id,:g,:s,:l,:t,:n,:state,:r)",
            {
                "id": uid(),
                "g": group,
                "s": source,
                "l": location,
                "t": content,
                "n": normalize_text(content),
                "state": state,
                "r": reason,
            },
        )

    async def run(self):
        last_health = 0
        last_dispatch = 0
        next_retention = time.monotonic() + 60
        while not self.stopping:
            if time.monotonic() - last_health > 5:
                health = {
                    "updated_at": time.time(),
                    "maintenance": self.maintenance,
                    "worker_owner": self.owner,
                }
                await asyncio.to_thread(
                    (self.root / "health.json").write_text, canonical(health), encoding="utf-8"
                )
                last_health = time.monotonic()
            self.running = {x for x in self.running if not x.done()}
            if time.monotonic() >= next_retention and not self.running and not self.maintenance:
                self.running.add(
                    asyncio.create_task(self.maintain_retention(), name="notido-retention")
                )
                next_retention = time.monotonic() + 60
            if time.monotonic() - last_dispatch > 1:
                await self.dispatch_pending()
                last_dispatch = time.monotonic()
            # Restore review fingerprints must remain stable until explicit
            # acknowledgement. Automatic reconciliation also changes ledger
            # revisions, so leave all background jobs pending in maintenance.
            if len(self.running) < 6 and not self.maintenance:
                async with self.db.transaction() as conn:
                    job = await one(
                        conn,
                        "SELECT * FROM jobs WHERE state='pending' AND available_at<=:now ORDER BY CASE WHEN priority>2 AND created_at<:now-30 THEN 0 ELSE priority END,created_at LIMIT 1",
                        {"now": time.time()},
                    )
                    if job:
                        await execute(
                            conn,
                            "UPDATE jobs SET state='running',owner=:o,attempt=attempt+1 WHERE id=:id AND state='pending'",
                            {"id": job["id"], "o": self.owner},
                        )
                if job:
                    task = asyncio.create_task(self.perform(job), name=f"notido:{job['kind']}")
                    self.running.add(task)
                    continue
            self.wakeup.clear()
            try:
                await asyncio.wait_for(self.wakeup.wait(), 0.5)
            except TimeoutError:
                pass
        await asyncio.gather(*self.running, return_exceptions=True)

    async def dispatch_pending(self):
        """Promote durable dependencies/outbox as bounded queue capacity becomes free."""
        async with self.db.transaction() as conn:
            receipts = await rows(
                conn,
                "SELECT r.id FROM receipt_records r WHERE r.state='pending' AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.kind='send_receipt' AND j.state IN ('pending','running') AND json_extract(j.payload,'$.receipt_id')=r.id) ORDER BY r.created_at LIMIT 10",
            )
            for receipt in receipts:
                await self.db.job(
                    conn,
                    "send_receipt",
                    f"receipt:{receipt['id']}",
                    {"receipt_id": receipt["id"]},
                    priority=1,
                    defer=True,
                )
            if not self.maintenance and not self.stopping:
                operations = await rows(
                    conn,
                    "SELECT o.id,o.revision FROM operations o WHERE o.state='validated' AND o.paused=0 AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.kind='execute_operation' AND j.state IN ('pending','running') AND json_extract(j.payload,'$.operation_id')=o.id) ORDER BY o.created_at LIMIT 10",
                )
                for operation in operations:
                    await self.db.job(
                        conn,
                        "execute_operation",
                        f"dispatch:{operation['id']}:{operation['revision']}",
                        {"operation_id": operation["id"]},
                        priority=5,
                        defer=True,
                    )

    async def perform(self, job):
        payload = json.loads(job["payload"])
        try:
            await {
                "acquire_material": self.acquire,
                "close_group": self.close_group,
                "read_materials": self.read_materials,
                "parse_group": self.parse_group,
                "execute_operation": self.execute_operation,
                "reconcile": self.reconcile,
                "send_receipt": self.send_receipt,
            }[job["kind"]](payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = exc.code if isinstance(exc, NotiDoError) else "INTERNAL_ERROR"
            async with self.db.transaction() as conn:
                await execute(
                    conn,
                    "UPDATE jobs SET state='failed',error=:e WHERE id=:id",
                    {"e": code, "id": job["id"]},
                )
                group_id = payload.get("group_id")
                if group_id:
                    await self.ask(
                        conn,
                        group_id,
                        [
                            exc.message
                            if isinstance(exc, NotiDoError)
                            else "处理未完成，请从后台查看并核查。"
                        ],
                        {
                            "job_id": job["id"],
                            **(exc.details if isinstance(exc, NotiDoError) else {}),
                        },
                    )
            return
        async with self.db.transaction() as conn:
            await execute(
                conn,
                "UPDATE jobs SET state='done',owner=NULL WHERE id=:id AND state='running'",
                {"id": job["id"]},
            )

    async def acquire(self, payload):
        async with self.material_limit:
            asset = (
                await self.db.read("SELECT * FROM assets WHERE id=:id", {"id": payload["asset_id"]})
            )[0]
            try:
                path, name = await self.bridge.acquire_material(asset["source_id"])
                settings, _ = await self.db.settings()
                file = await asyncio.to_thread(Path(path).open, "rb")
                try:
                    blob = await self.blobs.save(
                        AsyncFile(file),
                        min_free_bytes=settings.min_free_bytes,
                        max_bytes=settings.materials.file_bytes,
                    )
                finally:
                    await asyncio.to_thread(file.close)
                async with self.db.transaction() as conn:
                    total = await one(
                        conn,
                        "SELECT coalesce(sum(b.size),0) AS size FROM assets a JOIN blobs b ON b.hash=a.hash WHERE a.group_id=:g",
                        {"g": asset["group_id"]},
                    )
                    if total["size"] + blob["size"] > settings.materials.group_bytes:
                        raise NotiDoError(
                            "GROUP_BYTES_LIMIT", "该组原件超过配置字节预算，请分组补发。"
                        )
                    await self.register_blob(conn, blob)
                    await execute(
                        conn,
                        "UPDATE assets SET hash=:h,name=:n,state='ready',revision=revision+1 WHERE id=:id",
                        {"h": blob["hash"], "n": name, "id": asset["id"]},
                    )
                    await execute(
                        conn,
                        "UPDATE media_acquisitions SET state='done',revision=revision+1 WHERE asset_id=:id",
                        {"id": asset["id"]},
                    )
            except Exception as exc:
                code = exc.code if isinstance(exc, NotiDoError) else "MATERIAL_UNAVAILABLE"
                async with self.db.transaction() as conn:
                    await execute(
                        conn,
                        "UPDATE assets SET state='unavailable',error=:e,revision=revision+1 WHERE id=:id",
                        {"e": code, "id": asset["id"]},
                    )
                    await execute(
                        conn,
                        "UPDATE media_acquisitions SET state='unavailable',revision=revision+1 WHERE asset_id=:id",
                        {"id": asset["id"]},
                    )
            self.wakeup.set()

    async def restored_pipeline_stale(self, payload):
        holds = await self.db.read(
            "SELECT released_at,resume_id FROM restored_group_holds WHERE group_id=:g",
            {"g": payload["group_id"]},
        )
        if not holds:
            return False
        hold = holds[0]
        return hold["released_at"] is None or hold["resume_id"] != payload.get("resume_id")

    async def ask(self, conn, group_id, questions, context):
        if self.native_ai and context.get("operation_id"):
            await execute(
                conn,
                "UPDATE operations SET result=:r WHERE id=:id",
                {
                    "id": context["operation_id"],
                    "r": canonical(
                        {
                            "error": {"code": context.get("code", "TARGET_CHANGED")},
                            "questions": questions[:3],
                            "current": context.get("current"),
                        }
                    ),
                },
            )
            return {"questions": questions[:3]}
        group = await one(conn, "SELECT * FROM material_groups WHERE id=:g", {"g": group_id})
        message = await one(
            conn,
            "SELECT envelope FROM message_records WHERE group_id=:g ORDER BY received_at LIMIT 1",
            {"g": group_id},
        )
        origin = (
            json.loads(message["envelope"])["reply_origin_ref"]
            if message
            else context.get("origin")
        )
        question = {
            "question_ref": uid(),
            "group_id": group_id,
            "questions": questions[:3],
            "context": context,
            "yes_no": context.get("yes_no", False),
        }
        session = await one(
            conn,
            "SELECT question,question_expires FROM sessions WHERE id=:s",
            {"s": group["session_id"]},
        )
        active = json.loads(session["question"] or "null")
        another_active = bool(
            active
            and (session["question_expires"] or 0) > time.time()
            and active["group_id"] != group_id
        )
        await execute(
            conn,
            "INSERT INTO question_history VALUES (:q,:g,:s,:p,:e,:t)",
            {
                "q": question["question_ref"],
                "g": group_id,
                "s": group["session_id"],
                "p": canonical(question),
                "e": time.time() + 1800,
                "t": time.time(),
            },
        )
        if not another_active:
            await execute(
                conn,
                "UPDATE sessions SET question=:q,question_expires=:t,revision=revision+1 WHERE id=:s",
                {"q": canonical(question), "t": time.time() + 1800, "s": group["session_id"]},
            )
        await execute(
            conn,
            "UPDATE material_groups SET state='awaiting_clarification',revision=revision+1 WHERE id=:g",
            {"g": group_id},
        )
        if origin:
            await self.db.receipt(
                conn,
                origin=origin,
                body="\n".join(questions[:3])
                + (
                    "\n此会话另有活动问题，本组材料已保留。请先处理当前问题，再从后台续办本组。"
                    if another_active
                    else f"\n回答请引用：{question['question_ref']}"
                ),
                dedupe=f"question:{question['question_ref']}",
            )
        return question

    def base_plan(self, group, origin, settings, revision):
        plan = {
            "plan_id": uid(),
            "user_id": "personal",
            "account_ref": settings.account_ref,
            "credential_generation": settings.credential_generation,
            "group_id": group["id"],
            "session_key": group["session_id"],
            "origin": origin,
            "config_revision": revision,
            "authorization_revision": settings.authorization_revision,
            "timezone": settings.timezone,
            "parser_version": "1",
            "prompt_version": "4",
            "created_at": datetime.now(UTC).isoformat(),
        }

        if not self.native_ai:
            plan["identity_snapshot"] = settings.identity.model_dump()
        return plan

    @staticmethod
    def item_anchor(item, envelopes, is_notice, clarification):
        if clarification.get("source_published_at"):
            return datetime.fromisoformat(clarification["source_published_at"])
        if not is_notice:
            return envelopes[0].received_at
        sources = {x.source_id for x in item.source_evidence}
        timestamps = {
            s.published_at
            for e in envelopes
            for s in e.segments
            if s.source_id in sources and s.published_at is not None
        }
        return next(iter(timestamps)) if len(timestamps) == 1 else None

    async def persist_operation(self, conn, plan):
        budget = await one(
            conn,
            "SELECT deadline_at FROM processing_budgets WHERE group_id=:g",
            {"g": plan["group_id"]},
        )
        if budget:
            plan["processing_deadline_at"] = budget["deadline_at"]
        message = await one(
            conn,
            "SELECT envelope FROM message_records WHERE group_id=:g ORDER BY received_at,id LIMIT 1",
            {"g": plan["group_id"]},
        )
        if not message:
            raise NotiDoError("PLAN_ORIGIN_MISSING", "计划缺少已授权的原始事件。")
        envelope = InputEnvelope.model_validate_json(message["envelope"])
        plan["actor_key"] = envelope.actor_key
        plan["framework_instance_id"] = envelope.framework_instance_id
        # Re-parsing one admitted request must not create another update/complete operation.
        if plan["kind"] in ("update", "complete", "delete"):
            plan["plan_id"] = key(
                "plan-v1",
                plan["account_ref"],
                plan["group_id"],
                plan["kind"],
                plan["project_id"],
                plan["task_id"],
                plan["fields"],
            )
        plan["input_fingerprint"] = key(message["envelope"])
        operation_key = (
            key("personal", plan["account_ref"], plan["action_id"], "create")
            if plan["kind"] == "create"
            else key("personal", plan["account_ref"], plan["plan_id"], plan["kind"])
        )
        previous_operation = await one(
            conn, "SELECT id FROM operations WHERE operation_key=:k", {"k": operation_key}
        )
        if previous_operation:
            return previous_operation["id"]
        if plan.get("action_id"):
            action = await one(
                conn, "SELECT revision FROM action_items WHERE id=:id", {"id": plan["action_id"]}
            )
            plan["item_revision"] = action["revision"] + (1 if plan["kind"] == "update" else 0)
        if (
            plan["kind"] == "update"
            and "dueDate" in plan["fields"]
            and not plan.get("normalized_date")
        ):
            due = plan["fields"].get("dueDate")
            combined = {**plan["before"], **plan["fields"]}
            local = task_date(combined, plan.get("timezone", "Asia/Shanghai"))
            plan["normalized_date"] = normalize(
                local.date().isoformat() if due and local else None,
                local.strftime("%H:%M") if due and local and not combined.get("isAllDay") else None,
                anchor=envelope.received_at,
                timezone=plan.get("timezone", "Asia/Shanghai"),
                kind="none" if not due else "date_only" if combined.get("isAllDay") else "timed",
                evidence_id="direct-patch",
            )
        operation_id = uid()
        result = await execute(
            conn,
            "INSERT OR IGNORE INTO operations (id,user_id,account_ref,action_id,kind,operation_key,plan,state,created_at) VALUES (:id,'personal',:a,:act,:k,:key,:p,'validated',:t)",
            {
                "id": operation_id,
                "a": plan["account_ref"],
                "act": plan.get("action_id"),
                "k": plan["kind"],
                "key": operation_key,
                "p": canonical(plan),
                "t": time.time(),
            },
        )
        if result.rowcount:
            if plan["kind"] == "update" and plan.get("action_id"):
                await execute(
                    conn,
                    "UPDATE action_items SET revision=:r WHERE id=:a",
                    {"r": plan["item_revision"], "a": plan["action_id"]},
                )
                await execute(
                    conn,
                    "INSERT INTO action_item_versions VALUES (:id,:a,:r,:p,:t)",
                    {
                        "id": uid(),
                        "a": plan["action_id"],
                        "r": plan["item_revision"],
                        "p": canonical(plan),
                        "t": time.time(),
                    },
                )
            await self.db.job(
                conn,
                "execute_operation",
                f"execute:{operation_id}",
                {"operation_id": operation_id},
                priority=5,
                defer=True,
            )
        return operation_id

    async def finish_group(self, conn, group_id):
        await execute(
            conn,
            "UPDATE material_groups SET state='completed',revision=revision+1 WHERE id=:g",
            {"g": group_id},
        )
        await execute(
            conn, "UPDATE message_records SET state='processed' WHERE group_id=:g", {"g": group_id}
        )

    @staticmethod
    def query_receipt(snapshot, selection):
        lines = [
            "查询范围完整。"
            if snapshot["complete"]
            else "部分清单读取失败，总数未知；以下为可见部分。"
        ]
        for index, item in enumerate(selection, start=1):
            task = item["snapshot"]
            lines.append(
                f"{index}. {task['title']} · {task['date_label']}"
                + (" · 逾期" if task["overdue"] else "")
                + f"\n选择引用：{item['selection_ref']}"
            )
        if not selection:
            lines.append(
                "此范围没有未完成任务。"
                if snapshot["complete"]
                else "可见部分无任务，不能确定全量为空。"
            )
        return "\n".join(lines)

    async def locate(self, target, session_id, settings):
        if target.keyword:
            snapshot = await read_scope(
                self.gateway, settings, datetime.now(UTC), Query(keyword=target.keyword)
            )
            if not snapshot["complete"]:
                raise NotiDoError("QUERY_INCOMPLETE", "定位范围不完整，请提供有效选择引用。")
            if len(snapshot["tasks"]) != 1:
                raise NotiDoError("TARGET_AMBIGUOUS", "未唯一匹配任务，请查询后选择具体条目。")
            candidate = snapshot["tasks"][0]
            return await self.gateway.get(candidate["projectId"], candidate["id"])
        session = (await self.db.read("SELECT * FROM sessions WHERE id=:s", {"s": session_id}))[0]
        if target.selection_ref:
            candidates = json.loads(session["query"] or "{}").get("selection", [])
            matches = [
                x
                for x in candidates
                if x["selection_ref"] == target.selection_ref
                and x["query_revision"] == session["query_revision"]
            ]
        else:
            recent = json.loads(session["recent"] or "null")
            matches = [recent] if recent and recent["recent_ref"] == target.recent_ref else []
        if (
            len(matches) != 1
            or matches[0]["expires_at"] < time.time()
            or matches[0]["account_ref"] != settings.account_ref
        ):
            raise NotiDoError("SELECTION_EXPIRED", "选择引用已失效，请重新查询。")
        candidate = matches[0]
        if candidate["project_id"] not in settings.allowed_projects:
            raise NotiDoError("PROJECT_NOT_ALLOWED", "目标清单已撤权。")
        return await self.gateway.get(candidate["project_id"], candidate["task_id"])

    async def patch_fields(self, patch, target, settings, anchor, *, allow_overdue=False):
        fields = patch.model_dump(exclude_unset=True)
        if "reminder" in fields:
            raise NotiDoError("REMINDER_NOT_VERIFIED", "原生提醒尚未验证，是否只记录待办？")
        result = {
            remote: fields[name]
            for name, remote in (("title", "title"), ("notes", "content"), ("priority", "priority"))
            if name in fields
        }
        if any(x in fields for x in ("date_text", "time_text", "all_day")):
            old = task_date(target, settings.timezone)
            if fields.get("all_day") is True and fields.get("time_text"):
                raise NotiDoError(
                    "DATE_CONTRADICTION",
                    "全天日期不能同时指定具体时刻，请核对后提交一致的日期参数。",
                )
            if "date_text" in fields and fields["date_text"] is None:
                if fields.get("time_text") or fields.get("all_day") is True:
                    raise NotiDoError("DATE_CONTRADICTION", "清除日期不能同时设置时刻或全天日期。")
                result.update({"dueDate": None})
            else:
                date_text = fields.get("date_text") or (old.date().isoformat() if old else None)
                time_text = (
                    fields.get("time_text")
                    if "time_text" in fields
                    else (old.strftime("%H:%M") if old and not target.get("isAllDay") else None)
                )
                if fields.get("all_day") is True:
                    time_text = None
                if fields.get("all_day") is False and not time_text:
                    raise NotiDoError(
                        "DATE_UNRESOLVED", "明确非全天但没有可保留或新指定的时刻，请补充具体时刻。"
                    )
                value = normalize(
                    date_text,
                    time_text,
                    anchor=anchor,
                    timezone=settings.timezone,
                    kind="timed" if time_text else "date_only",
                    evidence_id="direct-patch",
                )
                if overdue(value, datetime.now(UTC)) and not allow_overdue:
                    raise NotiDoError(
                        "OVERDUE_CONFIRMATION", "修改后的期限已过去，请明确是否补记逾期。"
                    )
                result.update(native_fields(value))
                if result["isAllDay"] is False and not target.get("isAllDay", False):
                    result.pop("isAllDay")
        return result

    async def execute_operation(self, payload):
        async with self.write_lock:
            operation_id = payload["operation_id"]
            operation = (
                await self.db.read("SELECT * FROM operations WHERE id=:id", {"id": operation_id})
            )[0]
            if (
                operation["state"] != "validated"
                or operation["paused"]
                or self.stopping
                or self.maintenance
            ):
                return
            plan = json.loads(operation["plan"])
            settings, revision = await self.db.settings()
            bindings = await self.db.read(
                "SELECT * FROM actor_bindings WHERE session_key=:s AND actor_key=:a AND instance_id=:i AND enabled=1",
                {
                    "s": plan["session_key"],
                    "a": plan.get("actor_key"),
                    "i": plan.get("framework_instance_id"),
                },
            )
            error = None
            if self.processing_expired(plan):
                error = "PROCESSING_BUDGET_EXPIRED"
            elif (
                settings.account_ref != plan["account_ref"]
                or settings.credential_generation != plan["credential_generation"]
            ):
                error = "ACCOUNT_CHANGED"
            elif (
                plan.get("delivery_mode") != "framework_tool"
                and settings.identity.model_dump() != plan.get("identity_snapshot")
            ) or (
                not bindings
                and not (plan.get("request_origin") == "webui" and plan.get("webui_actor"))
            ):
                error = "CONFIG_OR_AUTHORIZATION_CHANGED"
            elif plan["project_id"] not in settings.allowed_projects:
                error = "PROJECT_NOT_ALLOWED"
            elif plan["kind"] == "delete":
                confirmation = await self.db.read(
                    "SELECT id FROM delete_confirmations WHERE id=:id AND consumed_by=:op AND expires_at>:now",
                    {"id": plan.get("confirmation_ref"), "op": operation_id, "now": time.time()},
                )
                if not confirmation:
                    error = "DELETE_CONFIRMATION_EXPIRED"
            elif (
                plan["kind"] in ("create", "update")
                and plan.get("normalized_date")
                and overdue(plan["normalized_date"], datetime.now(UTC))
                and not plan.get("allow_overdue")
            ):
                error = "OVERDUE_CONFIRMATION"
            if not error and plan["kind"] in ("update", "complete", "upload", "delete"):
                try:
                    await self.ensure_task_active(
                        plan["account_ref"], plan["project_id"], plan["task_id"]
                    )
                except NotiDoError as exc:
                    error = exc.code
            if error:
                async with self.db.transaction() as conn:
                    await execute(
                        conn,
                        "UPDATE operations SET paused=1,revision=revision+1 WHERE id=:id AND state='validated'",
                        {"id": operation_id},
                    )
                    await self.ask(
                        conn,
                        plan["group_id"],
                        ["执行前条件发生变化或期限已过，请重新确认当前配置与事项。"],
                        {"operation_id": operation_id, "code": error},
                    )
                return
            if plan["kind"] in ("update", "complete", "delete") or plan.get("supplement_target_id"):
                actual = await self.gateway.get(plan["project_id"], plan["task_id"])
                watched = set(plan["fields"]) | {"title", "status"}
                if plan["kind"] == "delete":
                    watched |= {"content", "dueDate", "isAllDay", "repeatFlag", "repeatFrom"}
                if any(actual.get(field) != plan["before"].get(field) for field in watched):
                    async with self.db.transaction() as conn:
                        await execute(
                            conn,
                            "UPDATE operations SET paused=1,revision=revision+1 WHERE id=:id AND state='validated'",
                            {"id": operation_id},
                        )
                        await self.ask(
                            conn,
                            plan["group_id"],
                            ["目标任务被外部编辑，请核对待改字段与含义。"],
                            {
                                "operation_id": operation_id,
                                "before": plan["before"],
                                "current": actual,
                            },
                        )
                    return
            async with self.db.transaction() as conn:
                if self.processing_expired(plan):
                    await execute(
                        conn,
                        "UPDATE operations SET paused=1,revision=revision+1 WHERE id=:id AND state='validated'",
                        {"id": operation_id},
                    )
                    await self.ask(
                        conn,
                        plan["group_id"],
                        ["处理预算已用完，已保存实际结果；剩余未开始操作待继续核验。"],
                        {"operation_id": operation_id, "code": "PROCESSING_BUDGET_EXPIRED"},
                    )
                    return
                claimed = await execute(
                    conn,
                    "UPDATE operations SET state='executing',attempt=attempt+1,revision=revision+1 WHERE id=:id AND state='validated' AND paused=0",
                    {"id": operation_id},
                )
                if claimed.rowcount != 1:
                    return
            try:
                if plan["kind"] == "upload":
                    response = await self.gateway.upload(plan)
                else:
                    response = await self.gateway.write(
                        plan["kind"], plan["project_id"], plan["fields"], plan.get("task_id")
                    )
                remote_id = response.value.get("id") if isinstance(response.value, dict) else None
                if plan["kind"] in ("update", "complete", "delete"):
                    remote_id = plan["task_id"]
                reliable = isinstance(remote_id, str) and bool(remote_id)
                if response.error:
                    state = (
                        "uploaded_unverified"
                        if reliable and plan["kind"] == "upload"
                        else "failed_safe"
                        if response.side_effect == "none"
                        else "outcome_unknown"
                    )
                elif not reliable:
                    state = "outcome_unknown"
                else:
                    state = {"create": "created_unverified", "upload": "uploaded_unverified"}.get(
                        plan["kind"], "applied_unverified"
                    )
                result = {
                    "contract_version": 1,
                    "operation_id": operation_id,
                    "kind": plan["kind"],
                    "status": state,
                    "side_effect": response.side_effect,
                    "account_ref": plan["account_ref"],
                    "remote_id": remote_id if reliable else None,
                    "actual_fields": {},
                    "verification": {},
                    "error": {"code": response.error} if response.error else None,
                }
                # Save reliable ID immediately, before any verification call.
                async with self.db.transaction() as conn:
                    await execute(
                        conn,
                        "UPDATE operations SET state=:s,remote_id=:remote,result=:r,revision=revision+1 WHERE id=:id AND state='executing'",
                        {
                            "id": operation_id,
                            "s": state,
                            "remote": remote_id if reliable else None,
                            "r": canonical(result),
                        },
                    )
                if state in ("created_unverified", "uploaded_unverified", "applied_unverified"):
                    await self._reconcile({"operation_id": operation_id})
                else:
                    async with self.db.transaction() as conn:
                        await self.operation_receipt(conn, operation_id, state, plan, result)
            except asyncio.CancelledError:
                async with self.db.transaction() as conn:
                    await execute(
                        conn,
                        "UPDATE operations SET state='outcome_unknown',revision=revision+1 WHERE id=:id AND state='executing'",
                        {"id": operation_id},
                    )
                raise
            except Exception:
                # Recording failure after a remote write cannot prove absence of side effects.
                self.maintenance = "RESULT_PERSISTENCE_OR_VERIFICATION_FAILED"
                try:
                    async with self.db.transaction() as conn:
                        await execute(
                            conn,
                            "UPDATE operations SET state='outcome_unknown',revision=revision+1 WHERE id=:id AND state='executing'",
                            {"id": operation_id},
                        )
                except Exception:
                    pass
                raise

    @staticmethod
    def fields_match(actual, expected):
        for field, value in expected.items():
            if field in ("dueDate", "startDate") and value is not None and actual.get(field):
                try:
                    left = datetime.fromisoformat(actual[field].replace("Z", "+00:00"))
                    right = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if left != right:
                        return False
                except ValueError:
                    return False
            elif field == "content":
                if (actual.get(field) or "") != (value or ""):
                    return False
            elif field == "repeatFlag":
                from .recurrence import canonical_rule

                if canonical_rule(actual.get(field)) != canonical_rule(value):
                    return False
            elif field == "isAllDay" and value is False:
                if actual.get(field, False) is not False:
                    return False
            elif actual.get(field) != value:
                return False
        return True

    async def reconcile(self, payload):
        async with self.write_lock:
            await self._reconcile(payload)

    async def _reconcile(self, payload):
        operation_id = payload["operation_id"]
        operation = (
            await self.db.read("SELECT * FROM operations WHERE id=:id", {"id": operation_id})
        )[0]
        if operation["state"] in (
            "succeeded",
            "cancelled",
            "validated",
            "failed_safe",
            "executing",
        ):
            return
        plan = json.loads(operation["plan"])
        settings, _ = await self.db.settings()
        if settings.account_ref != plan["account_ref"]:
            raise NotiDoError("ACCOUNT_MISMATCH", "旧操作必须在原账号核查，不能在新账号重建。")
        result = json.loads(operation["result"] or "{}")
        if not operation["remote_id"] and plan["kind"] in ("update", "complete", "delete"):
            # The selected existing target survives a crash before stdout/ID storage.
            # Recover the lookup identity only; a separate read must prove the effect.
            async with self.db.transaction() as conn:
                await execute(
                    conn,
                    "UPDATE operations SET remote_id=:t,revision=revision+1 WHERE id=:id AND remote_id IS NULL",
                    {"t": plan["task_id"], "id": operation_id},
                )
            operation["remote_id"] = plan["task_id"]
            result["remote_id"] = plan["task_id"]
        if not operation["remote_id"] and plan["kind"] == "upload" and plan.get("attachment_id"):
            # A preallocated registration ID is a lookup candidate, never evidence of success.
            # Only downloading the bytes from the exact target can promote it to a real ID.
            try:
                candidate = await self.gateway.inspect_upload(plan, plan["attachment_id"])
            except NotiDoError:
                candidate = None
            if (
                candidate
                and candidate.get("task_id") == plan["task_id"]
                and (
                    candidate.get("sha256") == plan["hash"]
                    or candidate.get("id") == plan["attachment_id"]
                    and candidate.get("project_id") == plan["project_id"]
                )
            ):
                async with self.db.transaction() as conn:
                    saved = await execute(
                        conn,
                        "UPDATE operations SET remote_id=:r,state='uploaded_unverified',revision=revision+1 WHERE id=:id AND remote_id IS NULL AND state='outcome_unknown'",
                        {"id": operation_id, "r": plan["attachment_id"]},
                    )
                if saved.rowcount != 1:
                    return
                operation["remote_id"], operation["state"] = (
                    plan["attachment_id"],
                    "uploaded_unverified",
                )
                result["remote_id"] = plan["attachment_id"]
        if not operation["remote_id"]:
            async with self.db.transaction() as conn:
                await execute(
                    conn,
                    "UPDATE operations SET checked_at=:t,revision=revision+1 WHERE id=:id",
                    {"t": time.time(), "id": operation_id},
                )
                await self.operation_receipt(conn, operation_id, operation["state"], plan, result)
            return
        try:
            if plan["kind"] == "delete":
                probe = await self.gateway.probe_task(plan["project_id"], plan["task_id"])
                actual = {
                    **probe,
                    "deleted": not probe["exists"],
                    "title": plan["before"].get("title"),
                }
                verified = probe["exists"] is False
            elif plan["kind"] == "upload":
                actual = await self.gateway.inspect_upload(plan, operation["remote_id"])
                verified = (
                    actual.get("sha256") == plan["hash"]
                    and actual.get("task_id") == plan["task_id"]
                )
            else:
                actual = await self.gateway.get(plan["project_id"], operation["remote_id"])
                verified = (
                    actual.get("status") == 2
                    if plan["kind"] == "complete"
                    else self.fields_match(actual, plan["fields"])
                )
        except NotiDoError:
            actual, verified = {}, False
        result.update(
            {
                "actual_fields": actual,
                "verification": {"checked_at": datetime.now(UTC).isoformat(), "verified": verified},
            }
        )
        if actual and not verified:
            result["error"] = {"code": "RESULT_MISMATCH"}
        if verified:
            result.update({"status": "succeeded", "side_effect": "applied", "error": None})
        async with self.db.transaction() as conn:
            current = await one(
                conn, "SELECT state FROM operations WHERE id=:id", {"id": operation_id}
            )
            if current["state"] == "succeeded":
                return
            await execute(
                conn,
                "UPDATE operations SET state=:s,result=:r,checked_at=:t,revision=revision+1 WHERE id=:id",
                {
                    "id": operation_id,
                    "s": "succeeded" if verified else current["state"],
                    "r": canonical(result),
                    "t": time.time(),
                },
            )
            if verified and plan["kind"] in ("create", "update") and plan.get("action_id"):
                await execute(
                    conn,
                    "INSERT INTO notice_task_links VALUES (:id,:n,:act,:a,:p,:task,:s) ON CONFLICT(action_id,account_ref,project_id,task_id) DO UPDATE SET snapshot=excluded.snapshot",
                    {
                        "id": uid(),
                        "n": plan.get("notice_id"),
                        "act": plan["action_id"],
                        "a": plan["account_ref"],
                        "p": plan["project_id"],
                        "task": operation["remote_id"],
                        "s": canonical(
                            {
                                **actual,
                                "_notido": {"source_published_at": plan.get("source_published_at")},
                            }
                        ),
                    },
                )
            if verified and plan["kind"] == "create":
                siblings = await one(
                    conn,
                    "SELECT count(*) AS n FROM operations WHERE json_extract(plan,'$.group_id')=:g AND kind='create'",
                    {"g": plan["group_id"]},
                )
                if siblings["n"] == 1:
                    recent = {
                        "recent_ref": uid(),
                        "account_ref": plan["account_ref"],
                        "project_id": plan["project_id"],
                        "task_id": operation["remote_id"],
                        "expires_at": time.time() + 1800,
                    }
                    await execute(
                        conn,
                        "UPDATE sessions SET recent=:r,revision=revision+1 WHERE id=:s",
                        {"r": canonical(recent), "s": plan["session_key"]},
                    )
                else:
                    await execute(
                        conn,
                        "UPDATE sessions SET recent=NULL,revision=revision+1 WHERE id=:s",
                        {"s": plan["session_key"]},
                    )
                for asset_id in plan.get("attachment_asset_ids", []):
                    await self.schedule_upload(conn, plan, operation["remote_id"], asset_id)
            if verified and plan["kind"] == "upload":
                await execute(
                    conn,
                    "UPDATE task_attachment_links SET remote_id=:r,verified=1 WHERE operation_id=:id",
                    {"r": operation["remote_id"], "id": operation_id},
                )
            await self.operation_receipt(
                conn, operation_id, "succeeded" if verified else current["state"], plan, result
            )
            remaining = await one(
                conn,
                "SELECT count(*) AS n FROM operations WHERE json_extract(plan,'$.group_id')=:g AND state!='succeeded' AND state!='cancelled'",
                {"g": plan["group_id"]},
            )
            session = await one(
                conn, "SELECT question FROM sessions WHERE id=:s", {"s": plan["session_key"]}
            )
            question = json.loads(session["question"] or "null")
            group = await one(
                conn, "SELECT state FROM material_groups WHERE id=:g", {"g": plan["group_id"]}
            )
            if (
                remaining["n"] == 0
                and plan.get("delivery_mode") != "framework_tool"
                and group["state"] != "awaiting_clarification"
                and (not question or question["group_id"] != plan["group_id"])
            ):
                await self.finish_group(conn, plan["group_id"])
        self.wakeup.set()

    async def ensure_task_active(self, account_ref, project_id, task_id):
        deletions = await self.db.read(
            "SELECT state FROM operations WHERE account_ref=:a AND coalesce(json_extract(plan,'$.task_id'),remote_id)=:t AND json_extract(plan,'$.project_id')=:p AND kind='delete' AND state IN ('executing','outcome_unknown','applied_unverified','succeeded') ORDER BY created_at DESC LIMIT 1",
            {"a": account_ref, "p": project_id, "t": task_id},
        )
        if not deletions:
            return
        if deletions[0]["state"] != "succeeded":
            raise NotiDoError(
                "DELETE_RESULT_PENDING",
                "目标存在待核查的删除，请先核查该操作，不继续修改、完成或上传。",
            )
        probe = await self.gateway.probe_task(project_id, task_id)
        if not probe["exists"]:
            raise NotiDoError(
                "TARGET_DELETED", "目标已删除，不能以详情接口仍返回记录为由继续操作或重建。"
            )

    async def schedule_upload(self, conn, parent, task_id, asset_id):
        asset = await one(
            conn,
            "SELECT a.*,b.path,b.size FROM assets a JOIN blobs b ON b.hash=a.hash WHERE a.id=:id AND a.user_id='personal' AND a.state='ready'",
            {"id": asset_id},
        )
        if not asset:
            raise NotiDoError("ASSET_UNAVAILABLE", "附件原件未取得。")
        operation_key = key(
            "personal",
            parent["account_ref"],
            parent["project_id"],
            task_id,
            asset["hash"],
            "upload",
        )
        operation_id = uid()
        names = await rows(
            conn,
            "SELECT a.name FROM task_attachment_links l JOIN assets a ON a.id=l.asset_id WHERE l.account_ref=:a AND l.project_id=:p AND l.task_id=:t AND l.hash!=:h",
            {
                "a": parent["account_ref"],
                "p": parent["project_id"],
                "t": task_id,
                "h": asset["hash"],
            },
        )
        display_name = asset["name"]
        if any(x["name"] == display_name for x in names):
            name_path = Path(display_name)
            display_name = f"{name_path.stem}-{asset['hash'][:8]}{name_path.suffix}"
        if len(display_name) > 200:
            raise NotiDoError(
                "FILENAME_TOO_LONG", "同名原件加内容后缀后超过名称上限，请提供较短原名。"
            )
        plan = {
            **parent,
            "plan_id": uid(),
            "kind": "upload",
            "task_id": task_id,
            "attachment_id": uid().replace("-", "")[:24],
            "asset_id": asset_id,
            "hash": asset["hash"],
            "name": display_name,
            "blob_path": str(self.blobs.path(asset["path"])),
            "size": asset["size"],
            "fields": {},
        }
        result = await execute(
            conn,
            "INSERT OR IGNORE INTO operations (id,user_id,account_ref,kind,operation_key,plan,state,created_at) VALUES (:id,'personal',:a,'upload',:k,:p,'validated',:t)",
            {
                "id": operation_id,
                "a": parent["account_ref"],
                "k": operation_key,
                "p": canonical(plan),
                "t": time.time(),
            },
        )
        if result.rowcount:
            await execute(
                conn,
                "INSERT INTO task_attachment_links VALUES (:id,:a,:p,:task,:h,:asset,NULL,:op,0)",
                {
                    "id": uid(),
                    "a": parent["account_ref"],
                    "p": parent["project_id"],
                    "task": task_id,
                    "h": asset["hash"],
                    "asset": asset_id,
                    "op": operation_id,
                },
            )
            await self.db.job(
                conn,
                "execute_operation",
                f"execute:{operation_id}",
                {"operation_id": operation_id},
                priority=5,
                defer=True,
            )
            await execute(
                conn,
                "UPDATE material_groups SET state='task_saved_attachments_pending',revision=revision+1 WHERE id=:g",
                {"g": parent["group_id"]},
            )

    async def operation_receipt(self, conn, operation_id, state, plan, result):
        if plan.get("delivery_mode") == "framework_tool":
            # The native agent receives this durable result as tool output and replies.
            from .native_outcome import NativeOutcomes

            for group in {plan.get("group_id"), plan.get("source_group_id")} - {None}:
                await NativeOutcomes(self).refresh(conn, group)
            return
        title = (
            plan["fields"].get("title")
            or plan.get("name")
            or plan.get("before", {}).get("title")
            or plan.get("task_id")
            or "事项"
        )
        remote_id = result.get("remote_id")
        if state == "succeeded":
            verb = {
                "create": "已保存并核验",
                "update": "已修改并核验",
                "complete": "已完成并核验",
                "upload": "原件已上传并核验",
            }[plan["kind"]]
            body = f"{verb}：{title}\n真实 ID：{remote_id}"
            value = plan.get("normalized_date")
            if value:
                label = "活动时间" if value["time_kind"] == "event" else "到期时间"
                body += f"\n{label}：" + (
                    "无日期"
                    if value["kind"] == "none"
                    else value["local_date"]
                    + (" 全天" if value["is_all_day"] else " " + value["local_time"])
                )
            if plan["fields"].get("content"):
                requirements = plan.get("requirements")
                body += (
                    "\n要求：" + "；".join(requirements)
                    if requirements
                    else "\n要求已写入任务备注。"
                )
            if plan.get("attachment_asset_ids") and plan["kind"] == "create":
                body += "\n原件附件另行上传核验，结果尚待处理。"
        elif state == "failed_safe":
            body = f"未执行：{title}。原因：{result.get('error', {}).get('code', 'DEPENDENCY_UNAVAILABLE')}。修复后可安全重试。"
        else:
            body = (
                f"{title} 的结果待核查（{state}）。"
                + (f"已保留真实 ID：{remote_id}。" if remote_id else "")
                + "不会自动重做新增/上传。"
            )
        body += "\n清单：" + (plan.get("project_name") or plan["project_id"])
        if plan["kind"] == "upload":
            body += f"\n关联任务 ID：{plan['task_id']}"
        await self.db.receipt(
            conn,
            origin=plan["origin"],
            body=body,
            dedupe=f"operation:{operation_id}:{state}",
            operation_id=operation_id,
        )

    async def send_receipt(self, payload):
        receipt = (
            await self.db.read(
                "SELECT * FROM receipt_records WHERE id=:id", {"id": payload["receipt_id"]}
            )
        )[0]
        if receipt["state"] == "sent":
            return
        state = await self.bridge.reply(receipt["origin"], receipt["body"])
        async with self.db.transaction() as conn:
            await execute(
                conn,
                "UPDATE receipt_records SET state=:s,attempt=attempt+1,revision=revision+1 WHERE id=:id",
                {"s": state, "id": receipt["id"]},
            )

    async def processing_budget(self, group_id):
        """Persist the original wall deadline, then use monotonic time during this owner run."""
        if group_id not in self.processing_deadlines:
            settings, _ = await self.db.settings()
            async with self.db.transaction() as conn:
                record = await one(
                    conn, "SELECT * FROM processing_budgets WHERE group_id=:g", {"g": group_id}
                )
                if record is None:
                    materials = await rows(
                        conn,
                        "SELECT a.name,b.size FROM assets a LEFT JOIN blobs b ON b.hash=a.hash WHERE a.group_id=:g",
                        {"g": group_id},
                    )
                    texts = await one(
                        conn,
                        "SELECT coalesce(sum(length(text)),0) AS n FROM material_segments WHERE group_id=:g",
                        {"g": group_id},
                    )
                    long = texts["n"] > settings.materials.block_characters or any(
                        (x["size"] or 0) > 1024**2
                        and Path(x["name"]).suffix.lower() in (".pdf", ".docx")
                        for x in materials
                    )
                    seconds = (
                        settings.time_budgets.long_seconds
                        if long
                        else settings.time_budgets.material_seconds
                        if materials
                        else settings.time_budgets.text_seconds
                    )
                    now = time.time()
                    record = {"deadline_at": now + seconds}
                    await execute(
                        conn,
                        "INSERT INTO processing_budgets VALUES (:g,:t,:d,:s)",
                        {"g": group_id, "t": now, "d": record["deadline_at"], "s": seconds},
                    )
            self.processing_deadlines[group_id] = time.monotonic() + max(
                0, record["deadline_at"] - time.time()
            )
        return self.processing_deadlines[group_id]

    async def remaining_budget(self, group_id):
        remaining = await self.processing_budget(group_id) - time.monotonic()
        if remaining <= 0:
            raise NotiDoError(
                "PROCESSING_BUDGET_EXPIRED",
                "处理预算已用完，材料和实际结果已保留；请从待处理页继续核验。",
            )
        return remaining

    def processing_expired(self, plan):
        deadline = plan.get("processing_deadline_at")
        if deadline is None:
            return False
        cache_key = (
            ("webui", plan["plan_id"])
            if plan.get("request_origin") == "webui"
            else plan["group_id"]
        )
        if cache_key not in self.processing_deadlines:
            self.processing_deadlines[cache_key] = time.monotonic() + max(0, deadline - time.time())
        return time.monotonic() >= self.processing_deadlines[cache_key] or time.time() >= deadline
