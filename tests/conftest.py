import asyncio
from datetime import UTC, datetime

import pytest
from legacy_flow import LegacyFlow

from notido.cli import CLIResponse
from notido.db import execute
from notido.keys import uid
from notido.models import PLAN_ADAPTER, InputEnvelope, Segment, Settings
from notido.native import NativeTools
from notido.service import Service


class HistoricalService(LegacyFlow, Service):
    def __init__(self, *args):
        super().__init__(*args)
        self.native_ai = False
        self.model_limit = asyncio.Semaphore(2)


class FakeBridge:
    instance_id = "test-instance"
    provider_id = "test-provider"

    def __init__(self):
        self.calls, self.receipts, self.plan = [], [], None
        self.reply_state = "sent"

    async def call_provider(self, payload, **kwargs):
        self.calls.append(payload)
        plan = self.plan(payload) if callable(self.plan) else self.plan
        return PLAN_ADAPTER.validate_python(plan)

    async def reply(self, origin, body):
        self.receipts.append((origin, body))
        return self.reply_state


class FakeGateway:
    def __init__(self):
        self.items, self.writes, self.failed_projects = {}, [], set()
        self.write_error = None
        self.read_error = False

    async def projects(self):
        return [{"id": "p1", "name": "学习"}, {"id": "p2", "name": "生活"}]

    async def tasks(self, project):
        from notido.errors import NotiDoError

        if project in self.failed_projects:
            raise NotiDoError("READ_FAILED", "读取失败")
        return [
            dict(x)
            for x in self.items.values()
            if x["projectId"] == project and x.get("status", 0) == 0
        ]

    async def get(self, project, task):
        from notido.errors import NotiDoError

        if self.read_error or (project, task) not in self.items:
            raise NotiDoError("READ_FAILED", "回读失败")
        return dict(self.items[(project, task)])

    async def write(self, kind, project, fields, task=None):
        self.writes.append((kind, project, fields, task))
        if self.write_error:
            return self.write_error
        task = task or uid()
        if kind == "create":
            self.items[(project, task)] = {"id": task, "projectId": project, "status": 0, **fields}
        elif kind == "update":
            self.items[(project, task)].update(fields)
        elif kind == "delete":
            del self.items[(project, task)]
        else:
            self.items[(project, task)]["status"] = 2
        return CLIResponse(value={"id": task}, side_effect="applied")

    async def probe_task(self, project, task):
        if self.read_error:
            from notido.errors import NotiDoError

            raise NotiDoError("READ_FAILED", "核查失败")
        return {"id": task, "projectId": project, "exists": (project, task) in self.items}

    def attachment_capability(self):
        return {"supported": False}


@pytest.fixture
async def service(tmp_path):
    # Historical plan fixtures verify retained execution/recovery invariants only.
    result = HistoricalService(tmp_path, FakeBridge(), FakeGateway())
    await result.db.initialize()
    settings = Settings(
        account_ref="account-a",
        credential_generation=1,
        default_project="p1",
        allowed_projects=["p1", "p2"],
        provider_id="test-provider",
        min_free_bytes=0,
    )
    async with result.db.transaction() as conn:
        await execute(
            conn,
            "INSERT INTO account_scopes VALUES ('account-a','personal','cn',NULL,1,'active',0)",
        )
        await execute(
            conn,
            "UPDATE settings SET payload=:p WHERE id='main'",
            {"p": settings.model_dump_json()},
        )
        await execute(
            conn,
            "INSERT INTO actor_bindings VALUES ('binding','personal','test-instance','actor','session',1,0)",
        )
    yield result
    await result.db.close()


def envelope(
    message="message-1",
    text="记一下 2027年12月20日提交报告",
    source_kind="direct_request",
    session="session",
):
    return InputEnvelope(
        event_id=uid(),
        framework_instance_id="test-instance",
        session_key=session,
        actor_key="actor",
        message_key=message,
        received_at=datetime(2026, 10, 7, 8, tzinfo=UTC),
        source_kind=source_kind,
        segments=[Segment(source_id=uid(), kind="text", text=text)],
        reply_origin_ref="origin",
    )


def create_plan(
    payload,
    *,
    intent="create",
    relevance="applies",
    obligation="required",
    ambiguities=None,
    date_text="2027年12月20日",
    title="提交报告",
):
    source = next(x for x in payload["materials"] if x["state"] == "read")
    item = {
        "action": "create",
        "title": title,
        "relevance": relevance,
        "obligation": obligation,
        "requirements": ["PDF 格式"],
        "project_name": None,
        "attachment_asset_ids": [],
        "date_text": date_text,
        "time_text": None,
        "time_kind": "deadline" if date_text else "none",
        "date_kind": "date_only" if date_text else "none",
        "source_evidence": [
            {
                "source_id": source["source_id"],
                "location": source["location"],
                "quote": source["text"],
            }
        ],
        "ambiguities": ambiguities or [],
    }
    plan = {"schema_version": 4, "intent": intent, "ambiguities": [], "tasks": [item]}
    if intent == "ingest_notice":
        plan.update(
            {
                "material_group_id": payload["material_group_id"],
                "notice_summary": "提交报告通知",
                "source_published_at": None,
                "time_basis": None,
                "optional_items": [],
                "information_only": [],
            }
        )
    return plan


async def parsed(service, message="message-1", **kwargs):
    event = envelope(message=message, **kwargs)
    result = await service.intake(event)
    await service.parse_group({"group_id": result["group_id"]})
    return result


async def operation(service):
    values = await service.db.read("SELECT * FROM operations ORDER BY created_at,id")
    return values[0] if values else None


@pytest.fixture
def native(service):
    service.native_ai = True
    service.bridge.identity = lambda event: {
        "i": "test-instance",
        "a": event.envelope.actor_key,
        "s": event.envelope.session_key,
    }
    service.bridge.normalize_event = lambda event: event.envelope
    service.bridge.release_material_refs = lambda envelope: None
    service.bridge.confirmation_text = lambda event: "\n".join(
        segment.text or "" for segment in event.envelope.segments if segment.kind == "text"
    )
    return NativeTools(service)
