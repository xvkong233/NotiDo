"""Notification provenance and revision guards; interpretation belongs to AstrBot."""

import json
import re
from datetime import datetime

from pydantic import Field

from .dates import normalize
from .errors import NotiDoError
from .keys import key
from .models import Evidence, InputEnvelope, StrictModel
from .policy import managed_notes, managed_region, normalize_text, replace_managed
from .query import task_date


class NoticeSource(StrictModel):
    group_id: str = Field(min_length=1, max_length=200)
    action_key: str = Field(min_length=1, max_length=200)


class NoticeRevision(StrictModel):
    group_id: str = Field(min_length=1, max_length=200)
    evidence: list[Evidence] = Field(default_factory=list)
    visual_evidence: list[Evidence] = Field(default_factory=list)
    requirements: list[str] | None = Field(default=None, max_length=100)
    source_order_confirmed: bool = False


class NativeNotices:
    def __init__(self, service):
        self.service = service

    async def source(self, group_id, session, settings):
        groups = await self.service.db.read(
            "SELECT id FROM material_groups WHERE id=:g AND session_id=:s AND user_id='personal'",
            {"g": group_id, "s": session},
        )
        if not groups:
            raise NotiDoError("MATERIAL_NOT_FOUND", "通知来源不属于当前授权会话或已过期。")
        segments = await self.service.db.read(
            "SELECT source_id,location,text,state FROM material_segments WHERE group_id=:g ORDER BY location,id",
            {"g": group_id},
        )
        assets = await self.service.db.read(
            "SELECT hash,state FROM assets WHERE group_id=:g ORDER BY hash,state", {"g": group_id}
        )
        records = await self.service.db.read(
            "SELECT envelope FROM message_records WHERE group_id=:g ORDER BY received_at,id",
            {"g": group_id},
        )
        original = {
            s.published_at
            for row in records
            for s in InputEnvelope.model_validate_json(row["envelope"]).segments
            if s.published_at is not None
        }
        timestamp = next(iter(original)) if len(original) == 1 else None
        envelopes = [InputEnvelope.model_validate_json(row["envelope"]) for row in records]
        anchor = timestamp
        if anchor is None and len(envelopes) == 1 and envelopes[0].source_kind != "user_forward":
            anchor = envelopes[0].received_at
        fingerprint = key(
            [(s["location"], normalize_text(s["text"])) for s in segments],
            [(a["hash"], a["state"]) for a in assets],
            timestamp.isoformat() if timestamp else None,
        )
        existing = await self.service.db.read(
            "SELECT n.id,v.fingerprint FROM notice_records n JOIN notice_versions v ON v.notice_id=n.id AND v.revision=0 WHERE n.group_id=:g AND length(n.id)=64 ORDER BY n.created_at LIMIT 1",
            {"g": group_id},
        )
        notice_id = key("native-notice", settings.account_ref, fingerprint)
        if existing:
            # Late originals extend an existing notice; they do not rename its identity.
            original_id = key("native-notice", settings.account_ref, existing[0]["fingerprint"])
            if existing[0]["id"] == original_id:
                notice_id, fingerprint = original_id, existing[0]["fingerprint"]
        return {
            "notice_id": notice_id,
            "fingerprint": fingerprint,
            "source_published_at": timestamp.isoformat() if timestamp else None,
            "source_group_id": group_id,
            "anchor": anchor,
            "source_ids": {s["source_id"] for s in segments}
            | {
                s.source_id
                for row in records
                for s in InputEnvelope.model_validate_json(row["envelope"]).segments
            },
        }

    @staticmethod
    def require_evidence(source, evidence, visual_evidence):
        if not evidence and not visual_evidence:
            raise NotiDoError("EVIDENCE_REQUIRED", "通知行动必须引用已交付的文字或真实图像依据。")
        if any(e.source_id not in source["source_ids"] for e in [*evidence, *visual_evidence]):
            raise NotiDoError("EVIDENCE_INVALID", "通知依据必须来自所选材料组。")

    @staticmethod
    def preserve_filename(requirements, evidence, visual_evidence):
        # Validate an explicitly labelled literal in caller parameters against
        # cited evidence. This does not classify notices or invent requirements.
        pattern = re.compile(r"(?:文件名|(?:文件)?命名)(?:为|要求)?[：:\s]*([^；;，,。\n\r]+)")

        def templates(values):
            return {
                normalize_text(match[1]).strip("“”「」\"'")
                for value in values
                for match in pattern.finditer(value)
            }

        declared = templates(requirements)
        original = templates([e.quote for e in [*evidence, *visual_evidence]])
        if declared - original:
            raise NotiDoError(
                "FILENAME_REQUIREMENT_CHANGED",
                "文件名或命名模板必须原样取自所引用材料，不能补分隔符或改写；修正参数和必要依据后沿用原request_key。",
                details={
                    "input_rejected": True,
                    "side_effect": "none",
                    "safe_to_correct_arguments": True,
                },
            )

    async def known_actions(self, source, settings):
        return await self.service.db.read(
            "SELECT json_extract(o.plan,'$.action_key') AS action_key,l.project_id,l.task_id,l.snapshot FROM action_items a JOIN notice_task_links l ON l.action_id=a.id JOIN operations o ON o.action_id=a.id AND o.kind='create' WHERE a.notice_id=:n AND l.account_ref=:a",
            {"n": source["notice_id"], "a": settings.account_ref},
        )

    async def revision(self, plan, data, target, source, settings):
        links = await self.service.db.read(
            "SELECT * FROM notice_task_links WHERE account_ref=:a AND project_id=:p AND task_id=:t AND notice_id IS NOT NULL",
            {"a": settings.account_ref, "p": target["projectId"], "t": target["id"]},
        )
        if len(links) != 1:
            raise NotiDoError(
                "NOTICE_TARGET_AMBIGUOUS", "目标缺少唯一通知关联，请核对来源后进行明确修改。"
            )
        link = links[0]
        previous = json.loads(link["snapshot"])
        if target.get("status") != 0:
            raise NotiDoError("TASK_COMPLETED", "已完成通知任务不自动延期或重新打开。")
        watched = {f for f in ("dueDate", "isAllDay", "title") if f in previous}
        if not self.service.fields_match(target, {f: previous.get(f) for f in watched}):
            raise NotiDoError(
                "TARGET_CHANGED", "通知任务的标题或日期已由用户修改，请核对后明确选择修改。"
            )
        prior_plans = await self.service.db.read(
            "SELECT plan FROM operations WHERE action_id=:a AND kind IN ('create','update') AND state='succeeded' ORDER BY created_at DESC LIMIT 1",
            {"a": link["action_id"]},
        )
        if not prior_plans:
            raise NotiDoError("NOTICE_SNAPSHOT_MISSING", "通知行动缺少可核验的写入快照。")
        prior = json.loads(prior_plans[0]["plan"])
        old_time = prior.get("source_published_at")
        new_time = source["source_published_at"]
        if not data.notice.source_order_confirmed:
            if not old_time or not new_time:
                raise NotiDoError(
                    "SOURCE_ORDER_UNKNOWN",
                    "原发布时间不能确定先后，请在 AstrBot 会话确认这是最新通知。",
                )
            if datetime.fromisoformat(new_time) <= datetime.fromisoformat(old_time):
                raise NotiDoError("SOURCE_ORDER_CONFLICT", "来源不晚于已处理通知，请核对延期版本。")
        marker = prior.get("managed_notice_id") or link["notice_id"]
        old_region = managed_region(previous.get("content") or "", marker)
        replacement_date = prior.get("normalized_date")
        if any(f in data.patch.model_fields_set for f in ("date_text", "time_text", "all_day")):
            combined = {**target, **plan["fields"]}
            local = task_date(combined, settings.timezone)
            replacement_date = normalize(
                local.date().isoformat() if local else None,
                local.strftime("%H:%M") if local and not combined.get("isAllDay") else None,
                anchor=datetime.fromisoformat(new_time) if new_time else None,
                timezone=settings.timezone,
                kind="none" if not local else "date_only" if combined.get("isAllDay") else "timed",
                evidence_id="astrbot-notice-revision",
                time_kind=(replacement_date or {}).get("time_kind", "deadline"),
            )
            replacement_date["raw_text"] = (
                " ".join(
                    str(value) for value in (data.patch.date_text, data.patch.time_text) if value
                )
                or replacement_date["raw_text"]
            )
        if not replacement_date:
            raise NotiDoError("NOTICE_DATE_MISSING", "通知缺少日期快照，请核对目标。")
        requirements = (
            data.notice.requirements
            if data.notice.requirements is not None
            else prior.get("requirements", [])
        )
        replacement = managed_notes(
            marker,
            requirements,
            [*data.notice.evidence, *data.notice.visual_evidence],
            replacement_date,
        )
        content = replace_managed(target.get("content") or "", old_region, replacement, marker)
        if len(content) > 10000:
            raise NotiDoError("NOTES_TOO_LONG", "保留用户备注后超过备注上限，请精简要求。")
        action = (
            await self.service.db.read(
                "SELECT revision FROM action_items WHERE id=:a", {"a": link["action_id"]}
            )
        )[0]
        plan.update(
            {
                "action_id": link["action_id"],
                "item_revision": action["revision"] + 1,
                "notice_id": link["notice_id"],
                "managed_notice_id": marker,
                "source_published_at": new_time,
                "source_group_id": source["source_group_id"],
                "normalized_date": replacement_date,
                "requirements": requirements,
                "source_evidence": [e.model_dump() for e in data.notice.evidence],
                "visual_evidence": [e.model_dump() for e in data.notice.visual_evidence],
            }
        )
        plan["fields"]["content"] = content
