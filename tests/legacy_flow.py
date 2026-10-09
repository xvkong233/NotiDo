"""Retired independent planning fixtures, used only to test historical ledger invariants.
Not shipped with the plugin and not a runtime or acceptance AI implementation.
"""

import asyncio
import json
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from notido.chunks import material_blocks
from notido.dates import native_fields, normalize, overdue
from notido.db import execute, one, rows
from notido.errors import NotiDoError
from notido.keys import canonical, key, uid
from notido.models import CreateItem, InputEnvelope, Query
from notido.policy import (
    managed_notes,
    managed_region,
    replace_managed,
    resolve_project,
    verify_evidence,
)
from notido.query import page, read_scope, task_date


class LegacyFlow:
    async def intake(self, envelope: InputEnvelope):
        if self.native_ai:
            raise NotiDoError(
                "ASTRBOT_NATIVE_FLOW_REQUIRED",
                "请在 AstrBot 当前对话使用滴答函数工具或 /notido 指令；插件不再独立调用 AI。",
            )
        if self.stopping or self.maintenance:
            raise NotiDoError("MAINTENANCE", "插件维护中，暂未接收。", status=503)
        if not await self.authorized(envelope):
            raise NotiDoError("NOT_AUTHORIZED", "此框架身份/会话未授权。", status=403)
        if envelope.message_key is None:
            raise NotiDoError(
                "FRAMEWORK_ID_UNAVAILABLE", "缺少稳定消息标识，不能自动写入，请从后台提交明确请求。"
            )
        settings, config_revision = await self.db.settings()
        if shutil.disk_usage(self.root).free < settings.min_free_bytes:
            raise NotiDoError("STORAGE_FULL", "暂未接收，请稍后重发。", status=429)
        text_body = "\n".join(
            x.text or "" for x in envelope.segments if x.kind in ("text", "transcript")
        )
        if (
            len(text_body) > settings.materials.message_characters
            or len(envelope.segments) > settings.materials.message_nodes
        ):
            raise NotiDoError(
                "MATERIAL_LIMIT", "消息超过材料预算，暂未接收，请分组发送。", status=400
            )
        command = text_body.strip()
        supplement = (
            re.fullmatch(r"补充任务\s+([A-Za-z0-9_-]{1,200})", command)
            if envelope.source_kind != "user_forward"
            else None
        )
        direct = (
            supplement is not None
            or envelope.source_kind == "direct_request"
            or command.startswith(
                (
                    "记一下",
                    "记个",
                    "新建待办",
                    "添加待办",
                    "提醒我",
                    "查",
                    "列出",
                    "完成",
                    "修改",
                    "把",
                    "下一页",
                    "回答 ",
                    "补充任务 ",
                )
            )
        )
        if direct and envelope.source_kind != "user_forward":
            envelope = envelope.model_copy(update={"source_kind": "direct_request"})
        now = envelope.received_at.timestamp()
        async with self.db.transaction() as conn:
            prior = await one(
                conn,
                "SELECT id,group_id FROM message_records WHERE instance_id=:i AND session_key=:s AND message_key=:m",
                {
                    "i": envelope.framework_instance_id,
                    "s": envelope.session_key,
                    "m": envelope.message_key,
                },
            )
            if prior:
                return {"duplicate": True, **prior}
            if supplement and not settings.account_ref:
                raise NotiDoError("ACCOUNT_REQUIRED", "请先配置补件所用的滴答账号。", status=503)
            count = await one(
                conn,
                "SELECT count(*) AS n FROM message_records WHERE state IN ('admitted','pending')",
            )
            if count["n"] >= settings.max_inbox:
                raise NotiDoError("INBOX_FULL", "暂未接收，请稍后重发。", status=429)
            backlog = await one(
                conn,
                "SELECT (SELECT count(*) FROM jobs WHERE state IN ('pending','running')) + (SELECT count(*) FROM operations o WHERE state='validated' AND paused=0 AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.state IN ('pending','running') AND j.kind='execute_operation' AND json_extract(j.payload,'$.operation_id')=o.id)) AS n",
            )
            if backlog["n"] >= settings.max_jobs - min(10, max(2, settings.max_jobs // 5)):
                raise NotiDoError("QUEUE_FULL", "处理队列已满，暂未接收，请稍后重发。", status=429)
            await execute(
                conn,
                "INSERT OR IGNORE INTO sessions (id,user_id) VALUES (:s,'personal')",
                {"s": envelope.session_key},
            )
            collecting = await one(
                conn,
                "SELECT * FROM material_groups WHERE session_id=:s AND state='collecting'",
                {"s": envelope.session_key},
            )
            if command in ("收完了", "取消收集", "开始收集", "继续收集", "取消"):
                result = await self.collection_command(
                    conn, envelope, collecting, command, now, settings
                )
                await execute(
                    conn,
                    "INSERT INTO message_records VALUES (:id,'personal',:g,:i,:s,:m,:t,:e,'processed')",
                    {
                        "id": envelope.event_id,
                        "g": result.get("group_id"),
                        "i": envelope.framework_instance_id,
                        "s": envelope.session_key,
                        "m": envelope.message_key,
                        "t": now,
                        "e": envelope.model_dump_json(),
                    },
                )
                await self.db.receipt(
                    conn,
                    origin=envelope.reply_origin_ref,
                    body=result["message"],
                    dedupe=f"message:{envelope.event_id}",
                )
                self.wakeup.set()
                return result
            if collecting and not direct:
                group_id = collecting["id"]
                closes = (
                    min(
                        now + settings.silence_seconds,
                        collecting["first_at"] + settings.window_seconds,
                    )
                    if collecting["mode"] == "automatic"
                    else collecting["closes_at"]
                )
                await execute(
                    conn,
                    "UPDATE material_groups SET last_at=:t,closes_at=:c,revision=revision+1 WHERE id=:g",
                    {"t": now, "c": closes, "g": group_id},
                )
            else:
                group_id = uid()
                mode, state = (
                    ("direct", "awaiting_materials") if direct else ("automatic", "collecting")
                )
                closes = now if direct else now + settings.silence_seconds
                await execute(
                    conn,
                    "INSERT INTO material_groups VALUES (:g,'personal',:s,:mode,:state,:t,:t,:c,0)",
                    {
                        "g": group_id,
                        "s": envelope.session_key,
                        "mode": mode,
                        "state": state,
                        "t": now,
                        "c": closes,
                    },
                )
            await execute(
                conn,
                "INSERT INTO message_records VALUES (:id,'personal',:g,:i,:s,:m,:t,:e,'admitted')",
                {
                    "id": envelope.event_id,
                    "g": group_id,
                    "i": envelope.framework_instance_id,
                    "s": envelope.session_key,
                    "m": envelope.message_key,
                    "t": now,
                    "e": envelope.model_dump_json(),
                },
            )
            if supplement:
                await execute(
                    conn,
                    "INSERT INTO material_task_targets VALUES (:g,:a,:gen,:r,:t)",
                    {
                        "g": group_id,
                        "a": settings.account_ref,
                        "gen": settings.credential_generation,
                        "r": config_revision,
                        "t": supplement[1],
                    },
                )
            assets = await one(
                conn, "SELECT count(*) AS n FROM assets WHERE group_id=:g", {"g": group_id}
            )
            media = [x for x in envelope.segments if x.kind in ("image", "file")]
            if assets["n"] + len(media) > settings.materials.group_files:
                raise NotiDoError(
                    "ASSET_COUNT_LIMIT", "单组原件超过配置数量，此消息暂未接收。", status=429
                )
            for segment in envelope.segments:
                if segment.kind in ("text", "transcript"):
                    await self.add_segment(
                        conn, group_id, segment.source_id, "message", segment.text or ""
                    )
                elif segment.kind in ("image", "file"):
                    asset = uid()
                    await execute(
                        conn,
                        "INSERT INTO assets VALUES (:id,'personal',:g,:m,NULL,:name,:source,'pending',NULL,:t,0)",
                        {
                            "id": asset,
                            "g": group_id,
                            "m": envelope.event_id,
                            "name": "框架材料",
                            "source": segment.source_id,
                            "t": now,
                        },
                    )
                    await execute(
                        conn,
                        "INSERT INTO media_acquisitions VALUES (:id,:a,'pending',NULL,0)",
                        {"id": uid(), "a": asset},
                    )
                    await self.db.job(
                        conn,
                        "acquire_material",
                        f"acquire:{asset}",
                        {"asset_id": asset},
                        priority=2,
                    )
                else:
                    await self.add_segment(
                        conn,
                        group_id,
                        segment.source_id,
                        "message",
                        "",
                        "unknown",
                        segment.unavailable_reason or "MATERIAL_UNAVAILABLE",
                    )
            await self.db.job(
                conn,
                "close_group",
                f"close:{group_id}",
                {"group_id": group_id},
                available_at=closes,
            )
        self.wakeup.set()
        return {"group_id": group_id, "duplicate": False}

    async def collection_command(self, conn, envelope, group, command, now, settings):
        if command == "开始收集":
            if group:
                return {"group_id": group["id"], "message": "已在收集。"}
            group_id = uid()
            await execute(
                conn,
                "INSERT INTO material_groups VALUES (:g,'personal',:s,'explicit','collecting',:t,:t,:c,0)",
                {"g": group_id, "s": envelope.session_key, "t": now, "c": now + 600},
            )
            await self.db.job(
                conn,
                "close_group",
                f"close:{group_id}",
                {"group_id": group_id},
                available_at=now + 600,
            )
            return {
                "group_id": group_id,
                "message": "开始收集材料；发送“收完了”后处理，10 分钟未结束将保留草稿。",
            }
        if command == "继续收集" and not group:
            previous = await one(
                conn,
                "SELECT * FROM material_groups WHERE session_id=:s AND mode='explicit' AND state='awaiting_clarification' ORDER BY first_at DESC LIMIT 1",
                {"s": envelope.session_key},
            )
            if previous:
                await execute(
                    conn,
                    "UPDATE material_groups SET state='collecting',closes_at=:t,revision=revision+1 WHERE id=:g",
                    {"t": now + 600, "g": previous["id"]},
                )
                await self.db.job(
                    conn,
                    "close_group",
                    f"resume:{previous['id']}:{now}",
                    {"group_id": previous["id"]},
                    available_at=now + 600,
                )
                return {"group_id": previous["id"], "message": "已恢复收集。"}
        if command == "取消":
            # Cancel only validated plans in this session. Race with claim is conditional.
            result = await execute(
                conn,
                "UPDATE operations SET state='cancelled',revision=revision+1 WHERE state='validated' AND json_extract(plan,'$.session_key')=:s",
                {"s": envelope.session_key},
            )
            running = await one(
                conn,
                "SELECT count(*) AS n FROM operations WHERE state='executing' AND json_extract(plan,'$.session_key')=:s",
                {"s": envelope.session_key},
            )
            written = await rows(
                conn,
                "SELECT plan,remote_id FROM operations WHERE state='succeeded' AND json_extract(plan,'$.session_key')=:s ORDER BY created_at DESC,id DESC LIMIT 20",
                {"s": envelope.session_key},
            )
            preserved = []
            for item in written:
                plan = json.loads(item["plan"])
                title = (
                    plan.get("fields", {}).get("title")
                    or plan.get("before", {}).get("title")
                    or plan.get("name")
                    or "已写入事项"
                )
                preserved.append(f"{title} · 真实 ID：{item['remote_id']}")
            return {
                "message": f"已取消 {result.rowcount} 个尚未开始的操作。"
                + ("已有操作执行中，实际结果会保留。" if running["n"] else "")
                + ("\n已写入结果保留（最近 20 项）：\n" + "\n".join(preserved) if preserved else "")
            }
        if not group:
            return {"message": "此会话没有活动收集组；旧材料仍可在后台查看。"}
        if command == "取消收集":
            await execute(
                conn,
                "UPDATE material_groups SET state='cancelled',revision=revision+1 WHERE id=:g AND state='collecting'",
                {"g": group["id"]},
            )
            return {
                "group_id": group["id"],
                "message": "已取消未开始处理的材料组，保留已接收原件。",
            }
        await execute(
            conn,
            "UPDATE material_groups SET state='awaiting_materials',revision=revision+1 WHERE id=:g",
            {"g": group["id"]},
        )
        await self.db.job(conn, "read_materials", f"read:{group['id']}", {"group_id": group["id"]})
        return {"group_id": group["id"], "message": "收集结束，等待必要原件取得并读取。"}

    async def close_group(self, payload):
        async with self.db.transaction() as conn:
            group = await one(
                conn, "SELECT * FROM material_groups WHERE id=:g", {"g": payload["group_id"]}
            )
            if not group or group["state"] not in ("collecting", "awaiting_materials"):
                return
            if group["state"] == "awaiting_materials" and await one(
                conn,
                "SELECT id FROM jobs WHERE kind='read_materials' AND json_extract(payload,'$.group_id')=:g LIMIT 1",
                {"g": group["id"]},
            ):
                return
            if group["state"] == "collecting" and group["closes_at"] > time.time():
                await self.db.job(
                    conn,
                    "close_group",
                    f"close:{group['id']}:{group['revision']}",
                    payload,
                    available_at=group["closes_at"],
                )
                return
            if group["mode"] == "explicit" and group["state"] == "collecting":
                await self.ask(
                    conn,
                    group["id"],
                    ["显式收集已过期，材料已保留。请发送“继续收集”或在后台关闭此组。"],
                    {},
                )
                return
            if group["state"] == "collecting":
                await execute(
                    conn,
                    "UPDATE material_groups SET state='awaiting_materials',revision=revision+1 WHERE id=:g AND state='collecting'",
                    {"g": group["id"]},
                )
            await self.db.job(conn, "read_materials", f"read:{group['id']}", payload)

    async def read_materials(self, payload):
        if self.native_ai:
            raise NotiDoError(
                "ASTRBOT_NATIVE_FLOW_REQUIRED", "材料请通过 AstrBot 原生材料工具读取。"
            )
        group_id = payload["group_id"]
        if await self.restored_pipeline_stale(payload):
            return
        groups = await self.db.read(
            "SELECT state FROM material_groups WHERE id=:g", {"g": group_id}
        )
        if not groups or groups[0]["state"] != "awaiting_materials":
            return
        await self.processing_budget(group_id)
        assets = await self.db.read(
            "SELECT a.*,b.path FROM assets a LEFT JOIN blobs b ON a.hash=b.hash WHERE group_id=:g",
            {"g": group_id},
        )
        if any(x["state"] == "pending" for x in assets):
            async with self.db.transaction() as conn:
                await self.db.job(
                    conn,
                    "read_materials",
                    f"read-wait:{group_id}:{uid()}",
                    payload,
                    available_at=time.time() + 1,
                )
            return
        settings, _ = await self.db.settings()
        if await self.supplement_group(group_id):
            return
        total_visual, screenshots, pdf_visual, docx_visual = 0, 0, 0, 0
        for asset in assets:
            if asset["state"] != "ready":
                async with self.db.transaction() as conn:
                    await self.add_segment(
                        conn, group_id, asset["source_id"], "asset", "", "unknown", asset["error"]
                    )
                continue
            async with self.material_limit:
                result = await asyncio.wait_for(
                    self.blobs.read(
                        self.blobs.path(asset["path"]),
                        asset["name"],
                        budget=settings.materials,
                        deadline_seconds=settings.time_budgets.read_seconds,
                    ),
                    await self.remaining_budget(group_id),
                )
            async with self.db.transaction() as conn:
                for segment in result["segments"]:
                    await self.add_segment(
                        conn, group_id, asset["source_id"], segment["location"], segment["text"]
                    )
                for index, reason in enumerate(result["unknowns"]):
                    await self.add_segment(
                        conn,
                        group_id,
                        asset["source_id"],
                        f"unknown:{index}",
                        "",
                        "unknown",
                        reason,
                    )
            for visual in result["visuals"]:
                total_visual += 1
                suffix = Path(asset["name"]).suffix.lower()
                if suffix == ".pdf":
                    pdf_visual += 1
                elif suffix == ".docx":
                    docx_visual += 1
                else:
                    screenshots += 1
                try:
                    if (
                        total_visual > settings.materials.total_visuals
                        or screenshots > settings.materials.screenshots
                        or pdf_visual > settings.materials.pdf_visuals
                        or docx_visual > settings.materials.docx_visuals
                    ):
                        raise NotiDoError("VISUAL_LIMIT", "图像读取超出预算，部分材料未读。")
                    async with self.model_limit:
                        value = await asyncio.wait_for(
                            self.bridge.read_visual(
                                visual["path"],
                                visual["location"],
                                settings.provider_id or self.bridge.provider_id,
                                deadline_seconds=settings.time_budgets.provider_seconds,
                            ),
                            await self.remaining_budget(group_id),
                        )
                    async with self.db.transaction() as conn:
                        await self.add_segment(
                            conn,
                            group_id,
                            asset["source_id"],
                            f"{visual['location']}:visual",
                            value["text"],
                        )
                        if value["critical_unknowns"]:
                            await self.add_segment(
                                conn,
                                group_id,
                                asset["source_id"],
                                f"{visual['location']}:uncertain",
                                "",
                                "unknown",
                                canonical(value["critical_unknowns"]),
                            )
                except Exception as exc:
                    async with self.db.transaction() as conn:
                        await self.add_segment(
                            conn,
                            group_id,
                            asset["source_id"],
                            f"{visual['location']}:unread",
                            "",
                            "unknown",
                            exc.code if isinstance(exc, NotiDoError) else "VISUAL_UNREADABLE",
                        )
        async with self.db.transaction() as conn:
            await self.db.job(
                conn,
                "parse_group",
                f"parse:{group_id}:{payload.get('resume_id', 'base')}:{payload.get('read_ref', 'base')}",
                payload,
            )

    async def parse_group(self, payload):
        if self.native_ai:
            raise NotiDoError(
                "ASTRBOT_NATIVE_FLOW_REQUIRED", "通知理解与追问由 AstrBot 原生会话负责。"
            )
        group_id = payload["group_id"]
        if await self.restored_pipeline_stale(payload):
            return
        group = (await self.db.read("SELECT * FROM material_groups WHERE id=:g", {"g": group_id}))[
            0
        ]
        if group["state"] in (
            "cancelled",
            "completed",
            "task_saved_attachments_pending",
            "partially_done",
        ):
            return
        await self.processing_budget(group_id)
        if await self.supplement_group(group_id):
            return
        messages = await self.db.read(
            "SELECT * FROM message_records WHERE group_id=:g ORDER BY received_at,id",
            {"g": group_id},
        )
        envelopes = [InputEnvelope.model_validate_json(x["envelope"]) for x in messages]
        segments = await self.db.read(
            "SELECT * FROM material_segments WHERE group_id=:g ORDER BY source_id,location",
            {"g": group_id},
        )
        assets = await self.db.read(
            "SELECT id,name,hash,state,source_id FROM assets WHERE group_id=:g", {"g": group_id}
        )
        settings, config_revision = await self.db.settings()
        session = (
            await self.db.read("SELECT * FROM sessions WHERE id=:s", {"s": group["session_id"]})
        )[0]
        origin = envelopes[0].reply_origin_ref
        text_body = "\n".join(x["text"] for x in segments if x["state"] == "read")
        if text_body.strip() == "下一页":
            await self.next_page(group["session_id"], origin)
            return
        active_question = (
            json.loads(session["question"])
            if session["question"] and (session["question_expires"] or 0) > time.time()
            else None
        )
        if active_question and text_body.strip().startswith(
            f"回答 {active_question['question_ref']} "
        ):
            answer = text_body.strip().split(" ", 2)[2]
            await self.resolve_question(
                group["session_id"], active_question["question_ref"], answer, origin
            )
            return
        if text_body.strip() in ("好", "是", "好的", "否", "不"):
            if active_question and active_question["yes_no"]:
                await self.resolve_question(
                    group["session_id"], active_question["question_ref"], text_body.strip(), origin
                )
                return
            async with self.db.transaction() as conn:
                await self.db.receipt(
                    conn,
                    origin=origin,
                    body="请引用具体问题作答；当前没有唯一明确的是/否问题。",
                    dedupe=f"answer:{group_id}",
                )
            return
        projects = await self.gateway.projects() if settings.account_ref else []
        chunked_materials, reading_range = material_blocks(segments, settings.materials)
        request = {
            "material_group_id": group_id,
            "identity": settings.identity.model_dump(),
            "timezone": settings.timezone,
            "materials": chunked_materials,
            "reading_range": reading_range,
            "assets": assets,
            "allowed_projects": [
                x["name"] for x in projects if x["id"] in settings.allowed_projects
            ],
            "outer_messages": [
                {
                    "source_kind": e.source_kind,
                    "received_at": e.received_at.isoformat(),
                    "segments": [
                        {
                            "source_id": s.source_id,
                            "kind": s.kind,
                            "text": s.text,
                            "published_at": s.published_at.isoformat() if s.published_at else None,
                        }
                        for s in e.segments
                    ],
                }
                for e in envelopes
            ],
            "question": (
                active_question
                if active_question and active_question["group_id"] == group_id
                else None
            ),
            "clarification": payload.get("clarification"),
            "task_selection": json.loads(session["query"] or "{}").get("selection", []),
            "recent_ref": json.loads(session["recent"] or "null"),
        }
        # Bound metadata independently from the text-block budget; never silently truncate.
        if len(canonical(request)) > 2 * 1024**2:
            raise NotiDoError("MODEL_INPUT_LIMIT", "材料元数据超过输入预算，请缩小当前组；未执行。")
        async with self.model_limit:
            model_plan = await asyncio.wait_for(
                self.bridge.call_provider(
                    request,
                    provider_id=settings.provider_id or self.bridge.provider_id,
                    deadline_seconds=settings.time_budgets.provider_seconds,
                ),
                await self.remaining_budget(group_id),
            )
        if model_plan.intent in ("none", "unsupported"):
            async with self.db.transaction() as conn:
                unread = [
                    x
                    for x in segments
                    if x["state"] == "unknown"
                    and x["reason"] != "BODY_NOT_SUPPORTED_ATTACHMENT_ONLY"
                ]
                if model_plan.intent == "none" and unread:
                    await self.ask(
                        conn,
                        group_id,
                        ["必要材料尚未读全，请补发可读内容或原件；原件已保留，尚未写入任务。"],
                        {
                            "blocked": [
                                {
                                    "code": "MATERIAL_UNREAD",
                                    "locations": [
                                        {
                                            "source_id": x["source_id"],
                                            "location": x["location"],
                                            "reason": x["reason"],
                                        }
                                        for x in unread
                                    ],
                                }
                            ]
                        },
                    )
                    return
                await self.db.receipt(
                    conn,
                    origin=origin,
                    body=model_plan.safe_summary or model_plan.reason,
                    dedupe=f"summary:{group_id}",
                )
                await self.finish_group(conn, group_id)
            return
        outer_text = "\n".join(
            s.text or ""
            for e in envelopes
            for s in e.segments
            if s.published_at is None and s.kind == "text"
        )
        if outer_text.strip().startswith(("只总结", "不写入", "仅总结")):
            summary = (
                getattr(model_plan, "notice_summary", None)
                or "\n".join(
                    x.title for x in getattr(model_plan, "tasks", []) if isinstance(x, CreateItem)
                )
                or "已收到；本次按外层要求不写入任务。"
            )
            async with self.db.transaction() as conn:
                await self.db.receipt(
                    conn, origin=origin, body=summary, dedupe=f"summary-only:{group_id}"
                )
                await self.finish_group(conn, group_id)
            return
        if model_plan.intent == "query":
            await self.query(group["session_id"], origin, model_plan.query)
            async with self.db.transaction() as conn:
                await self.finish_group(conn, group_id)
            return
        if model_plan.intent == "clarify":
            await self.resolve_question(
                group["session_id"], model_plan.question_ref, model_plan.answer, origin
            )
            return
        dispositions_only = bool(getattr(model_plan, "tasks", [])) and all(
            x.relevance == "not_applies" or x.obligation == "informational"
            for x in model_plan.tasks
        )
        if model_plan.ambiguities and not dispositions_only:
            async with self.db.transaction() as conn:
                await self.ask(
                    conn,
                    group_id,
                    model_plan.ambiguities,
                    {
                        "model_plan": model_plan.model_dump(),
                        "clarification": payload.get("clarification", {}),
                    },
                )
            return
        if not settings.account_ref:
            raise NotiDoError(
                "ACCOUNT_REQUIRED", "请先在设置中确认当前滴答账号、默认和允许清单。", status=503
            )
        if model_plan.intent in ("update", "complete"):
            target = await self.locate(model_plan.target, group["session_id"], settings)
            if model_plan.intent == "update":
                for value in (model_plan.patch.date_text, model_plan.patch.time_text):
                    if value and value.replace(" ", "") not in text_body.replace(" ", ""):
                        raise NotiDoError(
                            "DATE_EVIDENCE_INVALID", "修改时间原文未包含在本次请求中。"
                        )
            patch = (
                await self.patch_fields(
                    model_plan.patch,
                    target,
                    settings,
                    envelopes[0].received_at,
                    allow_overdue=payload.get("clarification", {}).get("allow_overdue", False),
                )
                if model_plan.intent == "update"
                else {}
            )
            plan = self.base_plan(group, origin, settings, config_revision)
            plan.update(
                {
                    "kind": model_plan.intent,
                    "project_id": target["projectId"],
                    "task_id": target["id"],
                    "fields": patch,
                    "before": target,
                    "allow_overdue": payload.get("clarification", {}).get("allow_overdue", False),
                }
            )
            async with self.db.transaction() as conn:
                await self.persist_operation(conn, plan)
                await execute(
                    conn,
                    "UPDATE message_records SET state='processed' WHERE group_id=:g",
                    {"g": group_id},
                )
            self.wakeup.set()
            return
        if model_plan.intent == "ingest_notice" and model_plan.material_group_id != group_id:
            raise NotiDoError("GROUP_MISMATCH", "模型材料组引用不一致。")
        is_notice = model_plan.intent == "ingest_notice"
        notice_id = None
        if is_notice:
            existing = await self.db.read(
                "SELECT * FROM notice_records WHERE group_id=:g ORDER BY created_at LIMIT 1",
                {"g": group_id},
            )
            notice_id = existing[0]["id"] if existing else uid()
            async with self.db.transaction() as conn:
                await execute(
                    conn,
                    "INSERT OR IGNORE INTO notice_records VALUES (:id,:g,'personal','processing',0,:t)",
                    {"id": notice_id, "g": group_id, "t": time.time()},
                )
                revision = (
                    await one(
                        conn, "SELECT revision FROM notice_records WHERE id=:id", {"id": notice_id}
                    )
                )["revision"] + 1
                await execute(
                    conn,
                    "INSERT INTO notice_versions VALUES (:id,:n,:r,:p,:f,:t)",
                    {
                        "id": uid(),
                        "n": notice_id,
                        "r": revision,
                        "p": model_plan.model_dump_json(),
                        "f": key(text_body, [(x["hash"], x["state"]) for x in assets]),
                        "t": time.time(),
                    },
                )
                await execute(
                    conn,
                    "UPDATE notice_records SET revision=:r WHERE id=:id",
                    {"r": revision, "id": notice_id},
                )
        blocked, saved, ignored = [], 0, []
        clarification = payload.get("clarification", {})
        for index, item in enumerate(model_plan.tasks):
            if item.relevance == "not_applies" or item.obligation == "informational":
                ignored.append("事项不适用或仅知晓，未创建任务。")
                continue
            try:
                if item.ambiguities or item.relevance == "unknown":
                    raise NotiDoError(
                        "IDENTITY_UNKNOWN",
                        "；".join(item.ambiguities) or "请补充此事项所需身份条件。",
                    )
                if item.obligation == "optional" and getattr(
                    item, "title", None
                ) not in clarification.get(
                    "participation_titles", [clarification.get("participation_title")]
                ):
                    raise NotiDoError("OPTIONAL_CHOICE", "是否参加此自愿事项？请明确事项名称。")
                matched = verify_evidence(item.source_evidence, segments)
                source_ids = {x["source_id"] for x in matched}
                if any(
                    x["state"] == "unknown"
                    and x["source_id"] in source_ids
                    and x["reason"] != "BODY_NOT_SUPPORTED_ATTACHMENT_ONLY"
                    for x in segments
                ):
                    raise NotiDoError(
                        "CRITICAL_UNREAD", "此事项的来源有未读或模糊位置，请补可读材料。"
                    )
                if "详见附件" in "".join(x.quote for x in item.source_evidence) and (
                    not assets or any(x["state"] != "ready" for x in assets)
                ):
                    raise NotiDoError(
                        "REQUIRED_ASSET_MISSING",
                        "此事项要求的附件未取得，请补发；独立明确事项会继续处理。",
                    )
                if not isinstance(item, CreateItem):
                    target = await self.locate(item.target, group["session_id"], settings)
                    # Notice postponement may only change last-written fields of an associated task.
                    links = await self.db.read(
                        "SELECT * FROM notice_task_links WHERE account_ref=:a AND project_id=:p AND task_id=:t",
                        {"a": settings.account_ref, "p": target["projectId"], "t": target["id"]},
                    )
                    if len(links) != 1 or target.get("status", 0) != 0:
                        raise NotiDoError(
                            "NOTICE_LINK_AMBIGUOUS",
                            "延期未能唯一关联未完成的原通知任务，请指定目标。",
                        )
                    previous = json.loads(links[0]["snapshot"])
                    for field in ("dueDate", "isAllDay"):
                        if target.get(field) != previous.get(field):
                            raise NotiDoError(
                                "EXTERNAL_CHANGE", "原任务日期已被外部编辑，请先确认差异。"
                            )
                    anchor = self.item_anchor(item, envelopes, is_notice, clarification)
                    previous_source = previous.get("_notido", {}).get("source_published_at")
                    if not clarification.get("source_order_confirmed"):
                        if anchor is None or not previous_source:
                            raise NotiDoError(
                                "SOURCE_ORDER_UNKNOWN",
                                "来源先后尚不明确，请确认这份通知是否为最新通知。",
                            )
                        if anchor <= datetime.fromisoformat(previous_source):
                            raise NotiDoError(
                                "SOURCE_ORDER_CONFLICT",
                                "本通知原发布时间未晚于已处理版本，请核对延期来源。",
                            )
                    for value in (item.patch.date_text, item.patch.time_text):
                        if value and not any(
                            value.replace(" ", "") in x.quote.replace(" ", "")
                            for x in item.source_evidence
                        ):
                            raise NotiDoError(
                                "DATE_EVIDENCE_INVALID", "延期时间原文未包含在该事项依据中。"
                            )
                    patch = await self.patch_fields(
                        item.patch,
                        target,
                        settings,
                        anchor,
                        allow_overdue=clarification.get("allow_overdue", False),
                    )
                    previous_plans = await self.db.read(
                        "SELECT plan FROM operations WHERE action_id=:a AND kind IN ('create','update') AND state='succeeded' ORDER BY created_at DESC LIMIT 1",
                        {"a": links[0]["action_id"]},
                    )
                    if not previous_plans:
                        raise NotiDoError("NOTICE_SNAPSHOT_MISSING", "原行动缺少可核验的写入快照。")
                    previous_plan = json.loads(previous_plans[0]["plan"])
                    original_notice = (
                        previous_plan.get("managed_notice_id") or links[0]["notice_id"]
                    )
                    old_region = managed_region(previous.get("content") or "", original_notice)
                    # User text outside the managed region is retained; edited robot evidence conflicts.
                    replace_managed(
                        target.get("content") or "", old_region, old_region, original_notice
                    )
                    requirements = (
                        [item.patch.notes]
                        if "notes" in item.patch.model_fields_set and item.patch.notes
                        else previous_plan.get("requirements", [])
                    )
                    normalized_date = previous_plan.get("normalized_date")
                    if any(
                        x in item.patch.model_fields_set
                        for x in ("date_text", "time_text", "all_day")
                    ):
                        combined = {**target, **patch}
                        local = task_date(combined, settings.timezone)
                        normalized_date = normalize(
                            item.patch.date_text or (local.date().isoformat() if local else None),
                            local.strftime("%H:%M")
                            if local and not combined.get("isAllDay")
                            else None,
                            anchor=anchor,
                            timezone=settings.timezone,
                            kind="none"
                            if not local
                            else "date_only"
                            if combined.get("isAllDay")
                            else "timed",
                            evidence_id=matched[0]["id"],
                            time_kind=previous_plan.get("normalized_date", {}).get(
                                "time_kind", "deadline"
                            ),
                        )
                    if normalized_date is None:
                        raise NotiDoError("NOTICE_DATE_MISSING", "原通知日期快照缺失，请核对目标。")
                    replacement = managed_notes(
                        original_notice, requirements, item.source_evidence, normalized_date
                    )
                    patch["content"] = replace_managed(
                        target.get("content") or "", old_region, replacement, original_notice
                    )
                    if len(patch["content"]) > 10000:
                        raise NotiDoError(
                            "NOTES_TOO_LONG", "保留用户备注后超过远端备注上限，请精简后再处理。"
                        )
                    plan = self.base_plan(group, origin, settings, config_revision)
                    plan.update(
                        {
                            "kind": "update",
                            "project_id": target["projectId"],
                            "task_id": target["id"],
                            "fields": patch,
                            "before": target,
                            "notice_id": notice_id,
                            "action_id": links[0]["action_id"],
                            "normalized_date": normalized_date,
                            "source_published_at": anchor.isoformat() if anchor else None,
                            "managed_notice_id": original_notice,
                            "requirements": requirements,
                            "allow_overdue": clarification.get("allow_overdue", False),
                            "source_evidence": [x.model_dump() for x in item.source_evidence],
                        }
                    )
                else:
                    for value in (item.date_text, item.time_text):
                        if value and not any(
                            value.replace(" ", "") in x.quote.replace(" ", "")
                            for x in item.source_evidence
                        ):
                            raise NotiDoError(
                                "DATE_EVIDENCE_INVALID", "日期原文未包含在该事项已读依据中。"
                            )
                    anchor = self.item_anchor(item, envelopes, is_notice, clarification)
                    date_text = item.date_text
                    if (
                        date_text
                        and not date_text.endswith("前")
                        and any(
                            (date_text + "前").replace(" ", "") in x.quote.replace(" ", "")
                            for x in item.source_evidence
                        )
                    ):
                        date_text += "前"
                    date_value = normalize(
                        date_text,
                        item.time_text,
                        anchor=anchor,
                        timezone=settings.timezone,
                        kind=item.date_kind,
                        evidence_id=matched[0]["id"],
                        time_kind=item.time_kind,
                        comparison_override=clarification.get("date_comparison"),
                    )
                    if overdue(date_value, datetime.now(UTC)) and not clarification.get(
                        "allow_overdue"
                    ):
                        raise NotiDoError(
                            "OVERDUE_CONFIRMATION", "此期限已过去，是否补记逾期事项？"
                        )
                    project = resolve_project(item.project_name, projects, settings)
                    for asset_id in item.attachment_asset_ids:
                        if not any(x["id"] == asset_id and x["state"] == "ready" for x in assets):
                            raise NotiDoError(
                                "ASSET_NOT_ALLOWED", "附件未取得或不属于此材料组，请确认关联。"
                            )
                    date_identity = {
                        k: date_value[k]
                        for k in (
                            "kind",
                            "local_date",
                            "local_time",
                            "timezone",
                            "comparison",
                            "time_kind",
                        )
                    }
                    signature = (
                        key(
                            "notice-action",
                            text_body,
                            item.title,
                            item.requirements,
                            date_identity,
                            [x["hash"] for x in assets],
                        )
                        if is_notice
                        else key("direct-action", group_id, index)
                    )
                    previous_actions = await self.db.read(
                        "SELECT * FROM action_items WHERE identity_key=:k", {"k": signature}
                    )
                    if previous_actions and is_notice:
                        links = await self.db.read(
                            "SELECT * FROM notice_task_links WHERE action_id=:id AND account_ref=:a",
                            {"id": previous_actions[0]["id"], "a": settings.account_ref},
                        )
                        if links:
                            await self.gateway.get(links[0]["project_id"], links[0]["task_id"])
                            ignored.append(f"重复通知，复用已存任务 {links[0]['task_id']}。")
                            continue
                    action_id = previous_actions[0]["id"] if previous_actions else uid()
                    if previous_actions and not is_notice:
                        versions = await self.db.read(
                            "SELECT payload FROM action_item_versions WHERE action_id=:a AND revision=0",
                            {"a": action_id},
                        )
                        if versions and json.loads(versions[0]["payload"]) != item.model_dump():
                            raise NotiDoError(
                                "ACTION_IDENTITY_CHANGED",
                                "已固化行动的解析结果发生变化，请明确修改已有任务；不会重排或重建。",
                            )
                    content = (
                        managed_notes(
                            notice_id, item.requirements, item.source_evidence, date_value
                        )
                        if is_notice
                        else "\n".join(item.requirements)
                    )
                    plan = self.base_plan(group, origin, settings, config_revision)
                    plan.update(
                        {
                            "kind": "create",
                            "project_id": project["id"],
                            "project_name": project["name"],
                            "fields": {
                                "title": item.title,
                                "content": content,
                                **native_fields(date_value),
                            },
                            "normalized_date": date_value,
                            "attachment_asset_ids": item.attachment_asset_ids,
                            "notice_id": notice_id,
                            "action_id": action_id,
                            "allow_overdue": clarification.get("allow_overdue", False),
                            "requirements": item.requirements,
                            "source_published_at": anchor.isoformat()
                            if is_notice and anchor
                            else None,
                            "source_evidence": [x.model_dump() for x in item.source_evidence],
                        }
                    )
                    async with self.db.transaction() as conn:
                        await execute(
                            conn,
                            "INSERT OR IGNORE INTO action_items VALUES (:id,'personal',:n,:k,0)",
                            {"id": action_id, "n": notice_id, "k": signature},
                        )
                        await execute(
                            conn,
                            "INSERT OR IGNORE INTO action_item_versions VALUES (:id,:a,0,:p,:t)",
                            {
                                "id": uid(),
                                "a": action_id,
                                "p": item.model_dump_json(),
                                "t": time.time(),
                            },
                        )
                        for source in item.source_evidence:
                            segment = next(
                                x
                                for x in matched
                                if x["source_id"] == source.source_id
                                and x["location"] == source.location
                            )
                            await execute(
                                conn,
                                "INSERT INTO evidence_records VALUES (:id,:s,:q,1)",
                                {"id": uid(), "s": segment["id"], "q": source.quote},
                            )
                async with self.db.transaction() as conn:
                    await self.persist_operation(conn, plan)
                saved += 1
            except NotiDoError as exc:
                blocked.append({"item_index": index, "code": exc.code, "question": exc.message})
        async with self.db.transaction() as conn:
            unchosen = [
                x
                for x in getattr(model_plan, "optional_items", [])
                if x
                not in clarification.get(
                    "participation_titles", [clarification.get("participation_title")]
                )
            ]
            if blocked or unchosen:
                questions = [x["question"] for x in blocked] or [
                    "请确认是否参加自愿事项：" + "、".join(unchosen)
                ]
                await self.ask(
                    conn,
                    group_id,
                    questions,
                    {
                        "model_plan": model_plan.model_dump(),
                        "blocked": blocked,
                        "clarification": clarification,
                    },
                )
            elif not saved:
                body = getattr(model_plan, "notice_summary", "") or "未发现需要记录的行动。"
                await self.db.receipt(
                    conn,
                    origin=origin,
                    body="\n".join([body, *ignored]),
                    dedupe=f"no-action:{group_id}",
                )
                await self.finish_group(conn, group_id)
            await execute(
                conn,
                "UPDATE message_records SET state='processed' WHERE group_id=:g",
                {"g": group_id},
            )
        self.wakeup.set()

    async def supplement_group(self, group_id):
        targets = await self.db.read(
            "SELECT * FROM material_task_targets WHERE group_id=:g", {"g": group_id}
        )
        if not targets:
            return False
        frozen = targets[0]
        async with self.write_lock:
            settings, revision = await self.db.settings()
            group = (
                await self.db.read("SELECT * FROM material_groups WHERE id=:g", {"g": group_id})
            )[0]
            if group["state"] != "awaiting_materials":
                return True
            messages = await self.db.read(
                "SELECT envelope FROM message_records WHERE group_id=:g ORDER BY received_at,id",
                {"g": group_id},
            )
            envelope = InputEnvelope.model_validate_json(messages[0]["envelope"])
            assets = await self.db.read("SELECT * FROM assets WHERE group_id=:g", {"g": group_id})
            if any(x["state"] == "pending" for x in assets):
                return True
            try:
                if (
                    frozen["account_ref"] != settings.account_ref
                    or frozen["credential_generation"] != settings.credential_generation
                    or frozen["config_revision"] != revision
                    or not await self.authorized(envelope)
                    or self.maintenance
                ):
                    raise NotiDoError(
                        "SUPPLEMENT_CONTEXT_CHANGED",
                        "补件账号、配置或授权已变更，请在后台核对目标后重新补发。",
                    )
                candidates = await self.db.read(
                    "SELECT * FROM operations WHERE account_ref=:a AND remote_id=:t AND state='succeeded' AND kind IN ('create','update') ORDER BY created_at DESC,id DESC LIMIT 201",
                    {"a": settings.account_ref, "t": frozen["task_id"]},
                )
                if len(candidates) > 200:
                    raise NotiDoError(
                        "SUPPLEMENT_HISTORY_LIMIT",
                        "此任务历史超过消息补件核对预算，请从页面明确选择目标。",
                    )
                eligible = [
                    x
                    for x in candidates
                    if json.loads(x["plan"])["project_id"] in settings.allowed_projects
                ]
                projects = {json.loads(x["plan"])["project_id"] for x in eligible}
                if len(projects) != 1:
                    raise NotiDoError(
                        "SUPPLEMENT_TARGET_NOT_UNIQUE",
                        "此 ID 没有唯一已保存且已授权的任务，请在页面明确选择补件目标。",
                    )
                if not assets:
                    raise NotiDoError(
                        "SUPPLEMENT_ORIGINAL_REQUIRED",
                        "请将原件与“补充任务 <真实任务 ID>”一同发送，或从页面选择目标补件。",
                    )
                original = json.loads(eligible[0]["plan"])
                task = await self.gateway.get(original["project_id"], frozen["task_id"])
                expected_title = original["fields"].get("title") or original.get("before", {}).get(
                    "title"
                )
                if task.get("status", 0) != 0 or task.get("title") != expected_title:
                    raise NotiDoError(
                        "SUPPLEMENT_TARGET_CHANGED",
                        "目标任务已完成或标题已变化，请在后台核对后补件。",
                    )
                parent = self.base_plan(group, envelope.reply_origin_ref, settings, revision)
                parent.update(
                    {
                        "project_id": original["project_id"],
                        "project_name": original.get("project_name"),
                        "actor_key": envelope.actor_key,
                        "framework_instance_id": envelope.framework_instance_id,
                        "fields": {},
                        "before": task,
                        "supplement_target_id": frozen["task_id"],
                        "processing_deadline_at": time.time()
                        + await self.remaining_budget(group_id),
                    }
                )
                async with self.db.transaction() as conn:
                    current = await one(conn, "SELECT revision FROM settings WHERE id='main'")
                    if current["revision"] != revision:
                        raise NotiDoError(
                            "SUPPLEMENT_CONTEXT_CHANGED", "补件配置已变更，请重新核对目标。"
                        )
                    for asset in assets:
                        if asset["state"] == "ready":
                            await self.schedule_upload(conn, parent, frozen["task_id"], asset["id"])
                    failures = [x for x in assets if x["state"] != "ready"]
                    if failures:
                        await self.ask(
                            conn,
                            group_id,
                            ["部分原件未取得，已取得部分单独上传；请按目标引用补发失败原件。"],
                            {
                                "code": "SUPPLEMENT_ORIGINAL_UNAVAILABLE",
                                "task_id": frozen["task_id"],
                            },
                        )
                    else:
                        await self.db.receipt(
                            conn,
                            origin=envelope.reply_origin_ref,
                            body=f"补件目标已核对：{task['title']}\n真实任务 ID：{task['id']}\n原件按目标和 hash 独立记账；既有成功或待核查上传不会重传。",
                            dedupe=f"supplement:{group_id}",
                        )
                        pending = await one(
                            conn,
                            "SELECT count(*) AS n FROM operations WHERE json_extract(plan,'$.group_id')=:g AND state NOT IN ('succeeded','cancelled')",
                            {"g": group_id},
                        )
                        if not pending["n"]:
                            await self.finish_group(conn, group_id)
                    await execute(
                        conn,
                        "UPDATE message_records SET state='processed' WHERE group_id=:g",
                        {"g": group_id},
                    )
            except NotiDoError as error:
                async with self.db.transaction() as conn:
                    await self.ask(
                        conn,
                        group_id,
                        [error.message],
                        {"code": error.code, "task_id": frozen["task_id"]},
                    )
        self.wakeup.set()
        return True

    async def resolve_question(self, session_id, question_ref, answer, origin):
        session = (await self.db.read("SELECT * FROM sessions WHERE id=:s", {"s": session_id}))[0]
        question = json.loads(session["question"] or "null")
        if (
            not question
            or question["question_ref"] != question_ref
            or (session["question_expires"] or 0) < time.time()
        ):
            raise NotiDoError(
                "QUESTION_EXPIRED", "问题引用已失效，请从待处理页面继续并获取新问题。"
            )
        if await self.db.read(
            "SELECT group_id FROM restored_group_holds WHERE group_id=:g AND released_at IS NULL",
            {"g": question["group_id"]},
        ):
            raise NotiDoError(
                "RESTORE_REPROCESS_CONFIRMATION", "恢复前的草稿须先在后台复核并明确重新处理。"
            )
        # Every answer is retained as evidence. Only explicit machine-verifiable consent affects policy.
        clarification = self.answer_context(question, answer)
        async with self.db.transaction() as conn:
            await execute(
                conn,
                "DELETE FROM processing_budgets WHERE group_id=:g",
                {"g": question["group_id"]},
            )
            await execute(
                conn,
                "UPDATE sessions SET question=NULL,question_expires=NULL,revision=revision+1 WHERE id=:s",
                {"s": session_id},
            )
            await execute(
                conn,
                "UPDATE material_groups SET state='awaiting_materials',revision=revision+1 WHERE id=:g",
                {"g": question["group_id"]},
            )
            await self.db.job(
                conn,
                "parse_group",
                f"clarify:{question_ref}",
                {"group_id": question["group_id"], "clarification": clarification},
            )
            await self.db.receipt(
                conn,
                origin=origin,
                body="已记录该问题的回答，重新核验受影响事项。",
                dedupe=f"answer:{question_ref}",
            )
        self.processing_deadlines.pop(question["group_id"], None)
        self.wakeup.set()

    @staticmethod
    def answer_context(question, answer):
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 12000:
            raise NotiDoError("ANSWER_INVALID", "回答须为非空文本且不超过 12,000 字。", status=400)
        clarification = {
            **question.get("context", {}).get("clarification", {}),
            "question_ref": question["question_ref"],
            "answer": answer,
        }
        codes = {x["code"] for x in question["context"].get("blocked", [])}
        if "OVERDUE_CONFIRMATION" in codes and answer.strip() in ("补记逾期", "确认补记逾期"):
            clarification["allow_overdue"] = True
        if answer.startswith("参加 "):
            clarification["participation_title"] = answer.split(" ", 1)[1]
            clarification["participation_titles"] = list(
                dict.fromkeys(
                    [
                        *clarification.get("participation_titles", []),
                        clarification["participation_title"],
                    ]
                )
            )
        if answer.strip() == "确认这是最新通知":
            clarification["source_order_confirmed"] = True
        if answer.strip() in ("当天可提交", "包含当天"):
            clarification["date_comparison"] = "before_or_at"
        if answer.strip() in ("当天不可提交", "不含当天"):
            clarification["date_comparison"] = "before"
        if answer.startswith("原发布时间 "):
            try:
                anchor = datetime.fromisoformat(answer.split(" ", 1)[1])
                if anchor.tzinfo is None:
                    raise ValueError
                clarification["source_published_at"] = anchor.isoformat()
            except ValueError as exc:
                raise NotiDoError(
                    "INVALID_ANCHOR", "原发布时间须为带时区的 RFC3339。", status=400
                ) from exc
        return clarification

    async def query(self, session_id, origin, query):
        settings, _ = await self.db.settings()
        if query.project_name:
            project = resolve_project(query.project_name, await self.gateway.projects(), settings)
            settings = settings.model_copy(update={"allowed_projects": [project["id"]]})
        snapshot = await read_scope(self.gateway, settings, datetime.now(UTC), query)
        async with self.db.transaction() as conn:
            session = await one(conn, "SELECT * FROM sessions WHERE id=:s", {"s": session_id})
            revision = session["query_revision"] + 1
            selection = page(
                snapshot,
                account_ref=settings.account_ref,
                session_key=session_id,
                revision=revision,
                now=datetime.now(UTC),
            )
            record = {
                "query": query.model_dump(),
                "fingerprint": snapshot["fingerprint"],
                "offset": 0,
                "selection": selection,
                "account_ref": settings.account_ref,
            }
            await execute(
                conn,
                "UPDATE sessions SET query=:q,query_revision=:r,revision=revision+1 WHERE id=:s",
                {"q": canonical(record), "r": revision, "s": session_id},
            )
            await self.db.receipt(
                conn,
                origin=origin,
                body=self.query_receipt(snapshot, selection),
                dedupe=f"query:{session_id}:{revision}",
            )
        self.wakeup.set()
        return snapshot

    async def next_page(self, session_id, origin):
        session = (await self.db.read("SELECT * FROM sessions WHERE id=:s", {"s": session_id}))[0]
        record = json.loads(session["query"] or "null")
        settings, _ = await self.db.settings()
        if not record or record["account_ref"] != settings.account_ref:
            raise NotiDoError("QUERY_EXPIRED", "查询已失效，请重新查询。")
        query = Query.model_validate(record["query"])
        if query.project_name:
            project = resolve_project(query.project_name, await self.gateway.projects(), settings)
            settings = settings.model_copy(update={"allowed_projects": [project["id"]]})
        snapshot = await read_scope(self.gateway, settings, datetime.now(UTC), query)
        if snapshot["fingerprint"] != record["fingerprint"] or not snapshot["complete"]:
            await self.query(session_id, origin, query)
            raise NotiDoError("QUERY_CHANGED", "任务排序或范围发生变化，已刷新第一页。")
        async with self.db.transaction() as conn:
            revision = session["query_revision"] + 1
            offset = record["offset"] + 10
            selection = page(
                snapshot,
                account_ref=settings.account_ref,
                session_key=session_id,
                revision=revision,
                now=datetime.now(UTC),
                offset=offset,
            )
            record.update({"offset": offset, "selection": selection})
            await execute(
                conn,
                "UPDATE sessions SET query=:q,query_revision=:r,revision=revision+1 WHERE id=:s",
                {"q": canonical(record), "r": revision, "s": session_id},
            )
            await self.db.receipt(
                conn,
                origin=origin,
                body=self.query_receipt(snapshot, selection),
                dedupe=f"query:{session_id}:{revision}",
            )
