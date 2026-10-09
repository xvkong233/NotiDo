"""Fixed argv adapter for the inspected @suibiji/dida-cli 0.1.14."""

import asyncio
import json
import os
import signal
from dataclasses import dataclass
from pathlib import Path

from .errors import NotiDoError
from .keys import canonical
from .retry import ReadRetryBudget


@dataclass
class CLIResponse:
    value: object | None = None
    error: str | None = None
    side_effect: str = "none"


class CLIRunner:
    def __init__(self, node: str, script: str, home: Path, output_limit=1024**2):
        self.node, self.script, self.home = node, script, home
        self.limit = output_limit

    async def call(
        self,
        args: list[str],
        *,
        write=False,
        deadline_seconds=None,
        plain_success=False,
        envelope=False,
    ):
        if not Path(self.node).is_absolute() or not Path(self.script).is_absolute():
            return CLIResponse(error="CLI_PATH_INVALID")
        if not await asyncio.to_thread(Path(self.node).is_file) or not await asyncio.to_thread(
            Path(self.script).is_file
        ):
            return CLIResponse(error="CLI_UNAVAILABLE")
        # Node os.homedir uses USERPROFILE on Windows and HOME on Linux.
        env = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home), "NO_COLOR": "1"}
        options = {"start_new_session": True} if os.name != "nt" else {"creationflags": 0x00000200}
        try:
            process = await asyncio.create_subprocess_exec(
                self.node,
                self.script,
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                **options,
            )
        except OSError:
            return CLIResponse(error="CLI_START_FAILED")

        async def limited(stream):
            buffer = bytearray()
            while chunk := await stream.read(65536):
                buffer.extend(chunk)
                if len(buffer) > self.limit:
                    raise OverflowError
            return bytes(buffer)

        readers = [
            asyncio.create_task(limited(process.stdout)),
            asyncio.create_task(limited(process.stderr)),
        ]
        try:
            stdout, _ = await asyncio.wait_for(
                asyncio.gather(*readers), deadline_seconds or (20 if write else 60)
            )
            await process.wait()
        except (TimeoutError, OverflowError, asyncio.CancelledError) as exc:
            if process.returncode is None:
                if os.name == "nt":
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill",
                        "/PID",
                        str(process.pid),
                        "/T",
                        "/F",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await killer.wait()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            if isinstance(exc, asyncio.CancelledError):
                raise
            return CLIResponse(
                error="CLI_TIMEOUT" if isinstance(exc, TimeoutError) else "CLI_OUTPUT_LIMIT",
                side_effect="unknown" if write else "none",
            )
        # stderr may contain remote user data or credentials; never log or return it.
        if envelope:
            try:
                payload = json.loads(stdout)
                if (
                    payload.get("contract_version") != 1
                    or payload.get("side_effect") not in ("none", "applied", "unknown")
                    or not isinstance(payload.get("ok"), bool)
                ):
                    raise ValueError
                if payload["ok"] and process.returncode == 0:
                    return CLIResponse(value=payload["data"], side_effect=payload["side_effect"])
                if not payload["ok"] and process.returncode != 0:
                    return CLIResponse(
                        value={"id": payload.get("remote_id")},
                        error=payload.get("error", {}).get("code", "CLI_FAILED"),
                        side_effect=payload["side_effect"],
                    )
            except (ValueError, KeyError, AttributeError):
                pass
            return CLIResponse(
                error="CLI_OUTPUT_INVALID", side_effect="unknown" if write else "none"
            )
        if process.returncode != 0:
            return CLIResponse(error="CLI_EXIT_FAILURE", side_effect="unknown" if write else "none")
        if plain_success:
            if stdout.decode("utf-8", errors="replace").strip() != "任务已完成":
                return CLIResponse(
                    error="CLI_OUTPUT_INVALID", side_effect="unknown" if write else "none"
                )
            return CLIResponse(value={"accepted": True}, side_effect="applied")
        try:
            value = json.loads(stdout)
        except (ValueError, UnicodeError):
            return CLIResponse(
                error="CLI_OUTPUT_INVALID", side_effect="unknown" if write else "none"
            )
        return CLIResponse(value=value, side_effect="applied" if write else "none")


class DidaGateway:
    def __init__(
        self,
        runner: CLIRunner,
        *,
        task_extension=None,
        attachment_runner=None,
        attachment_verified=False,
    ):
        self.runner = runner
        self.task_extension, self.attachment_runner = task_extension, attachment_runner
        self.attachment_verified = attachment_verified

    async def read_call(self, runner, args, **kwargs):
        budget = ReadRetryBudget(60)
        while True:
            try:
                remaining = budget.remaining()
            except TimeoutError:
                return CLIResponse(error="CLI_TIMEOUT")
            result = await runner.call(args, deadline_seconds=min(20, remaining), **kwargs)
            if result.side_effect != "none" or result.error not in {
                "CLI_TIMEOUT",
                "REMOTE_HTTP_408",
                "REMOTE_HTTP_429",
                "REMOTE_HTTP_500",
                "REMOTE_HTTP_502",
                "REMOTE_HTTP_503",
                "REMOTE_HTTP_504",
            }:
                return result
            try:
                if not await budget.backoff():
                    return result
            except TimeoutError:
                return CLIResponse(error="CLI_TIMEOUT")

    async def projects(self):
        result = await self.read_call(self.runner, ["project", "list", "--json"])
        if result.error or not isinstance(result.value, list):
            raise NotiDoError(
                result.error or "CLI_SCHEMA_INVALID", "无法完整读取滴答清单。", status=503
            )
        if any(
            not isinstance(x, dict)
            or not isinstance(x.get("id"), str)
            or not isinstance(x.get("name"), str)
            for x in result.value
        ):
            raise NotiDoError("CLI_SCHEMA_INVALID", "清单输出与锁定契约不符。", status=503)
        return result.value

    async def tasks(self, project_id):
        result = await self.read_call(self.runner, ["project", "data", project_id, "--json"])
        if (
            result.error
            or not isinstance(result.value, dict)
            or not isinstance(result.value.get("tasks"), list)
        ):
            raise NotiDoError(
                result.error or "CLI_SCHEMA_INVALID", "此清单读取失败，查询范围不完整。", status=503
            )
        return result.value["tasks"]

    async def get(self, project_id, task_id):
        result = await self.read_call(self.runner, ["task", "get", project_id, task_id, "--json"])
        if result.error or not isinstance(result.value, dict):
            raise NotiDoError(
                result.error or "CLI_SCHEMA_INVALID", "未取得可靠任务回读，保留待核查。", status=503
            )
        task = result.value
        if task.get("id") != task_id or task.get("projectId") != project_id:
            raise NotiDoError("RESULT_MISMATCH", "回读任务标识不匹配。", status=503)
        return task

    async def write(self, kind, project_id, fields, task_id=None):
        if kind == "delete":
            if not self.task_extension:
                return CLIResponse(error="CLI_DELETE_UNAVAILABLE")
            return await self.task_extension.call(
                ["delete", project_id, task_id], write=True, envelope=True
            )
        if self.task_extension and (
            kind == "complete" or kind == "update" and fields.get("isAllDay") is False
        ):
            return await self.task_extension.call(
                [kind, project_id, task_id, canonical(fields)], write=True
            )
        if kind == "complete":
            return await self.runner.call(
                ["task", "complete", project_id, task_id], write=True, plain_success=True
            )
        if kind not in ("create", "update"):
            return CLIResponse(error="CLI_OPERATION_UNSUPPORTED")
        args = ["task", kind]
        if kind == "update":
            args.extend([task_id, f"--id={task_id}"])
        args.extend([f"--project={project_id}", "--json"])
        mapping = {
            "title": "title",
            "content": "content",
            "dueDate": "due-date",
            "startDate": "start-date",
            "timeZone": "time-zone",
            "priority": "priority",
            "repeatFlag": "repeat",
            "repeatFrom": "repeat-from",
        }
        for field, value in fields.items():
            if field == "isAllDay":
                if value:
                    args.append("--all-day")
                elif kind == "update":
                    # The official CLI has no --no-all-day. Avoid silently losing requested semantics.
                    return CLIResponse(error="CLI_ALL_DAY_CLEAR_UNSUPPORTED")
            elif field in mapping:
                if value is None and kind == "create":
                    continue
                args.append(f"--{mapping[field]}={'null' if value is None else value}")
            else:
                return CLIResponse(error="CLI_FIELD_UNSUPPORTED")
        return await self.runner.call(args, write=True)

    async def probe_task(self, project_id, task_id):
        if not self.task_extension:
            raise NotiDoError("CLI_PROBE_UNAVAILABLE", "缺少可靠删除核查能力。")
        result = await self.read_call(self.task_extension, ["probe", project_id, task_id])
        value = result.value
        if (
            result.error
            or not isinstance(value, dict)
            or value.get("id") != task_id
            or value.get("projectId") != project_id
            or type(value.get("exists")) is not bool
        ):
            raise NotiDoError(
                result.error or "CLI_SCHEMA_INVALID", "未取得可靠任务存在性核查，保持待核查。"
            )
        if value["exists"] and self.attachment_runner and self.attachment_verified:
            # Open API can return a soft-deleted record without its deleted flag.
            # Only the same-account web API's explicit tombstone proves this case;
            # absence from the active list alone could also mean completion/moving.
            native = await self.read_call(
                self.attachment_runner,
                ["task-probe", f"--project={project_id}", f"--task={task_id}"],
                envelope=True,
            )
            probe = native.value
            if (
                native.error
                or not isinstance(probe, dict)
                or probe.get("id") != task_id
                or probe.get("projectId") != project_id
                or type(probe.get("exists")) is not bool
                or probe.get("proof") not in ("native_deleted_flag", "native_active_flag")
                or probe["exists"] != (probe["proof"] == "native_active_flag")
            ):
                raise NotiDoError(
                    native.error or "CLI_SCHEMA_INVALID", "原生删除标志未取得可靠证据，保持待核查。"
                )
            return probe
        return value

    def attachment_capability(self):
        return {
            "supported": self.attachment_verified,
            "readiness": self.attachment_verified,
            "code": "READY" if self.attachment_verified else "ATTACHMENT_NOT_VERIFIED",
            "reason": "受控中国版 CLI 扩展，原件上传/登记/下载 hash 核验。"
            if self.attachment_verified
            else "中国版原生附件路径尚需联调。",
        }

    async def upload(self, plan):
        if not self.attachment_runner or not self.attachment_verified:
            return CLIResponse(error="ATTACHMENT_NOT_VERIFIED")
        return await self.attachment_runner.call(
            [
                "upload",
                f"--project={plan['project_id']}",
                f"--task={plan['task_id']}",
                f"--attachment={plan['attachment_id']}",
                f"--file={plan['blob_path']}",
                f"--name={plan['name']}",
                f"--sha256={plan['hash']}",
            ],
            write=True,
            deadline_seconds=120,
            envelope=True,
        )

    async def inspect_upload(self, plan, remote_id):
        if not self.attachment_runner:
            raise NotiDoError("ATTACHMENT_UNAVAILABLE", "附件核查 CLI 不可用。", status=503)
        result = await self.read_call(
            self.attachment_runner,
            [
                "inspect",
                f"--project={plan['project_id']}",
                f"--task={plan['task_id']}",
                f"--attachment={remote_id}",
            ],
            envelope=True,
        )
        if result.error:
            raise NotiDoError(result.error, "附件尚未取得可靠核验，保留待处理。", status=503)
        import re

        value = result.value
        if (
            not isinstance(value, dict)
            or value.get("id") != remote_id
            or value.get("project_id") != plan["project_id"]
            or value.get("task_id") != plan["task_id"]
            or not isinstance(value.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
            or type(value.get("size")) is not int
            or value["size"] < 0
        ):
            raise NotiDoError(
                "CLI_SCHEMA_INVALID", "附件核查输出不符合目标与原件契约。", status=503
            )
        return result.value
