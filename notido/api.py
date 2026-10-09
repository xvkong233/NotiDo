import hashlib
import hmac
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from .dates import overdue
from .db import execute, one
from .errors import NotiDoError
from .keys import canonical, key, uid
from .local_cancel import cancel_unstarted
from .models import Settings


class PagesAPI:
    """All handlers authenticate the framework-supplied WebUI principal."""

    def __init__(self, service):
        self.service, self.db = service, service.db

    def register(self, context):
        routes = {
            "status": ("GET", self.status),
            "recovery/status": ("GET", self.recovery_status),
            "recovery/check": ("POST", self.recovery_check),
            "recovery/confirm": ("POST", self.recovery_confirm),
            "projects": ("GET", self.projects),
            "settings": ("GET", self.settings),
            "settings/save": ("POST", self.save_settings),
            "identity/bind": ("POST", self.bind),
            "auth/task/set": ("POST", self.task_auth),
            "auth/attachment/set": ("POST", self.attachment_auth),
            "auth/clear": ("POST", self.clear_auth),
            "notices": ("GET", self.notices),
            "notices/<id>": ("GET", self.notice),
            "notices/<id>/resolve": ("POST", self.resolve),
            "notices/<id>/continue": ("POST", self.continue_notice),
            "groups/<id>/close": ("POST", self.close_group),
            "groups/<id>/files": ("POST", self.upload_file),
            "assets": ("GET", self.assets),
            "assets/<id>/download": ("GET", self.download),
            "operations": ("GET", self.operations),
            "operations/<id>": ("GET", self.operation),
            "operations/<id>/reconcile": ("POST", self.reconcile),
            "operations/<id>/retry": ("POST", self.retry),
            "operations/<id>/link-existing": ("POST", self.link),
            "operations/<id>/cancel-local": ("POST", self.cancel),
            "operations/<id>/revalidate": ("POST", self.revalidate),
            "receipts": ("GET", self.receipts),
            "receipts/<id>/retry": ("POST", self.receipt_retry),
            "source-capabilities": ("GET", self.sources),
            "sources/enable": ("POST", self.enable_source),
        }
        for route, (method, handler) in routes.items():
            context.register_web_api(
                f"/astrbot_plugin_notido/{route}", self.wrap(handler), [method], f"NotiDo {route}"
            )

    @staticmethod
    def authenticate(request):
        if not request.username or request.plugin_name != "astrbot_plugin_notido":
            raise NotiDoError("WEBUI_AUTH_REQUIRED", "请通过已登录 AstrBot 后台访问。", status=401)

    def wrap(self, handler):
        async def wrapped(**params):
            from astrbot.api.web import json_response, request

            try:
                self.authenticate(request)
                result = await handler(request, **params)
                return result if hasattr(result, "status_code") else json_response(result)
            except NotiDoError as exc:
                return json_response({"error": exc.public()}, status_code=exc.status)
            except (ValueError, KeyError, ValidationError):
                return json_response(
                    {
                        "error": NotiDoError(
                            "INVALID_INPUT", "请求参数不符合契约。", status=400
                        ).public()
                    },
                    status_code=400,
                )
            except Exception:
                return json_response(
                    {
                        "error": NotiDoError(
                            "INTERNAL_ERROR", "操作未完成，请检查后台状态。", status=503
                        ).public()
                    },
                    status_code=503,
                )

        return wrapped

    @staticmethod
    async def body(request, allowed):
        body = await request.json()
        if not isinstance(body, dict) or set(body) - set(allowed) - {
            "request_id",
            "expected_revision",
        }:
            raise NotiDoError("INVALID_INPUT", "请求含未知字段。", status=400)
        if (
            not isinstance(body.get("request_id"), str)
            or not 1 <= len(body["request_id"]) <= 200
            or type(body.get("expected_revision")) is not int
        ):
            raise NotiDoError(
                "REQUEST_CONTRACT_REQUIRED",
                "必须提供 request_id 和 expected_revision。",
                status=400,
            )
        return body

    @staticmethod
    def revision(row, expected):
        if not row:
            raise NotiDoError("NOT_FOUND", "目标不存在。", status=404)
        if row["revision"] != expected:
            raise NotiDoError("REVISION_CONFLICT", "记录已变更，请刷新后重新提交。")

    async def mutate(self, endpoint, target, body, callback):
        result = await self.db.api_mutate(
            endpoint, target, body["request_id"], body["expected_revision"], body, callback
        )
        self.service.wakeup.set()
        return result

    async def status(self, request):
        import asyncio

        settings, revision = await self.db.settings()
        jobs = await self.db.read("SELECT state,count(*) AS count FROM jobs GROUP BY state")
        capability = self.service.gateway.attachment_capability()
        attachment_authorized = await asyncio.to_thread(
            (self.service.gateway.runner.home / ".config/notido-attachments/config.json").is_file
        )
        capability["readiness"] = (
            capability.get("supported", False)
            and attachment_authorized
            and bool(settings.account_ref)
        )
        capability["authorized"] = attachment_authorized
        return {
            "name": "知办 · NotiDo",
            "revision": revision,
            "maintenance": self.service.maintenance,
            "astrbot_bridge": {"supported": True, "version": "4.28.2"},
            "provider": {
                "configured": None,
                "managed_by": "astrbot",
                "selection": "current_session",
            },
            "task_cli": {"version": "0.1.14", "account_confirmed": bool(settings.account_ref)},
            "attachment_cli": capability,
            "db": "ready",
            "worker": "stopping" if self.service.stopping else "running",
            "jobs": jobs,
            "retention": self.service.retention_status,
            "checked_at": time.time(),
            "website": "接口预留，采集未实现",
        }

    async def projects(self, request):
        return {
            "projects": await self.service.gateway.projects(),
            "complete": True,
            "checked_at": time.time(),
        }

    async def recovery_status(self, request):
        return await self.service.restore_review.status()

    async def recovery_check(self, request):
        body = await self.body(request, {"review_id"})
        if "review_id" in body and (
            not isinstance(body["review_id"], str) or not 1 <= len(body["review_id"]) <= 200
        ):
            raise NotiDoError("INVALID_INPUT", "复核引用无效。", status=400)
        return await self.service.restore_review.check(self, body)

    async def recovery_confirm(self, request):
        body = await self.body(
            request, {"review_id", "reviewed_remote_history", "keep_old_operations_paused"}
        )
        if not isinstance(body.get("review_id"), str) or not 1 <= len(body["review_id"]) <= 200:
            raise NotiDoError("INVALID_INPUT", "请提供有效复核引用。", status=400)
        return await self.service.restore_review.confirm(self, request, body)

    async def settings(self, request):
        settings, revision = await self.db.settings()
        bindings = await self.db.read("SELECT * FROM actor_bindings")
        excluded = (
            {
                "identity": True,
                "provider_id": True,
                "silence_seconds": True,
                "window_seconds": True,
                "time_budgets": {
                    "text_seconds",
                    "material_seconds",
                    "long_seconds",
                    "provider_seconds",
                },
            }
            if self.service.native_ai
            else set()
        )
        return {
            "settings": settings.model_dump(exclude=excluded),
            "revision": revision,
            "bindings": bindings,
        }

    async def save_settings(self, request):
        body = await self.body(request, {"settings"})
        if self.service.native_ai and set(body["settings"]) & {"identity", "provider_id"}:
            raise NotiDoError(
                "ASTRBOT_MANAGED_SETTING",
                "身份、记忆和模型由 AstrBot 当前会话管理，请使用 AstrBot 设置。",
                status=400,
            )
        proposed = Settings.model_validate(body["settings"])
        from zoneinfo import ZoneInfo

        ZoneInfo(proposed.timezone)
        projects = await self.service.gateway.projects() if proposed.allowed_projects else []
        if (
            any(
                x not in {p["id"] for p in projects if not p.get("closed")}
                for x in proposed.allowed_projects
            )
            or proposed.default_project
            and proposed.default_project not in proposed.allowed_projects
        ):
            raise NotiDoError(
                "PROJECT_NOT_ALLOWED", "默认/允许清单必须来自当前账号真实清单。", status=400
            )

        async def update(conn, revision):
            row = await one(conn, "SELECT * FROM settings WHERE id='main'")
            self.revision(row, revision)
            current = Settings.model_validate_json(row["payload"])
            protected = (
                "account_ref",
                "account_region",
                "credential_generation",
                "authorization_revision",
            )
            if any(getattr(proposed, x) != getattr(current, x) for x in protected):
                raise NotiDoError(
                    "AUTH_ENDPOINT_REQUIRED", "账号/授权须通过独立授权入口更新。", status=400
                )
            effective = proposed
            if self.service.native_ai:
                # Keep historical fields inert; they are absent from the public settings form.
                effective = proposed.model_copy(
                    update={
                        "identity": current.identity,
                        "provider_id": current.provider_id,
                        "silence_seconds": current.silence_seconds,
                        "window_seconds": current.window_seconds,
                        "time_budgets": proposed.time_budgets.model_copy(
                            update={
                                name: getattr(current.time_budgets, name)
                                for name in (
                                    "text_seconds",
                                    "material_seconds",
                                    "long_seconds",
                                    "provider_seconds",
                                )
                            }
                        ),
                    }
                )
            changed = [
                x for x in Settings.model_fields if getattr(effective, x) != getattr(current, x)
            ]
            affected = 0
            if set(changed) & {"identity", "default_project", "allowed_projects"}:
                affected = (
                    await execute(
                        conn,
                        "UPDATE operations SET paused=1,revision=revision+1 WHERE state='validated'",
                    )
                ).rowcount
            await execute(
                conn,
                "UPDATE settings SET payload=:p,revision=revision+1 WHERE id='main'",
                {"p": effective.model_dump_json()},
            )
            if not self.service.native_ai:
                await execute(
                    conn,
                    "UPDATE users SET identity=:i,revision=revision+1 WHERE id='personal'",
                    {"i": proposed.identity.model_dump_json()},
                )
            return {
                "revision": revision + 1,
                "changed_fields": changed,
                "affected_pending_items": affected,
            }

        return await self.mutate("settings/save", "main", body, update)

    async def bind(self, request):
        body = await self.body(request, {"platform_id", "actor_id", "origin", "enabled"})
        for name in ("platform_id", "actor_id", "origin"):
            if not isinstance(body.get(name), str) or not body[name] or len(body[name]) > 500:
                raise NotiDoError("IDENTITY_INVALID", "须提供真实框架标识。", status=400)
        if type(body.get("enabled")) is not bool:
            raise NotiDoError("IDENTITY_INVALID", "enabled 必须为布尔值。", status=400)
        instance = self.service.bridge.instance_id
        actor, session = (
            key(instance, body["platform_id"], body["actor_id"]),
            key(instance, body["origin"]),
        )

        async def update(conn, revision):
            row = await one(conn, "SELECT * FROM settings WHERE id='main'")
            self.revision(row, revision)
            settings = Settings.model_validate_json(row["payload"])
            settings.authorization_revision += 1
            await execute(
                conn,
                "INSERT INTO actor_bindings VALUES (:id,'personal',:i,:a,:s,:e,0) ON CONFLICT(instance_id,actor_key,session_key) DO UPDATE SET enabled=excluded.enabled,revision=actor_bindings.revision+1",
                {"id": uid(), "i": instance, "a": actor, "s": session, "e": int(body["enabled"])},
            )
            await execute(
                conn,
                "INSERT OR IGNORE INTO sessions (id,user_id) VALUES (:s,'personal')",
                {"s": session},
            )
            affected = (
                await execute(
                    conn,
                    "UPDATE operations SET paused=1,revision=revision+1 WHERE state='validated' AND json_extract(plan,'$.session_key')=:s",
                    {"s": session},
                )
            ).rowcount
            await execute(
                conn,
                "UPDATE settings SET payload=:p,revision=revision+1 WHERE id='main'",
                {"p": settings.model_dump_json()},
            )
            return {
                "revision": revision + 1,
                "actor_key": actor,
                "session_key": session,
                "affected_pending_items": affected,
            }

        return await self.mutate("identity/bind", session, body, update)

    async def listed(self, request, table, fields="*"):
        if table not in {"material_groups", "assets", "operations", "receipt_records"}:
            raise ValueError
        limit = min(100, max(1, int(request.query.get("limit", "20"))))
        cursor = request.query.get("cursor")
        timestamp = "first_at" if table == "material_groups" else "created_at"
        parameters = {"limit": limit + 1}
        where = ""
        if cursor:
            import base64

            before = json.loads(base64.urlsafe_b64decode(cursor))
            if not isinstance(before, list) or len(before) != 2:
                raise ValueError
            where = f"WHERE ({timestamp},id)<(:t,:id)"
            parameters.update({"t": before[0], "id": before[1]})
        entries = await self.db.read(
            f"SELECT {fields} FROM {table} {where} ORDER BY {timestamp} DESC,id DESC LIMIT :limit",
            parameters,
        )
        next_cursor = None
        if len(entries) > limit:
            import base64

            last = entries[limit - 1]
            next_cursor = base64.urlsafe_b64encode(
                canonical([last[timestamp], last["id"]]).encode()
            ).decode()
        return {"items": entries[:limit], "next_cursor": next_cursor}

    async def notices(self, request):
        result = await self.listed(request, "material_groups")
        result["ai_owner"] = "astrbot" if self.service.native_ai else "historical"
        if self.service.native_ai:
            contexts = {
                row["group_id"]: row["declaration"]
                for row in await self.db.read(
                    "SELECT group_id,declaration FROM native_group_outcomes WHERE group_id IN (SELECT value FROM json_each(:ids))",
                    {"ids": canonical([entry["id"] for entry in result["items"]])},
                )
            }
            for entry in result["items"]:
                entry["state_origin"] = (
                    "historical"
                    if entry["id"] not in contexts
                    else "astrbot_declared"
                    if contexts[entry["id"]]
                    else "pending_native_conclusion"
                )
        return result

    async def notice(self, request, id):
        group = await self.db.read("SELECT * FROM material_groups WHERE id=:id", {"id": id})
        if not group:
            group = await self.db.read(
                "SELECT g.* FROM notice_records n JOIN material_groups g ON g.id=n.group_id WHERE n.id=:id",
                {"id": id},
            )
        if not group:
            raise NotiDoError("NOT_FOUND", "通知/材料组不存在。", status=404)
        group = group[0]
        settings, _ = await self.db.settings()
        return {
            "ai_owner": "astrbot" if self.service.native_ai else "historical",
            "group": group,
            "native_outcome": (
                await self.db.read(
                    "SELECT declaration,updated_at FROM native_group_outcomes WHERE group_id=:g",
                    {"g": group["id"]},
                )
            )
            if self.service.native_ai
            else [],
            "material_budget": settings.materials.model_dump(),
            "restore_hold": bool(
                await self.db.read(
                    "SELECT group_id FROM restored_group_holds WHERE group_id=:g AND released_at IS NULL",
                    {"g": id},
                )
            ),
            "segments": await self.db.read(
                "SELECT * FROM material_segments WHERE group_id=:g", {"g": group["id"]}
            ),
            "versions": await self.db.read(
                "SELECT v.* FROM notice_versions v JOIN notice_records n ON n.id=v.notice_id WHERE n.group_id=:g",
                {"g": group["id"]},
            ),
            "session": (
                await self.db.read("SELECT * FROM sessions WHERE id=:s", {"s": group["session_id"]})
            )[0],
            "saved_targets": await self.db.read(
                "SELECT id,kind,remote_id,plan FROM operations WHERE state='succeeded' AND kind IN ('create','update') AND json_extract(plan,'$.group_id')=:g",
                {"g": group["id"]},
            ),
        }

    async def resolve(self, request, id):
        if self.service.native_ai:
            raise NotiDoError(
                "ASTRBOT_NATIVE_FLOW_REQUIRED",
                "请在 AstrBot 原会话回答并继续，插件后台不再独立进行模型追问。",
            )
        body = await self.body(request, {"question_ref", "answer"})

        async def update(conn, revision):
            group = await one(conn, "SELECT * FROM material_groups WHERE id=:id", {"id": id})
            self.revision(group, revision)
            if await one(
                conn,
                "SELECT group_id FROM restored_group_holds WHERE group_id=:g AND released_at IS NULL",
                {"g": id},
            ):
                raise NotiDoError(
                    "RESTORE_REPROCESS_CONFIRMATION", "恢复前的草稿须先复核并明确重新处理。"
                )
            session = await one(
                conn, "SELECT * FROM sessions WHERE id=:s", {"s": group["session_id"]}
            )
            question = json.loads(session["question"] or "null")
            if (
                not question
                or question["question_ref"] != body["question_ref"]
                or question["group_id"] != id
                or session["question_expires"] < time.time()
            ):
                raise NotiDoError("QUESTION_EXPIRED", "问题已失效，请继续处理并获取新问题。")
            clarification = self.service.answer_context(question, body["answer"])
            await execute(conn, "DELETE FROM processing_budgets WHERE group_id=:g", {"g": id})
            await execute(
                conn,
                "UPDATE sessions SET question=NULL,question_expires=NULL,revision=revision+1 WHERE id=:s",
                {"s": group["session_id"]},
            )
            await execute(
                conn,
                "UPDATE material_groups SET state='awaiting_materials',revision=revision+1 WHERE id=:id",
                {"id": id},
            )
            await self.db.job(
                conn,
                "parse_group",
                f"clarify:{body['question_ref']}",
                {"group_id": id, "clarification": clarification},
            )
            return {"revision": revision + 1, "queued": True}

        response = await self.mutate("notices/resolve", id, body, update)
        self.service.processing_deadlines.pop(id, None)
        return response

    async def close_group(self, request, id):
        if self.service.native_ai:
            raise NotiDoError(
                "ASTRBOT_NATIVE_FLOW_REQUIRED", "材料组织由 AstrBot 会话负责，请在原会话继续。"
            )
        body = await self.body(request, set())

        async def update(conn, revision):
            group = await one(conn, "SELECT * FROM material_groups WHERE id=:id", {"id": id})
            self.revision(group, revision)
            if group["state"] != "collecting":
                raise NotiDoError("GROUP_NOT_COLLECTING", "此组已结束收集。")
            await execute(
                conn,
                "UPDATE material_groups SET state='awaiting_materials',revision=revision+1 WHERE id=:id",
                {"id": id},
            )
            await self.db.job(conn, "read_materials", f"read:{id}", {"group_id": id})
            return {"revision": revision + 1, "queued": True}

        return await self.mutate("groups/close", id, body, update)

    async def continue_notice(self, request, id):
        if self.service.native_ai:
            raise NotiDoError(
                "ASTRBOT_NATIVE_FLOW_REQUIRED", "请在 AstrBot 原会话继续；旧独立模型草稿仅供核对。"
            )
        body = await self.body(request, {"confirm_restore_reprocess"})
        if (
            "confirm_restore_reprocess" in body
            and type(body["confirm_restore_reprocess"]) is not bool
        ):
            raise NotiDoError("INVALID_INPUT", "重新处理确认值必须是布尔值。", status=400)

        async def update(conn, revision):
            group = await one(conn, "SELECT * FROM material_groups WHERE id=:id", {"id": id})
            self.revision(group, revision)
            hold = await one(
                conn,
                "SELECT * FROM restored_group_holds WHERE group_id=:g AND released_at IS NULL",
                {"g": id},
            )
            if hold:
                if self.service.maintenance:
                    raise NotiDoError("MAINTENANCE", "请先完成恢复历史复核。", status=503)
                if body.get("confirm_restore_reprocess") is not True:
                    raise NotiDoError(
                        "RESTORE_REPROCESS_CONFIRMATION",
                        "备份恢复前的草稿须人工核对，再明确确认重新处理。",
                    )
                uncertain = await one(
                    conn,
                    "SELECT count(*) AS n FROM operations WHERE json_extract(plan,'$.group_id')=:g AND state IN ('executing','outcome_unknown','created_unverified','uploaded_unverified','applied_unverified')",
                    {"g": id},
                )
                if uncertain["n"]:
                    raise NotiDoError(
                        "RESTORE_UNKNOWN_OPERATIONS",
                        "此组有未知或未核验操作，请先核查或关联可靠目标。",
                    )
                resume_id = uid()
                await execute(
                    conn,
                    "UPDATE restored_group_holds SET released_at=:t,released_by=:actor,resume_id=:resume WHERE group_id=:g",
                    {"g": id, "t": time.time(), "actor": request.username, "resume": resume_id},
                )
                await execute(conn, "DELETE FROM processing_budgets WHERE group_id=:g", {"g": id})
                await execute(
                    conn,
                    "UPDATE sessions SET question=NULL,question_expires=NULL WHERE id=:s AND json_extract(question,'$.group_id')=:g",
                    {"s": group["session_id"], "g": id},
                )
                await execute(
                    conn,
                    "UPDATE material_groups SET state='awaiting_materials',revision=revision+1 WHERE id=:g",
                    {"g": id},
                )
                await self.db.job(
                    conn,
                    "read_materials",
                    f"restore-reprocess:{id}:{revision}",
                    {"group_id": id, "resume_id": resume_id},
                )
                return {"revision": revision + 1, "queued": True}
            if group["state"] != "awaiting_clarification":
                raise NotiDoError("GROUP_NOT_WAITING", "此组没有待续办的问题。")
            session = await one(
                conn, "SELECT * FROM sessions WHERE id=:s", {"s": group["session_id"]}
            )
            if session["question"] and (session["question_expires"] or 0) > time.time():
                raise NotiDoError(
                    "ACTIVE_QUESTION_EXISTS", "此会话已有有效问题，请先回答当前问题。"
                )
            previous = await one(
                conn,
                "SELECT * FROM question_history WHERE group_id=:g ORDER BY created_at DESC LIMIT 1",
                {"g": id},
            )
            if not previous:
                raise NotiDoError(
                    "QUESTION_UNAVAILABLE", "历史问题不可用，请查看未开始的操作并重新核验。"
                )
            old_question = json.loads(previous["payload"])
            question = await self.service.ask(
                conn, id, old_question["questions"], old_question["context"]
            )
            return {"revision": revision + 1, "question": question}

        result = await self.mutate("notices/continue", id, body, update)
        if result.get("queued"):
            self.service.processing_deadlines.pop(id, None)
        return result

    async def assets(self, request):
        return await self.listed(request, "assets")

    async def download(self, request, id):
        from astrbot.api.web import file_response

        assets = await self.db.read(
            "SELECT a.*,b.path FROM assets a JOIN blobs b ON b.hash=a.hash WHERE a.id=:id AND a.user_id='personal' AND a.state='ready'",
            {"id": id},
        )
        if not assets:
            raise NotiDoError("ASSET_UNAVAILABLE", "原件不存在或尚未取得。", status=404)
        asset = assets[0]
        return file_response(
            self.service.blobs.path(asset["path"]),
            filename=asset["name"],
            content_type="application/octet-stream",
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
        )

    async def upload_file(self, request, id):
        form, files = await request.form(), await request.files()

        # AstrBot 4.28.2 bridge.upload only sends the file multipart part.
        # Public request query carries the immutable request contract; never credentials.
        def metadata(name):
            values = [x for x in (form.get(name), request.query.get(name)) if x is not None]
            if len(set(values)) > 1:
                raise NotiDoError("INVALID_FILE_REQUEST", "上传元数据冲突。", status=400)
            return values[0] if values else None

        body = {
            "request_id": metadata("request_id"),
            "expected_revision": int(metadata("expected_revision")),
            "sha256": metadata("sha256"),
            "name": metadata("name"),
            "target_operation_id": metadata("target_operation_id"),
        }
        file = files.get("file")
        if not file or not body["name"] or not body["request_id"]:
            raise NotiDoError(
                "INVALID_FILE_REQUEST", "请提供原件、原名、hash 和请求编号。", status=400
            )
        import re

        if (
            not re.fullmatch(r"[a-f0-9]{64}", body["sha256"] or "")
            or len(body["request_id"]) > 200
            or len(body["name"]) > 200
            or any(x in body["name"] for x in ("/", "\\", "\0", "\r", "\n"))
        ):
            raise NotiDoError("INVALID_FILE_REQUEST", "原名、hash 或请求编号无效。", status=400)
        previous = await self.db.read(
            "SELECT * FROM api_requests WHERE user_id='personal' AND endpoint='groups/files' AND target=:g AND request_id=:r",
            {"g": id, "r": body["request_id"]},
        )
        if previous:
            if previous[0]["fingerprint"] != key(body):
                raise NotiDoError("REQUEST_ID_REUSED", "同一请求编号不能用于不同原件。")
            return json.loads(previous[0]["response"])
        settings, _ = await self.db.settings()
        target_plan = None
        if self.service.native_ai and not body["target_operation_id"]:
            raise NotiDoError(
                "ASTRBOT_NATIVE_FLOW_REQUIRED",
                "新增待分析材料请在 AstrBot 原会话发送；后台补件须明确选择已核验任务。",
            )
        if body["target_operation_id"]:
            target = await self.db.read(
                "SELECT * FROM operations WHERE id=:id", {"id": body["target_operation_id"]}
            )
            if (
                not target
                or target[0]["state"] != "succeeded"
                or target[0]["kind"] not in ("create", "update")
            ):
                raise NotiDoError(
                    "LATE_MATERIAL_TARGET_REQUIRED", "附件目标须是已保存并核验的任务。", status=409
                )
            target = target[0]
            target_plan = json.loads(target["plan"])
            if (
                target["account_ref"] != settings.account_ref
                or target_plan["project_id"] not in settings.allowed_projects
                or target_plan["group_id"] != id
            ):
                raise NotiDoError(
                    "TARGET_NOT_ALLOWED", "任务不属于当前账号、允许清单或所选材料组。", status=403
                )
            await self.service.gateway.get(target_plan["project_id"], target["remote_id"])
            target_plan = {
                **target_plan,
                "task_id": target["remote_id"],
                "request_origin": "webui",
                "webui_actor": request.username,
                "credential_generation": settings.credential_generation,
                "identity_snapshot": settings.identity.model_dump(),
                "authorization_revision": settings.authorization_revision,
                "processing_deadline_at": time.time() + settings.time_budgets.long_seconds,
            }
            if self.service.native_ai:
                target_plan.pop("identity_snapshot", None)
        blob = await self.service.blobs.save(
            file, min_free_bytes=settings.min_free_bytes, max_bytes=settings.materials.file_bytes
        )
        if blob["hash"] != body["sha256"]:
            raise NotiDoError("HASH_MISMATCH", "原件 hash 与请求不符。", status=400)

        async def update(conn, revision):
            group = await one(conn, "SELECT * FROM material_groups WHERE id=:id", {"id": id})
            self.revision(group, revision)
            if group["state"] == "cancelled" or (
                target_plan is None
                and group["state"]
                not in ("collecting", "awaiting_materials", "awaiting_clarification")
            ):
                raise NotiDoError(
                    "LATE_MATERIAL_TARGET_REQUIRED",
                    "已处理组的迟到原件须明确关联具体任务，不能自动挂最近任务。",
                )
            total = await one(
                conn,
                "SELECT count(*) AS n,coalesce(sum(b.size),0) AS size FROM assets a LEFT JOIN blobs b ON b.hash=a.hash WHERE a.group_id=:g",
                {"g": id},
            )
            if (
                total["n"] >= settings.materials.group_files
                or total["size"] + blob["size"] > settings.materials.group_bytes
            ):
                raise NotiDoError("MATERIAL_LIMIT", "组原件超过预算。", status=429)
            asset_id = uid()
            await self.service.register_blob(conn, blob)
            await execute(
                conn,
                "INSERT INTO assets VALUES (:id,'personal',:g,NULL,:h,:n,:s,'ready',NULL,:t,0)",
                {
                    "id": asset_id,
                    "g": id,
                    "h": blob["hash"],
                    "n": body["name"],
                    "s": uid(),
                    "t": time.time(),
                },
            )
            await execute(
                conn,
                "UPDATE material_groups SET last_at=:t,revision=revision+1 WHERE id=:id",
                {"id": id, "t": time.time()},
            )
            await execute(conn, "DELETE FROM retention_checks WHERE group_id=:g", {"g": id})
            if self.service.native_ai:
                from .native_outcome import NativeOutcomes

                await NativeOutcomes(self.service).invalidate(conn, id)
                await execute(
                    conn, "DELETE FROM native_material_deliveries WHERE group_id=:g", {"g": id}
                )
                await execute(
                    conn, "DELETE FROM native_material_reads WHERE group_id=:g", {"g": id}
                )
            if target_plan:
                current_settings = Settings.model_validate_json(
                    (await one(conn, "SELECT payload FROM settings WHERE id='main'"))["payload"]
                )
                if (
                    current_settings.account_ref != target_plan["account_ref"]
                    or target_plan["project_id"] not in current_settings.allowed_projects
                ):
                    raise NotiDoError(
                        "TARGET_NOT_ALLOWED", "账号或允许范围在上传期间发生变化。", status=409
                    )
                await self.service.schedule_upload(
                    conn, target_plan, target_plan["task_id"], asset_id
                )
            elif group["state"] != "collecting":
                if group["state"] == "awaiting_clarification":
                    await execute(
                        conn,
                        "UPDATE material_groups SET state='awaiting_materials' WHERE id=:id",
                        {"id": id},
                    )
                await self.db.job(
                    conn,
                    "read_materials",
                    f"read-upload:{asset_id}",
                    {"group_id": id, "read_ref": asset_id},
                )
            current = await one(
                conn, "SELECT revision FROM material_groups WHERE id=:id", {"id": id}
            )
            return {
                "revision": current["revision"],
                "asset_id": asset_id,
                "hash": blob["hash"],
                "attachment_scheduled": target_plan is not None,
            }

        return await self.mutate("groups/files", id, body, update)

    async def operations(self, request):
        result = await self.listed(
            request,
            "operations",
            "id,user_id,account_ref,action_id,kind,state,paused,remote_id,result,attempt,revision,created_at,checked_at",
        )
        return result

    async def operation(self, request, id):
        records = await self.db.read("SELECT * FROM operations WHERE id=:id", {"id": id})
        if not records:
            raise NotiDoError("NOT_FOUND", "操作不存在。", status=404)
        record = records[0]
        plan = json.loads(record["plan"])
        plan.pop("blob_path", None)
        record["plan"] = plan
        versions = await self.db.read(
            "SELECT * FROM operation_plan_versions WHERE operation_id=:id ORDER BY id", {"id": id}
        )
        for version in versions:
            payload = json.loads(version["payload"])
            payload.pop("blob_path", None)
            version["payload"] = payload
        record["plan_versions"] = versions
        return record

    async def revalidate(self, request, id):
        body = await self.body(request, {"confirm", "confirm_applicability", "allow_overdue"})
        if body.get("confirm") is not True or any(
            name in body and type(body[name]) is not bool
            for name in ("confirm_applicability", "allow_overdue")
        ):
            raise NotiDoError("PLAN_CONFIRMATION_REQUIRED", "请先核对计划并明确确认。", status=400)
        async with self.service.write_lock:
            if self.service.maintenance or self.service.stopping:
                raise NotiDoError("MAINTENANCE", "维护中不能重新启用写入。", status=503)

            async def update(conn, revision):
                row = await one(conn, "SELECT * FROM operations WHERE id=:id", {"id": id})
                self.revision(row, revision)
                if row["state"] != "validated" or not row["paused"] or row["attempt"] != 0:
                    raise NotiDoError(
                        "PLAN_REVALIDATION_UNSAFE",
                        "仅支持暂停且从未开始的计划；未知或已执行操作须核查。",
                    )
                original = json.loads(row["plan"])
                if row["kind"] == "delete":
                    raise NotiDoError(
                        "DELETE_CONFIRMATION_REQUIRED",
                        "删除计划须在 AstrBot 原会话重新展示目标并取得二次确认，不能从后台重新启用。",
                    )
                if self.service.native_ai and original.get("delivery_mode") != "framework_tool":
                    raise NotiDoError(
                        "ASTRBOT_NATIVE_FLOW_REQUIRED",
                        "此计划由旧独立模型生成，仅保留历史。请在 AstrBot 原生会话重新核对，不能直接恢复旧写入。",
                    )
                settings_row = await one(conn, "SELECT * FROM settings WHERE id='main'")
                settings = Settings.model_validate_json(settings_row["payload"])
                if settings.account_ref != row["account_ref"]:
                    raise NotiDoError(
                        "ACCOUNT_CHANGED", "必须切回计划原账号，不能用新账号重新执行旧计划。"
                    )
                if original["project_id"] not in settings.allowed_projects:
                    raise NotiDoError("PROJECT_NOT_ALLOWED", "计划清单未获当前授权。")
                identity_changed = (
                    not self.service.native_ai
                    and settings.identity.model_dump() != original.get("identity_snapshot")
                )
                if identity_changed and body.get("confirm_applicability") is not True:
                    raise NotiDoError(
                        "APPLICABILITY_CONFIRMATION_REQUIRED",
                        "身份已变化，请核对当前身份并明确确认此事项仍适用。",
                    )
                if (
                    original.get("normalized_date")
                    and overdue(original["normalized_date"], datetime.now(UTC))
                    and body.get("allow_overdue") is not True
                ):
                    raise NotiDoError(
                        "OVERDUE_CONFIRMATION", "期限已过，请明确确认补记逾期；原期限不会改为今天。"
                    )
                plan = {
                    **original,
                    "plan_id": uid(),
                    "supersedes_plan_id": original["plan_id"],
                    "request_origin": "webui",
                    "webui_actor": request.username,
                    "config_revision": settings_row["revision"],
                    "credential_generation": settings.credential_generation,
                    "authorization_revision": settings.authorization_revision,
                    "identity_snapshot": settings.identity.model_dump(),
                    "allow_overdue": body.get(
                        "allow_overdue", original.get("allow_overdue", False)
                    ),
                    "processing_deadline_at": time.time() + settings.time_budgets.long_seconds,
                    "revalidation": {
                        "actor": request.username,
                        "at": time.time(),
                        "applicability_confirmed": body.get("confirm_applicability", False),
                    },
                }
                if self.service.native_ai:
                    plan.pop("identity_snapshot", None)
                await execute(
                    conn,
                    "UPDATE operations SET plan=:p,paused=0,revision=revision+1 WHERE id=:id AND state='validated' AND paused=1 AND attempt=0",
                    {"p": canonical(plan), "id": id},
                )
                cleared = await execute(
                    conn,
                    "UPDATE sessions SET question=NULL,question_expires=NULL,revision=revision+1 WHERE id=:s AND json_extract(question,'$.context.operation_id')=:op",
                    {"s": original["session_key"], "op": id},
                )
                if cleared.rowcount:
                    await execute(
                        conn,
                        "UPDATE material_groups SET state='partially_done',revision=revision+1 WHERE id=:g AND state='awaiting_clarification'",
                        {"g": original["group_id"]},
                    )
                await self.db.job(
                    conn,
                    "execute_operation",
                    f"revalidate:{id}:{revision}",
                    {"operation_id": id},
                    priority=5,
                    defer=True,
                )
                return {"revision": revision + 1, "queued": True, "plan_id": plan["plan_id"]}

            # Target reads take place before the short mutation transaction; the shared write lock
            # prevents claim, and execute_operation repeats the comparison immediately before write.
            records = await self.db.read("SELECT * FROM operations WHERE id=:id", {"id": id})
            if not records:
                raise NotiDoError("NOT_FOUND", "操作不存在。", status=404)
            original = json.loads(records[0]["plan"])
            settings, _ = await self.db.settings()
            if (
                records[0]["state"] == "validated"
                and records[0]["paused"]
                and records[0]["account_ref"] == settings.account_ref
            ):
                if original["project_id"] not in settings.allowed_projects:
                    raise NotiDoError("PROJECT_NOT_ALLOWED", "计划清单未获当前授权。")
                if original["kind"] in ("update", "complete", "upload"):
                    actual = await self.service.gateway.get(
                        original["project_id"], original["task_id"]
                    )
                    if original["kind"] in ("update", "complete"):
                        watched = set(original["fields"]) | {"title", "status"}
                        differences = {
                            field: {
                                "planned_before": original["before"].get(field),
                                "current": actual.get(field),
                            }
                            for field in watched
                            if original["before"].get(field) != actual.get(field)
                        }
                        if differences:
                            raise NotiDoError(
                                "EXTERNAL_CHANGE",
                                "目标字段或含义已改变，请处理差异后再重新核验。",
                                details={"differences": differences},
                            )
                    elif actual.get("status", 0) != 0:
                        raise NotiDoError("TARGET_COMPLETED", "原件目标已完成，请先核对目标。")
                if original["kind"] == "upload":
                    assets = await self.db.read(
                        "SELECT b.path,b.hash FROM assets a JOIN blobs b ON a.hash=b.hash WHERE a.id=:a AND a.state='ready'",
                        {"a": original["asset_id"]},
                    )
                    if not assets or assets[0]["hash"] != original["hash"]:
                        raise NotiDoError(
                            "ASSET_UNAVAILABLE", "计划原件不可用，请补发并明确关联目标。"
                        )
                    path = self.service.blobs.path(assets[0]["path"])
                    import asyncio

                    if (
                        await asyncio.to_thread(self.service.blobs.hash_file, path)
                        != original["hash"]
                    ):
                        raise NotiDoError("BLOB_CORRUPT", "计划原件校验失败。", status=503)
            return await self.mutate("operations/revalidate", id, body, update)

    async def operation_action(self, request, id, action):
        body = await self.body(
            request, {"task_id", "project_id"} if action == "link-existing" else set()
        )
        verified_task = None
        if action == "link-existing":
            settings, _ = await self.db.settings()
            if body["project_id"] not in settings.allowed_projects:
                raise NotiDoError("PROJECT_NOT_ALLOWED", "目标清单未授权。")
            verified_task = await self.service.gateway.get(body["project_id"], body["task_id"])

        async def update(conn, revision):
            row = await one(conn, "SELECT * FROM operations WHERE id=:id", {"id": id})
            self.revision(row, revision)
            if row["kind"] == "delete" and action == "retry":
                raise NotiDoError(
                    "DELETE_CONFIRMATION_REQUIRED",
                    "删除重试须返回 AstrBot 查询目标并重新二次确认。",
                )
            if action == "cancel-local":
                return {**await cancel_unstarted(conn, id), "queued": False}
            elif action == "retry":
                result = json.loads(row["result"] or "{}")
                if row["state"] != "failed_safe" or result.get("side_effect") != "none":
                    raise NotiDoError("UNSAFE_RETRY", "无明确未发生副作用的证据，必须先核查。")
                await execute(
                    conn,
                    "UPDATE operations SET state='validated',paused=0,revision=revision+1 WHERE id=:id AND state='failed_safe'",
                    {"id": id},
                )
                await self.db.job(
                    conn,
                    "execute_operation",
                    f"retry:{id}:{revision}",
                    {"operation_id": id},
                    priority=5,
                )
            else:
                if row["state"] not in (
                    "outcome_unknown",
                    "created_unverified",
                    "uploaded_unverified",
                    "applied_unverified",
                ):
                    raise NotiDoError("RECONCILE_STATE_INVALID", "此状态不需要核查。")
                if action == "link-existing":
                    plan = json.loads(row["plan"])
                    settings = Settings.model_validate_json(
                        (await one(conn, "SELECT payload FROM settings WHERE id='main'"))["payload"]
                    )
                    if (
                        row["account_ref"] != settings.account_ref
                        or plan["kind"] != "create"
                        or plan["project_id"] != body["project_id"]
                        or not self.service.fields_match(verified_task, plan["fields"])
                    ):
                        raise NotiDoError("LINK_MISMATCH", "既有任务的账号、清单或实际内容不匹配。")
                    await execute(
                        conn,
                        "UPDATE operations SET remote_id=:remote,state='created_unverified',revision=revision+1 WHERE id=:id",
                        {"remote": body["task_id"], "id": id},
                    )
                else:
                    await execute(
                        conn, "UPDATE operations SET revision=revision+1 WHERE id=:id", {"id": id}
                    )
                await self.db.job(
                    conn,
                    "reconcile",
                    f"reconcile:{id}:{revision}",
                    {"operation_id": id},
                    priority=2,
                )
            return {"revision": revision + 1, "queued": action != "cancel-local"}

        return await self.mutate(f"operations/{action}", id, body, update)

    async def reconcile(self, request, id):
        return await self.operation_action(request, id, "reconcile")

    async def retry(self, request, id):
        return await self.operation_action(request, id, "retry")

    async def link(self, request, id):
        return await self.operation_action(request, id, "link-existing")

    async def cancel(self, request, id):
        return await self.operation_action(request, id, "cancel-local")

    async def receipts(self, request):
        return await self.listed(request, "receipt_records")

    async def receipt_retry(self, request, id):
        body = await self.body(request, set())

        async def update(conn, revision):
            row = await one(conn, "SELECT * FROM receipt_records WHERE id=:id", {"id": id})
            self.revision(row, revision)
            if row["state"] == "sent":
                return {"revision": revision, "sent": True}
            await execute(
                conn,
                "UPDATE receipt_records SET state='pending',revision=revision+1 WHERE id=:id",
                {"id": id},
            )
            await self.db.job(
                conn,
                "send_receipt",
                f"receipt-retry:{id}:{revision}",
                {"receipt_id": id},
                priority=1,
            )
            return {"revision": revision + 1, "queued": True}

        return await self.mutate("receipts/retry", id, body, update)

    async def sources(self, request):
        return {"sources": self.service.sources.describe()}

    async def enable_source(self, request):
        body = await self.body(request, {"source"})

        async def update(conn, revision):
            return {
                "enabled": False,
                "code": "SOURCE_NOT_IMPLEMENTED",
                "safe_message": "官网接口预留，采集未实现。",
            }

        return await self.mutate("sources/enable", body.get("source", "website"), body, update)

    def secret_fingerprint(self, secret):
        path = self.service.root / "auth-fingerprint.key"
        if not path.exists():
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as file:
                file.write(os.urandom(32))
        return hmac.new(path.read_bytes(), secret.encode(), hashlib.sha256).hexdigest()

    async def task_auth(self, request):
        return await self.set_auth(request, "task")

    async def attachment_auth(self, request):
        return await self.set_auth(request, "attachment")

    async def set_auth(self, request, kind):
        from .cli import CLIRunner, DidaGateway

        body = await self.body(
            request, {"secret", "account_mode", "verification_project", "verification_task"}
        )
        secret = body.pop("secret", None)
        if not isinstance(secret, str) or len(secret.strip()) < 10 or len(secret) > 16000:
            raise NotiDoError("AUTH_INPUT_INVALID", "授权值无效，请在本机重新授权。", status=400)
        if body.get("account_mode") not in ("same", "new"):
            raise NotiDoError(
                "ACCOUNT_CONFIRMATION_REQUIRED", "须明确同账号重新授权或更换账号。", status=400
            )
        body["secret_fingerprint"] = self.secret_fingerprint(secret)
        endpoint = f"auth/{kind}/set"
        # Deduplication precedes revision and all filesystem side effects.
        previous = await self.db.read(
            "SELECT * FROM api_requests WHERE user_id='personal' AND endpoint=:e AND target='main' AND request_id=:r",
            {"e": endpoint, "r": body["request_id"]},
        )
        if previous:
            if previous[0]["fingerprint"] != key(body):
                raise NotiDoError("REQUEST_ID_REUSED", "同一请求编号不能用于不同授权。")
            return json.loads(previous[0]["response"])
        temporary_home = self.service.root / "auth-staging" / uid()
        relative = (
            Path(".config/dida-cli/config.json")
            if kind == "task"
            else Path(".config/notido-attachments/config.json")
        )
        candidate = temporary_home / relative
        candidate.parent.mkdir(parents=True, exist_ok=True)
        auth_payload = (
            {"access_token": secret.strip()}
            if kind == "task"
            else {"token": secret.strip(), "region": "cn"}
        )
        descriptor = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(canonical(auth_payload))
        runner = (
            self.service.gateway.runner
            if kind == "task"
            else self.service.gateway.attachment_runner
        )
        if runner is None:
            raise NotiDoError("CLI_UNAVAILABLE", "授权 CLI 不可用。", status=503)
        probe = CLIRunner(runner.node, runner.script, temporary_home)
        try:
            if kind == "task":
                await DidaGateway(probe).projects()
            else:
                settings, _ = await self.db.settings()
                if not settings.account_ref or body.get("account_mode") != "same":
                    raise NotiDoError("ACCOUNT_MISMATCH", "附件授权须绑定已确认的当前任务账号。")
                project, task_id = body.get("verification_project"), body.get("verification_task")
                if project not in settings.allowed_projects or not task_id:
                    raise NotiDoError(
                        "ACCOUNT_VERIFICATION_REQUIRED",
                        "提供当前账号允许清单中一个真实任务用于账号匹配核验。",
                    )
                official = await self.service.gateway.get(project, task_id)
                response = await probe.call(
                    ["task-get", f"--project={project}", f"--task={task_id}"], envelope=True
                )
                if (
                    response.error
                    or response.value.get("id") != official["id"]
                    or response.value.get("title") != official["title"]
                ):
                    raise NotiDoError("ACCOUNT_MISMATCH", "任务与附件授权未能核验同账号。")
            async with self.service.write_lock:
                auth_files_changed = False

                async def update(conn, revision):
                    nonlocal auth_files_changed
                    row = await one(conn, "SELECT * FROM settings WHERE id='main'")
                    self.revision(row, revision)
                    settings = Settings.model_validate_json(row["payload"])
                    if kind == "task":
                        if body["account_mode"] == "same" and not settings.account_ref:
                            raise NotiDoError(
                                "ACCOUNT_CONFIRMATION_REQUIRED", "尚无当前账号，请选择新账号。"
                            )
                        if body["account_mode"] == "new":
                            await execute(
                                conn,
                                "UPDATE account_scopes SET state='retired' WHERE state='active'",
                            )
                            (
                                settings.account_ref,
                                settings.default_project,
                                settings.allowed_projects,
                            ) = uid(), None, []
                            settings.credential_generation = 0
                            await execute(
                                conn,
                                "INSERT INTO account_scopes VALUES (:id,'personal','cn',NULL,0,'active',:t)",
                                {"id": settings.account_ref, "t": time.time()},
                            )
                            await execute(
                                conn,
                                "UPDATE sessions SET query=NULL,recent=NULL,query_revision=query_revision+1",
                            )
                            attachment_config = (
                                self.service.gateway.runner.home
                                / ".config/notido-attachments/config.json"
                            )
                            attachment_config.unlink(missing_ok=True)
                            auth_files_changed = True
                        settings.credential_generation += 1
                        await execute(
                            conn,
                            "UPDATE account_scopes SET credential_generation=:g WHERE id=:id",
                            {"g": settings.credential_generation, "id": settings.account_ref},
                        )
                    settings.authorization_revision += 1
                    affected = (
                        await execute(
                            conn,
                            "UPDATE operations SET paused=1,revision=revision+1 WHERE state='validated'",
                        )
                    ).rowcount
                    destination = runner.home / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(candidate, destination)
                    auth_files_changed = True
                    await execute(
                        conn,
                        "UPDATE settings SET payload=:p,revision=revision+1 WHERE id='main'",
                        {"p": settings.model_dump_json()},
                    )
                    return {
                        "revision": revision + 1,
                        "account_ref": settings.account_ref,
                        "authorized": True,
                        "affected_pending_items": affected,
                    }

                try:
                    response = await self.mutate(endpoint, "main", body, update)
                except BaseException:
                    if auth_files_changed:
                        self.mark_auth_commit_unknown()
                    raise
                self.clear_auth_commit_unknown()
                return response
        finally:
            candidate.unlink(missing_ok=True)

    async def clear_auth(self, request):
        body = await self.body(request, {"confirm"})
        if body.get("confirm") != "清除当前授权":
            raise NotiDoError("CLEAR_CONFIRMATION_REQUIRED", "请明确确认清除当前授权。", status=400)
        async with self.service.write_lock:
            auth_files_changed = False

            async def update(conn, revision):
                nonlocal auth_files_changed
                row = await one(conn, "SELECT * FROM settings WHERE id='main'")
                self.revision(row, revision)
                settings = Settings.model_validate_json(row["payload"])
                await execute(
                    conn,
                    "UPDATE account_scopes SET state='paused' WHERE id=:a",
                    {"a": settings.account_ref},
                )
                settings.authorization_revision += 1
                settings.account_ref, settings.default_project, settings.allowed_projects = (
                    None,
                    None,
                    [],
                )
                await execute(
                    conn,
                    "UPDATE operations SET paused=1,revision=revision+1 WHERE state='validated'",
                )
                await execute(
                    conn,
                    "UPDATE sessions SET query=NULL,recent=NULL,query_revision=query_revision+1",
                )
                for relative in (
                    ".config/dida-cli/config.json",
                    ".config/notido-attachments/config.json",
                ):
                    (self.service.gateway.runner.home / relative).unlink(missing_ok=True)
                    auth_files_changed = True
                await execute(
                    conn,
                    "UPDATE settings SET payload=:p,revision=revision+1 WHERE id='main'",
                    {"p": settings.model_dump_json()},
                )
                return {"revision": revision + 1, "authorized": False}

            try:
                response = await self.mutate("auth/clear", "main", body, update)
            except BaseException:
                if auth_files_changed:
                    self.mark_auth_commit_unknown()
                raise
            self.clear_auth_commit_unknown()
            return response

    def mark_auth_commit_unknown(self):
        self.service.maintenance = "AUTH_COMMIT_UNKNOWN"
        marker = self.service.root / "maintenance.required"
        marker.write_text("AUTH_COMMIT_UNKNOWN\n", encoding="utf-8")
        os.chmod(marker, 0o600)

    def clear_auth_commit_unknown(self):
        if self.service.maintenance == "AUTH_COMMIT_UNKNOWN":
            (self.service.root / "maintenance.required").unlink(missing_ok=True)
            self.service.maintenance = (
                "RESTORE_REMOTE_REVIEW_REQUIRED"
                if (self.service.root / "restore-review.required").exists()
                else None
            )
