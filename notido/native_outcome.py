"""Persist AstrBot's conclusion; derive progress only from material and operation facts."""

import json
import time
from typing import Literal

from pydantic import Field, model_validator

from .db import execute, one, rows
from .errors import NotiDoError
from .models import StrictModel


class RequiredAttachment(StrictModel):
    asset_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=200)


class OutcomeArgs(StrictModel):
    group_id: str | None = Field(default=None, min_length=1, max_length=200)
    state: Literal[
        "completed", "awaiting_materials", "awaiting_clarification", "attachments_pending"
    ]
    pending_reason: str = Field(default="", max_length=2000)
    attachments: list[RequiredAttachment] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def reason_required(self):
        if self.state != "completed" and not self.pending_reason.strip():
            raise ValueError("pending_reason is required for an unresolved conclusion")
        if self.state == "completed" and self.pending_reason.strip():
            raise ValueError("a completed conclusion cannot contain an unresolved reason")
        return self


class NativeOutcomes:
    def __init__(self, service):
        self.service = service

    async def record(self, identity, settings, message_key, data):
        async with self.service.db.transaction() as conn:
            group_id = data.group_id
            if group_id is None:
                message = await one(
                    conn,
                    "SELECT group_id FROM message_records WHERE instance_id=:i AND session_key=:s AND message_key=:m",
                    {**identity, "m": message_key},
                )
                group_id = message["group_id"] if message else None
            context = await one(
                conn,
                "SELECT * FROM native_group_outcomes WHERE group_id=:g AND account_ref=:account AND credential_generation=:generation AND instance_id=:i AND actor_key=:a AND session_key=:s",
                {
                    **identity,
                    "g": group_id,
                    "account": settings.account_ref,
                    "generation": settings.credential_generation,
                },
            )
            if context is None:
                raise NotiDoError(
                    "MATERIAL_NOT_FOUND",
                    "未找到当前账号、身份与会话的材料组；先使用材料或任务工具取得真实引用。",
                )
            owned = await self.operations(conn, context)
            targets = set()
            for operation in owned:
                plan = json.loads(operation["plan"])
                task = (
                    operation["remote_id"] if operation["kind"] == "create" else plan.get("task_id")
                )
                if task and plan.get("project_id"):
                    targets.add((plan["project_id"], task))
            for attachment in data.attachments:
                asset = await one(
                    conn,
                    "SELECT id FROM assets WHERE id=:a AND group_id=:g",
                    {"a": attachment.asset_id, "g": group_id},
                )
                if (
                    asset is None
                    or attachment.project_id not in settings.allowed_projects
                    or (attachment.project_id, attachment.task_id) not in targets
                ):
                    raise NotiDoError(
                        "ATTACHMENT_TARGET_INVALID",
                        "待挂原件须属于本组，目标须为本组真实操作的任务，不能用任意引用标记完成。",
                    )
            await execute(
                conn,
                "UPDATE native_group_outcomes SET declaration=:d,updated_at=:t WHERE group_id=:g",
                {"g": group_id, "d": data.model_dump_json(), "t": time.time()},
            )
            return await self.refresh(conn, group_id)

    async def operations(self, conn, context):
        return await rows(
            conn,
            """SELECT o.* FROM operations o
            WHERE o.account_ref=:account
              AND json_extract(o.plan,'$.delivery_mode')='framework_tool'
              AND json_extract(o.plan,'$.framework_instance_id')=:i
              AND json_extract(o.plan,'$.actor_key')=:a
              AND (
                (json_extract(o.plan,'$.session_key')=:s AND
                 (json_extract(o.plan,'$.group_id')=:g OR json_extract(o.plan,'$.source_group_id')=:g))
                OR EXISTS (
                  SELECT 1 FROM native_tool_calls c JOIN message_records m
                    ON m.session_key=c.session_key AND m.message_key=c.message_key
                  WHERE c.operation_id=o.id AND c.session_key=:s
                    AND m.instance_id=:i AND m.group_id=:g)
                OR EXISTS (
                  SELECT 1 FROM delete_confirmations d JOIN message_records m
                    ON m.session_key=d.session_key AND m.message_key=d.message_key
                    AND m.instance_id=d.instance_id
                  WHERE d.consumed_by=o.id AND d.session_key=:s
                    AND d.instance_id=:i AND d.actor_key=:a AND m.group_id=:g)
              )""",
            {
                "g": context["group_id"],
                "account": context["account_ref"],
                "i": context["instance_id"],
                "a": context["actor_key"],
                "s": context["session_key"],
            },
        )

    async def invalidate(self, conn, group_id):
        context = await one(
            conn, "SELECT group_id FROM native_group_outcomes WHERE group_id=:g", {"g": group_id}
        )
        if context:
            await execute(
                conn,
                "UPDATE native_group_outcomes SET declaration=NULL,updated_at=:t WHERE group_id=:g",
                {"g": group_id, "t": time.time()},
            )
            await self.refresh(conn, group_id)

    async def refresh(self, conn, group_id):
        context = await one(
            conn, "SELECT * FROM native_group_outcomes WHERE group_id=:g", {"g": group_id}
        )
        if context is None:
            return None  # Historical records are not retroactively declared by the native agent.
        declaration = json.loads(context["declaration"]) if context["declaration"] else None
        operations = await self.operations(conn, context)
        saved = any(
            o["state"] == "succeeded" and o["kind"] in ("create", "update") for o in operations
        )
        unresolved = [o for o in operations if o["state"] not in ("succeeded", "cancelled")]
        pending_upload = any(o["kind"] == "upload" for o in unresolved)
        missing_attachments = []
        for attachment in (declaration or {}).get("attachments", []):
            link = await one(
                conn,
                "SELECT l.id FROM task_attachment_links l JOIN assets a ON a.id=:asset AND a.hash=l.hash JOIN operations o ON o.id=l.operation_id AND o.state='succeeded' WHERE l.verified=1 AND l.account_ref=:account AND l.project_id=:project AND l.task_id=:task",
                {
                    "asset": attachment["asset_id"],
                    "account": context["account_ref"],
                    "project": attachment["project_id"],
                    "task": attachment["task_id"],
                },
            )
            if link is None:
                missing_attachments.append(attachment)
        cache = await one(
            conn, "SELECT payload FROM native_material_reads WHERE group_id=:g", {"g": group_id}
        )
        unknowns, unread = [], 0
        if cache:
            payload = json.loads(cache["payload"])
            # Opaque originals (ZIP/DWG etc.) need byte/target verification,
            # not a fabricated readable body. Every other unread reason blocks.
            unknowns = [
                item
                for item in payload["unknowns"]
                if item.get("reason") != "BODY_NOT_SUPPORTED_ATTACHMENT_ONLY"
            ]
            delivered = await one(
                conn,
                "SELECT count(*) AS n FROM native_material_deliveries WHERE group_id=:g",
                {"g": group_id},
            )
            unread = max(0, len(payload["items"]) - delivered["n"])
        else:
            assets = await one(
                conn, "SELECT count(*) AS n FROM assets WHERE group_id=:g", {"g": group_id}
            )
            unread = assets["n"]
        requested = (declaration or {}).get("state")
        confirmation = await one(
            conn,
            "SELECT d.id FROM delete_confirmations d JOIN message_records m ON m.instance_id=d.instance_id AND m.session_key=d.session_key AND m.message_key=d.message_key WHERE m.group_id=:g AND d.account_ref=:a AND d.actor_key=:actor AND d.consumed_by IS NULL AND d.expires_at>:now",
            {
                "g": group_id,
                "a": context["account_ref"],
                "actor": context["actor_key"],
                "now": time.time(),
            },
        )
        if pending_upload or missing_attachments or requested == "attachments_pending":
            state = "task_saved_attachments_pending" if saved else "awaiting_materials"
        elif unresolved or confirmation:
            state = "partially_done" if saved else "awaiting_clarification"
        elif unknowns or unread:
            state = "partially_done" if saved else "awaiting_materials"
        elif requested in ("awaiting_materials", "awaiting_clarification"):
            state = "partially_done" if saved else requested
        else:
            state = "completed" if requested == "completed" else "collecting"
        await execute(
            conn,
            "UPDATE material_groups SET state=:state,revision=revision+1 WHERE id=:g AND state<>:state",
            {"g": group_id, "state": state},
        )
        return {
            "group_id": group_id,
            "state": state,
            "has_saved_work": saved,
            "declared_state": requested,
            "pending_reason": (declaration or {}).get("pending_reason", ""),
            "unresolved_operations": [
                {"operation_id": o["id"], "kind": o["kind"], "state": o["state"]}
                for o in unresolved
            ],
            "pending_attachments": missing_attachments,
            "unknown_materials": unknowns,
            "unread_items": unread,
            "remote_write": False,
            "ai_owner": "astrbot",
            "awaiting_delete_confirmation": bool(confirmation),
        }
