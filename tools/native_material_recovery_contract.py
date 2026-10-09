"""Real AstrBot public File/event recovery contract, isolated DB, zero AI/network.

Faults affect only a new temporary fixture root. This proves acquisition and
storage guards; it is not a claim that a Provider interpreted damaged contents.
"""

import asyncio
import hashlib
import importlib.util
import json
import sys
import tempfile
import time
import types
from pathlib import Path

from astrbot.api.message_components import File

from notido.db import execute
from notido.models import Settings
from tools.handler_contract_smoke import ContextFixture, event

PROJECT_ROOT = Path(sys.modules["notido"].__file__).resolve().parent.parent
STARTED = asyncio.Event()


class PendingFile(File):
    async def get_file(self, allow_return_url=False):
        STARTED.set()
        await asyncio.Event().wait()


class NoNetwork:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        async def forbidden(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError("material recovery fixture must not access Dida")

        return forbidden


def decoded(result):
    return json.loads(result if isinstance(result, str) else result.content[0].text)


def load_plugin():
    package = types.ModuleType("notido_material_recovery_fixture")
    package.__path__ = [str(PROJECT_ROOT)]
    sys.modules[package.__name__] = package
    spec = importlib.util.spec_from_file_location(
        package.__name__ + ".main", PROJECT_ROOT / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.NotiDoPlugin


def source_version():
    files = sorted([PROJECT_ROOT / "main.py", *(PROJECT_ROOT / "notido").rglob("*.py")])
    return hashlib.sha256(
        b"".join(
            str(p.relative_to(PROJECT_ROOT)).encode()
            + b"\0"
            + hashlib.sha256(p.read_bytes()).digest()
            for p in files
        )
    ).hexdigest()


def prepare_fixture_parent():
    fixture_parent = Path("/AstrBot/data/acceptance-probes/material-recovery")
    fixture_parent.mkdir(parents=True, exist_ok=True)
    if not fixture_parent.resolve().is_relative_to(
        Path("/AstrBot/data/acceptance-probes").resolve()
    ):
        raise RuntimeError("fixture directory escaped the acceptance root")
    return fixture_parent


async def main():
    Plugin = load_plugin()
    checks = []
    contexts, gateways = [], []
    fixture_parent = await asyncio.to_thread(prepare_fixture_parent)
    with tempfile.TemporaryDirectory(dir=fixture_parent) as temporary:
        folder = Path(temporary)
        data_root = folder / "isolated-data"
        incoming = event("材料取件中断契约", "acquisition-pending")
        incoming.message_obj.message.append(
            PendingFile(name="pending.txt", file=str(folder / "never.txt"))
        )

        async def open_plugin():
            context, gateway = ContextFixture(), NoNetwork()
            contexts.append(context)
            gateways.append(gateway)
            plugin = Plugin(
                context, {"data_root": str(data_root), "instance_id": "material-recovery-fixture"}
            )
            plugin.service.gateway = gateway
            await plugin.initialize()
            return plugin

        plugin = await open_plugin()
        identity = plugin.service.bridge.identity(incoming)
        settings = Settings(
            account_ref="material-recovery-fixture-account",
            credential_generation=1,
            allowed_projects=[],
            min_free_bytes=0,
        )
        async with plugin.service.db.transaction() as conn:
            await execute(
                conn,
                "INSERT INTO account_scopes VALUES (:a,'personal','cn',NULL,1,'active',:t)",
                {"a": settings.account_ref, "t": time.time()},
            )
            await execute(conn, "UPDATE settings SET payload=:p", {"p": settings.model_dump_json()})
            await execute(
                conn,
                "INSERT INTO actor_bindings VALUES ('fixture','personal',:i,:a,:s,1,0)",
                identity,
            )
        waiting = asyncio.create_task(plugin.tools.invoke(incoming, "materials", {}))
        await asyncio.wait_for(STARTED.wait(), 5)
        pending = await plugin.service.db.read("SELECT state,hash FROM assets")
        assert pending == [{"state": "pending", "hash": None}]
        assert not await plugin.service.db.read("SELECT * FROM blobs")
        checks.append(
            {
                "check": "interrupted_public_file_acquisition_persisted_pending_without_blob",
                "pass": True,
            }
        )
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        assert not plugin.service.bridge.material_refs
        await plugin.service.stop()

        plugin = await open_plugin()
        try:
            unavailable = decoded(await plugin.tools.invoke(incoming, "materials", {}))
            assert any(
                x.get("reason") == "MATERIAL_RESEND_REQUIRED" for x in unavailable["unknowns"]
            )
            asset = (await plugin.service.db.read("SELECT state,hash FROM assets"))[0]
            assert asset == {"state": "unavailable", "hash": None}
            outcome = decoded(
                await plugin.tools.invoke(incoming, "outcome", {"state": "completed"})
            )
            assert outcome["state"] == "awaiting_materials"
            checks.append(
                {
                    "check": "restart_expired_reference_requests_resend_no_fake_ready_blob",
                    "pass": True,
                }
            )

            original = folder / "reprovided.txt"
            original.write_text("重新提供的完整合成原件，UTF-8，无任务指令。", "utf-8")
            digest = hashlib.sha256(original.read_bytes()).hexdigest()
            resend = event("明确重发原件", "acquisition-resend")
            resend.message_obj.message.append(File(name=original.name, file=str(original)))
            supplied = decoded(await plugin.tools.invoke(resend, "materials", {}))
            assert not supplied["unknowns"] and any(
                "完整合成原件" in x.get("text", "") for x in supplied["items"]
            )
            ready = await plugin.service.db.read("SELECT * FROM assets WHERE state='ready'")
            assert len(ready) == 1 and ready[0]["hash"] == digest
            outcome = decoded(await plugin.tools.invoke(resend, "outcome", {"state": "completed"}))
            assert outcome["state"] == "completed"
            checks.append(
                {
                    "check": "new_public_file_resend_persists_exact_bytes_and_reads_body",
                    "pass": True,
                }
            )

            corrupt = folder / "unreadable.txt"
            corrupt.write_bytes(b"\xff\xfe\x00damaged-input")
            broken = event("损坏编码的合成原件", "unreadable-input")
            broken.message_obj.message.append(File(name=corrupt.name, file=str(corrupt)))
            unreadable = decoded(await plugin.tools.invoke(broken, "materials", {}))
            assert any(x.get("reason") == "ENCODING_UNKNOWN" for x in unreadable["unknowns"])
            outcome = decoded(await plugin.tools.invoke(broken, "outcome", {"state": "completed"}))
            assert outcome["state"] == "awaiting_materials"
            checks.append(
                {"check": "unreadable_original_cannot_be_declared_complete", "pass": True}
            )

            corrected = event("重新提供UTF-8原件，不猜旧损坏正文", "unreadable-resend")
            corrected.message_obj.message.append(File(name=original.name, file=str(original)))
            reread = decoded(await plugin.tools.invoke(corrected, "materials", {}))
            assert not reread["unknowns"]
            assert (
                decoded(await plugin.tools.invoke(corrected, "outcome", {"state": "completed"}))[
                    "state"
                ]
                == "completed"
            )
            old = await plugin.service.db.read(
                "SELECT state FROM material_groups WHERE id=:g", {"g": unreadable["group_id"]}
            )
            assert old[0]["state"] == "awaiting_materials"
            checks.append(
                {
                    "check": "resend_reads_new_valid_material_preserves_old_unknown_history",
                    "pass": True,
                }
            )
            stored = plugin.service.blobs.path("blobs/" + digest)
            assert stored.resolve().is_relative_to(data_root.resolve())
        finally:
            await plugin.service.stop()

        # Corrupt only this isolated fixture's exact, known content-addressed file.
        original_bytes = original.read_bytes()
        stored.write_bytes(b"synthetic-persisted-blob-corruption")
        broken_plugin = await open_plugin()
        try:
            assert broken_plugin.service.maintenance == "BLOB_CORRUPT"
            blocked = decoded(await broken_plugin.tools.invoke(resend, "materials", {}))
            assert blocked["error"] == "MAINTENANCE"
            checks.append(
                {
                    "check": "persisted_blob_corruption_enters_maintenance_and_blocks_materials",
                    "pass": True,
                }
            )
        finally:
            await broken_plugin.service.stop()
        assert hashlib.sha256(original_bytes).hexdigest() == digest
        stored.write_bytes(original_bytes)
        restored = await open_plugin()
        try:
            assert restored.service.maintenance is None
            await restored.service.check_blobs()
            checks.append(
                {"check": "exact_original_byte_repair_passes_startup_integrity", "pass": True}
            )
            assert not await restored.service.db.read("SELECT * FROM operations")
        finally:
            await restored.service.stop()
    assert all(not c.models and not c.sent for c in contexts)
    assert all(not g.calls for g in gateways)
    result = {
        "runtime_code_sha256": source_version(),
        "checks": checks,
        "scope": "real_astrbot_public_fixture_isolated_state",
        "provider_calls": 0,
        "dida_calls": 0,
        "remote_writes": 0,
    }
    output = fixture_parent / (result["runtime_code_sha256"][:12] + "-results.json")
    output.write_text(json.dumps(result, indent=2), "utf-8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
