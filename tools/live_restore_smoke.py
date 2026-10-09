"""Offline copy of dedicated real history; isolated restore permits only remote reads."""

import asyncio
import json
import re
import sqlite3
import subprocess
import uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

from notido.api import PagesAPI
from notido.cli import CLIRunner, DidaGateway
from notido.errors import NotiDoError
from notido.service import Service
from tools.backup import backup, restore
from tools.container_smoke import ROOT, wait_ready
from tools.live_same_name_smoke import save


class NoProvider:
    instance_id = "notido-local"
    provider_id = ""
    calls = 0

    async def call_provider(self, *args, **kwargs):
        self.calls += 1
        raise RuntimeError("restore review must not generate a plan")

    async def reply(self, *args):
        return "unknown"


class ReadOnlyGateway:
    def __init__(self, inner):
        self.inner = inner
        self.runner = inner.runner
        self.writes = 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def write(self, *args, **kwargs):
        self.writes += 1
        raise RuntimeError("remote writes are disabled in this isolated restore")

    async def upload(self, *args, **kwargs):
        self.writes += 1
        raise RuntimeError("remote uploads are disabled in this isolated restore")


def request(revision, **values):
    async def body():
        return {"request_id": str(uuid.uuid4()), "expected_revision": revision, **values}

    return SimpleNamespace(username="dedicated-fixture-maintainer", json=body)


def prepare(journal_name="live-restore-results.json", container="notido-acceptance"):
    private = ROOT / "runtime-data"
    if journal_name not in (
        "live-restore-results.json",
        "native-restore-results.json",
        "native-delete-restore-results.json",
        "native-complex-restore-results.json",
        "native-outcome-restore-results.json",
        "native-outcome-restore-v7-results.json",
        "native-outcome-restore-v7-r2-results.json",
        "native-outcome-restore-v7-r3-results.json",
    ):
        raise ValueError("unknown restore probe journal")
    roots = {
        "notido-acceptance": "astrbot-acceptance",
        "notido-native-v5": "astrbot-native-v5",
        "notido-native-v6": "astrbot-native-v6",
        "notido-native-v7": "astrbot-native-v7",
    }
    if container not in roots:
        raise ValueError("only isolated authorized acceptance roots are supported")
    journal = private / journal_name
    if journal.exists():
        state = json.loads(journal.read_text(encoding="utf-8"))
    else:
        run = uuid.uuid4().hex
        state = {
            "source_container": container,
            "backup_root": str(private / f"full-backup-{run}"),
            "restore_root": str(private / f"full-restored-{run}"),
        }
        save(journal, state)
    if state.get("source_container", "notido-acceptance") != container:
        raise RuntimeError("restore evidence cannot switch source instances")
    snapshot, target = Path(state["backup_root"]), Path(state["restore_root"])
    if not snapshot.exists():
        source = private / roots[container] / "plugin_data/astrbot_plugin_notido"
        project = json.loads((private / "test-project.json").read_text(encoding="utf-8-sig"))
        with closing(sqlite3.connect((source / "notido.db").as_uri() + "?mode=ro", uri=True)) as db:
            settings = json.loads(
                db.execute("SELECT payload FROM settings WHERE id='main'").fetchone()[0]
            )
            if project["name"] != "NotiDo 验收" or settings["allowed_projects"] != [project["id"]]:
                raise RuntimeError("only the dedicated test scope may be copied")
        try:
            subprocess.run(
                ["docker", "stop", "-t", "120", container],
                capture_output=True,
                check=True,
                timeout=150,
            )
            stopped = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Running}}", container],
                capture_output=True,
                check=True,
                timeout=15,
            )
            if stopped.stdout.strip() != b"false":
                raise RuntimeError("offline backup requires a stopped owner")
            state["backup"] = backup(source, snapshot)
            save(journal, state)
        finally:
            subprocess.run(
                ["docker", "start", container],
                capture_output=True,
                check=True,
                timeout=30,
            )
    if not target.exists():
        state["restore"] = restore(snapshot, target)
        save(journal, state)
    wait_ready()
    return journal, state, target


async def verify(journal, state, target):
    home = target / "cli-home"
    gateway = ReadOnlyGateway(
        DidaGateway(
            CLIRunner(
                r"D:\Program Files\nodejs\node.exe",
                str(ROOT / "node_modules/@suibiji/dida-cli/dist/index.js"),
                home,
            ),
            task_extension=CLIRunner(
                r"D:\Program Files\nodejs\node.exe", str(ROOT / "tools/task-extension.mjs"), home
            ),
            attachment_runner=CLIRunner(
                r"D:\Program Files\nodejs\node.exe", str(ROOT / "tools/attachment-cli.mjs"), home
            ),
            attachment_verified=True,
        )
    )
    bridge = NoProvider()
    service = Service(target, bridge, gateway)
    await service.start()
    try:
        baseline = await service.db.read(
            "SELECT id,state,attempt,remote_id FROM operations ORDER BY id"
        )
        allowed = (
            ("succeeded", "cancelled", "outcome_unknown")
            if state.get("source_container") == "notido-native-v7"
            else ("succeeded", "cancelled")
        )
        if not baseline or any(x["state"] not in allowed for x in baseline):
            raise RuntimeError("this probe expects verified real history")
        api = PagesAPI(service)
        if not state.get("confirmed"):
            if service.maintenance != "RESTORE_REMOTE_REVIEW_REQUIRED":
                raise RuntimeError("restored owner did not enforce the review gate")
            _, revision = await service.db.settings()
            latest = (await api.recovery_status(None))["review"]
            review_id = state.get("review_id")
            if latest and latest["state"] in ("checking", "ready"):
                if latest["config_revision"] != revision or (
                    review_id and review_id != latest["id"]
                ):
                    raise RuntimeError("cannot resume a review from another configuration or run")
                review_id = latest["id"]
            while True:
                try:
                    result = await api.recovery_check(
                        request(revision, **({"review_id": review_id} if review_id else {}))
                    )
                except NotiDoError as error:
                    state.setdefault("read_failures", []).append(
                        {"code": error.code, "details": error.details, "review_id": review_id}
                    )
                    save(journal, state)
                    raise
                review_id = result["review_id"]
                state["review_id"] = review_id
                state["review_ready"] = result["ready"]
                save(journal, state)
                if result["ready"]:
                    break
            review = json.loads((await api.recovery_status(None))["review"]["payload"])
            state["review"] = review
            save(journal, state)
            if len(review["history"]) != len(baseline):
                raise RuntimeError("real history contains changes; inspect the isolated review")
            # Dida adds an image Markdown line to notes when registering a native
            # image. Acknowledge only exact append-only lines pointing to our
            # already registered, independently hash-verified originals.
            acknowledged = []
            for item in review["history"]:
                if item["verification"] == "unknown_without_reliable_id":
                    unknown = next(
                        (row for row in baseline if row["id"] == item["operation_id"]), None
                    )
                    plan = json.loads(
                        (
                            await service.db.read(
                                "SELECT plan FROM operations WHERE id=:id",
                                {"id": item["operation_id"]},
                            )
                        )[0]["plan"]
                    )
                    if (
                        state.get("source_container") != "notido-native-v7"
                        or unknown is None
                        or unknown["state"] != "outcome_unknown"
                        or unknown["attempt"] != 1
                        or unknown["remote_id"] is not None
                        or not plan["fields"]
                        .get("title", "")
                        .startswith("NotiDo 验收 · V7退出边界 · ")
                    ):
                        raise RuntimeError("unexplained unknown history requires inspection")
                    acknowledged.append(
                        {
                            "operation_id": item["operation_id"],
                            "reason": "keep_recorded_shutdown_unknown_without_id_paused; no_link_or_replay",
                        }
                    )
                    continue
                if item["verification"] == "no_replay":
                    cancelled = next(
                        (row for row in baseline if row["id"] == item["operation_id"]), None
                    )
                    if (
                        cancelled is None
                        or cancelled["state"] != "cancelled"
                        or cancelled["attempt"] != 0
                        or cancelled["remote_id"] is not None
                    ):
                        raise RuntimeError("unexpected nonexecuted history requires review")
                    continue
                if item["verification"] == "deleted_by_confirmed_operation":
                    deletions = await service.db.read(
                        "SELECT * FROM operations WHERE id=:id AND kind='delete' AND state='succeeded'",
                        {"id": item["deletion_operation_id"]},
                    )
                    if len(deletions) != 1 or not item["current"].get("deleted"):
                        raise RuntimeError(
                            "deleted history lacks a confirmed deletion and tombstone"
                        )
                    plan = json.loads(deletions[0]["plan"])
                    probe = await gateway.probe_task(plan["project_id"], deletions[0]["remote_id"])
                    if probe["exists"]:
                        raise RuntimeError("deleted history target reappeared")
                    acknowledged.append(
                        {
                            "operation_id": item["operation_id"],
                            "reason": "verified_recorded_confirmed_deletion",
                            "deletion_operation_id": deletions[0]["id"],
                        }
                    )
                    continue
                if item["verification"] == "matches":
                    continue
                if item["verification"] != "external_change" or item["kind"] not in (
                    "create",
                    "update",
                ):
                    raise RuntimeError("unexplained history changes require review")
                row = (
                    await service.db.read(
                        "SELECT plan FROM operations WHERE id=:id", {"id": item["operation_id"]}
                    )
                )[0]
                plan = json.loads(row["plan"])
                actual = await gateway.get(plan["project_id"], item["remote_id"])
                expected_fields = dict(plan["fields"])
                known_changes = await service.db.read(
                    "SELECT id,kind,plan FROM operations WHERE state='succeeded' AND account_ref=:a AND json_extract(plan,'$.project_id')=:p AND coalesce(json_extract(plan,'$.task_id'),remote_id)=:t AND kind IN ('create','update','complete') ORDER BY created_at,id",
                    {"a": plan["account_ref"], "p": plan["project_id"], "t": item["remote_id"]},
                )
                for change in known_changes:
                    expected_fields.update(json.loads(change["plan"])["fields"])
                    if change["kind"] == "complete":
                        expected_fields["status"] = 2
                expected = expected_fields.get("content") or ""
                current = actual.get("content") or ""
                if not service.fields_match(
                    actual, {k: v for k, v in expected_fields.items() if k != "content"}
                ) or not current.startswith(expected):
                    raise RuntimeError("task fields or original notes were changed")
                links = await service.db.read(
                    "SELECT o.plan,l.remote_id FROM task_attachment_links l JOIN operations o ON o.id=l.operation_id WHERE l.account_ref=:a AND l.project_id=:p AND l.task_id=:t AND l.verified=1",
                    {"a": plan["account_ref"], "p": plan["project_id"], "t": item["remote_id"]},
                )
                known = {}
                for link in links:
                    upload_plan = json.loads(link["plan"])
                    inspected = await gateway.inspect_upload(upload_plan, link["remote_id"])
                    if inspected.get("sha256") != upload_plan["hash"]:
                        raise RuntimeError("appended image original hash did not match")
                    known[link["remote_id"]] = upload_plan["name"]
                suffix = current[len(expected) :]
                for line in suffix.splitlines():
                    if not line:
                        continue
                    match = re.fullmatch(r"!\[(?:image|file)\]\(([a-z0-9]{24})/(.+)\)", line)
                    if not match or known.get(match[1]) != unquote(match[2]):
                        state["unrecognized_appendix"] = {
                            "operation_id": item["operation_id"],
                            "suffix": suffix,
                            "known": known,
                        }
                        save(journal, state)
                        raise RuntimeError(
                            "unknown note appendix cannot be automatically acknowledged"
                        )
                acknowledged.append(
                    {
                        "operation_id": item["operation_id"],
                        "reason": "verified_recorded_task_revisions_and_native_attachment_appendix",
                        "timeline_operation_ids": [change["id"] for change in known_changes],
                    }
                )
            state["acknowledged_changes"] = acknowledged
            save(journal, state)
            try:
                await api.recovery_confirm(
                    request(
                        revision,
                        review_id=result["review_id"],
                        reviewed_remote_history=False,
                        keep_old_operations_paused=True,
                    )
                )
            except NotiDoError as error:
                if error.code != "RESTORE_ACK_REQUIRED":
                    raise
            else:
                raise RuntimeError("review gate accepted a missing acknowledgement")
            # Explicit test-maintainer POST after checking all synthetic history; no remote writes permitted.
            await api.recovery_confirm(
                request(
                    revision,
                    review_id=result["review_id"],
                    reviewed_remote_history=True,
                    keep_old_operations_paused=True,
                )
            )
            state["confirmed"] = True
            save(journal, state)
        if service.maintenance or (target / "restore-review.required").exists():
            raise RuntimeError("durable explicit review did not clear only the maintenance gate")
        holds = await service.db.read(
            "SELECT g.* FROM material_groups g JOIN restored_group_holds h ON h.group_id=g.id WHERE h.released_at IS NULL"
        )
        if not holds:
            # Current native chats keep clarification in AstrBot. This fixture
            # has completed material groups, not the legacy planner's drafts.
            groups = await service.db.read("SELECT state FROM material_groups")
            if (
                state.get("source_container") != "notido-native-v5"
                or not groups
                or any(g["state"] != "completed" for g in groups)
            ):
                raise RuntimeError("expected real pre-restore drafts were not held")
        for group in holds:
            for retired in (service.parse_group, service.read_materials):
                try:
                    await retired({"group_id": group["id"]})
                except NotiDoError as error:
                    if error.code != "ASTRBOT_NATIVE_FLOW_REQUIRED":
                        raise
                else:
                    raise RuntimeError("legacy AI flow remained executable")
        if holds:
            try:
                await api.continue_notice(request(holds[0]["revision"]), holds[0]["id"])
            except NotiDoError as error:
                if error.code != "ASTRBOT_NATIVE_FLOW_REQUIRED":
                    raise
            else:
                raise RuntimeError(
                    "old draft resumed without an explicit reprocess acknowledgement"
                )
        for operation in baseline:
            await service.execute_operation({"operation_id": operation["id"]})
        if state.get("source_container") == "notido-native-v7":
            unknowns = await service.db.read(
                "SELECT state,attempt,remote_id,paused FROM operations WHERE state='outcome_unknown'"
            )
            expected_unknowns = sum(row["state"] == "outcome_unknown" for row in baseline)
            if (
                expected_unknowns < 2
                or len(unknowns) != expected_unknowns
                or any(
                    row["attempt"] != 1 or row["remote_id"] is not None or not row["paused"]
                    for row in unknowns
                )
            ):
                raise RuntimeError("recorded shutdown unknowns were resumed or lost")
        if (
            baseline
            != await service.db.read(
                "SELECT id,state,attempt,remote_id FROM operations ORDER BY id"
            )
            or gateway.writes
            or bridge.calls
        ):
            raise RuntimeError("restore replayed or changed completed history")
        state["checks"] = [
            {"check": "offline_real_db_blob_auth_backup_restore", "pass": True},
            {
                "check": "full_real_task_and_attachment_history_readback",
                "pass": True,
                "operations": len(baseline),
            },
            {"check": "missing_ack_refused_explicit_ack_durable", "pass": True},
            {
                "check": "old_drafts_hold_and_successful_operations_no_replay"
                if holds
                else "native_only_groups_successful_operations_no_replay",
                "pass": True,
                "held_groups": len(holds),
            },
        ]
        save(journal, state)
        print(json.dumps(state["checks"]))
    finally:
        await service.stop()


if __name__ == "__main__":
    asyncio.run(verify(*prepare()))
