import asyncio
import json

import pytest
from test_native_tools import event

from notido import native_read_budget
from notido.db import execute
from notido.models import Segment
from notido.native import NativeTools
from tools.backup import backup, restore


async def material_event(service, tmp_path, count=1, message="budget"):
    incoming = event(message)
    incoming.envelope.segments = [Segment(source_id=f"file-{i}", kind="file") for i in range(count)]
    original = tmp_path / "material.txt"
    original.write_text("实际材料正文", encoding="utf-8")

    async def acquire(source):
        return str(original), f"{source}.txt"

    service.bridge.acquire_material = acquire
    return incoming


def decoded():
    return {
        "segments": [{"location": "text", "text": "实际材料正文"}],
        "visuals": [],
        "unknowns": [],
    }


def manifest(result):
    return json.loads(result.content[0].text)


async def test_group_180_seconds_is_cumulative_and_unread_files_block_complete(
    native, service, tmp_path, monkeypatch
):
    incoming = await material_event(service, tmp_path, count=4)
    clock, deadlines = [1000.0], []
    monkeypatch.setattr(native_read_budget, "monotonic", lambda: clock[0])

    async def reader(*args, deadline_seconds, **kwargs):
        deadlines.append(deadline_seconds)
        clock[0] += 60
        return decoded()

    service.blobs.read = reader
    first = manifest(await native.invoke(incoming, "materials", {}))
    assert deadlines == [60, 60, 60]
    assert first["read_budget"]["charged_seconds"] == 180
    assert first["read_budget"]["remaining_seconds"] == 0
    assert not first["complete"]
    assert first["unknowns"] == [
        {
            "asset_id": first["assets"][3]["id"],
            "source_id": "file-3",
            "location": "file",
            "reason": "GROUP_READ_BUDGET_EXCEEDED",
        }
    ]
    again = manifest(await native.invoke(incoming, "materials", {}))
    assert again["read_budget"] == first["read_budget"] and len(deadlines) == 3
    assert not service.gateway.writes and not service.bridge.calls
    checked = json.loads(
        await native.invoke(
            incoming,
            "outcome",
                {"group_id": first["group_id"], "state": "completed"},
        )
    )
    assert checked["state"] == "awaiting_materials"


async def test_slot_wait_and_idle_time_are_excluded_and_completed_assets_survive_restart(
    native, service, tmp_path, monkeypatch
):
    incoming = await material_event(service, tmp_path, count=2)
    clock, calls = [0.0], []
    monkeypatch.setattr(native_read_budget, "monotonic", lambda: clock[0])

    class SlowSlot:
        async def __aenter__(self):
            clock[0] += 1000

        async def __aexit__(self, *args):
            pass

    service.material_limit = SlowSlot()

    async def reader(*args, **kwargs):
        calls.append(1)
        clock[0] += 5
        return decoded()

    service.blobs.read = reader
    first = manifest(await native.invoke(incoming, "materials", {}))
    assert first["complete"] and first["read_budget"]["charged_seconds"] == 10
    clock[0] += 10000  # model thinking, queueing and uploads do not spend decoder time
    await service.db.close()
    await service.db.initialize()
    # Simulate loss of the group manifest, while completed per-asset reads persist.
    async with service.db.transaction() as conn:
        await execute(
            conn, "DELETE FROM native_material_reads WHERE group_id=:g", {"g": first["group_id"]}
        )
    rebuilt = manifest(await NativeTools(service).invoke(incoming, "materials", {}))
    assert rebuilt["complete"] and rebuilt["read_budget"]["charged_seconds"] == 10
    assert len(calls) == 2 and not service.bridge.calls


async def test_cancellation_settles_elapsed_time_and_retry_has_only_remaining_budget(
    native, service, tmp_path, monkeypatch
):
    incoming = await material_event(service, tmp_path)
    clock, entered = [0.0], asyncio.Event()
    monkeypatch.setattr(native_read_budget, "monotonic", lambda: clock[0])

    async def cancelled_reader(*args, **kwargs):
        clock[0] += 10
        entered.set()
        await asyncio.Future()

    service.blobs.read = cancelled_reader
    task = asyncio.create_task(native.invoke(incoming, "materials", {}))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    budget = (await service.db.read("SELECT * FROM native_read_budgets"))[0]
    assert budget["charged_seconds"] == 10
    assert not (await service.db.read("SELECT * FROM native_asset_reads"))[0]["reservation"]

    async def completed_reader(*args, **kwargs):
        clock[0] += 7
        return decoded()

    service.blobs.read = completed_reader
    retried = manifest(await NativeTools(service).invoke(incoming, "materials", {}))
    assert retried["complete"] and retried["read_budget"]["charged_seconds"] == 17
    assert retried["read_budget"]["remaining_seconds"] == 163


async def test_crash_reservation_is_not_refunded_and_cannot_be_reset_by_config_or_restart(
    native, service, tmp_path, monkeypatch
):
    incoming = await material_event(service, tmp_path)
    clock = [0.0]
    monkeypatch.setattr(native_read_budget, "monotonic", lambda: clock[0])
    # Admit without decoding, then persist the exact state left by a killed process.
    identity, settings = await native.authorize(incoming)
    _, group = await native.admit(incoming, identity, settings)
    asset = (await service.db.read("SELECT * FROM assets WHERE group_id=:g", {"g": group}))[0]
    async with service.db.transaction() as conn:
        await execute(conn, "INSERT INTO native_read_budgets VALUES (:g,180,60)", {"g": group})
        await execute(
            conn,
            "INSERT INTO native_asset_reads VALUES (:a,:g,:h,'killed',60,NULL)",
            {"a": asset["id"], "g": group, "h": asset["hash"]},
        )
        settings.time_budgets.group_read_seconds = 600
        await execute(
            conn,
            "UPDATE settings SET payload=:p WHERE id='main'",
            {"p": settings.model_dump_json()},
        )
    await service.db.close()
    await service.db.initialize()

    async def reader(*args, **kwargs):
        clock[0] += 20
        return decoded()

    service.blobs.read = reader
    result = manifest(await NativeTools(service).invoke(incoming, "materials", {}))
    assert result["read_budget"]["limit_seconds"] == 180
    assert result["read_budget"]["charged_seconds"] == 80
    assert result["read_budget"]["remaining_seconds"] == 100


async def test_real_wall_timeout_cancels_reader_and_reports_group_omission(
    native, service, tmp_path
):
    incoming = await material_event(service, tmp_path, count=2)
    settings, _ = await service.db.settings()
    settings.time_budgets.group_read_seconds = 1
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "UPDATE settings SET payload=:p WHERE id='main'",
            {"p": settings.model_dump_json()},
        )
    ended, deadlines = asyncio.Event(), []

    async def stuck_reader(*args, deadline_seconds, **kwargs):
        deadlines.append(deadline_seconds)
        try:
            await asyncio.Future()
        finally:
            ended.set()

    service.blobs.read = stuck_reader
    result = manifest(await native.invoke(incoming, "materials", {}))
    assert ended.is_set() and deadlines == [1]
    assert result["read_budget"]["charged_seconds"] == 1
    assert not result["complete"]
    assert {u["source_id"] for u in result["unknowns"]} == {"file-0", "file-1"}
    assert sum(u["reason"] == "GROUP_READ_BUDGET_EXCEEDED" for u in result["unknowns"]) == 2
    assert not service.bridge.calls and not service.gateway.writes


async def test_concurrent_material_calls_decode_once(native, service, tmp_path):
    incoming = await material_event(service, tmp_path)
    calls = []

    async def reader(*args, **kwargs):
        calls.append(1)
        await asyncio.sleep(0.02)
        return decoded()

    service.blobs.read = reader
    results = await asyncio.gather(*(native.invoke(incoming, "materials", {}) for _ in range(3)))
    assert all(manifest(result)["complete"] for result in results)
    assert len(calls) == 1


async def test_last_file_deadline_is_clamped_to_remaining_budget(
    native, service, tmp_path, monkeypatch
):
    incoming = await material_event(service, tmp_path)
    identity, settings = await native.authorize(incoming)
    _, group = await native.admit(incoming, identity, settings)
    async with service.db.transaction() as conn:
        await execute(conn, "INSERT INTO native_read_budgets VALUES (:g,180,175)", {"g": group})
    clock, deadlines = [0.0], []
    monkeypatch.setattr(native_read_budget, "monotonic", lambda: clock[0])

    async def reader(*args, deadline_seconds, **kwargs):
        deadlines.append(deadline_seconds)
        clock[0] += 3
        return decoded()

    service.blobs.read = reader
    result = manifest(await native.invoke(incoming, "materials", {}))
    assert deadlines == [5] and result["complete"]
    assert result["read_budget"]["charged_seconds"] == 178


async def test_offline_backup_restore_keeps_budget_and_purge_removes_cached_body(
    native, service, tmp_path, monkeypatch
):
    incoming = await material_event(service, tmp_path)
    clock = [0.0]
    monkeypatch.setattr(native_read_budget, "monotonic", lambda: clock[0])

    async def reader(*args, **kwargs):
        clock[0] += 12
        return decoded()

    service.blobs.read = reader
    result = manifest(await native.invoke(incoming, "materials", {}))
    await service.db.close()
    snapshot = tmp_path.parent / (tmp_path.name + "-snapshot")
    destination = tmp_path.parent / (tmp_path.name + "-restored")
    await asyncio.to_thread(backup, service.root, snapshot)
    await asyncio.to_thread(restore, snapshot, destination)
    from notido.db import Database

    restored = Database(destination)
    try:
        await restored.initialize()
        assert (await restored.read("SELECT charged_seconds FROM native_read_budgets"))[0][
            "charged_seconds"
        ] == 12
        assert (
            "实际材料正文"
            in (await restored.read("SELECT payload FROM native_asset_reads"))[0]["payload"]
        )
        assert not await restored.read("PRAGMA foreign_key_check")
    finally:
        await restored.close()
    async with service.db.transaction() as conn:
        await service.retention.purge(conn, result["group_id"], 0, False)
    assert not await service.db.read("SELECT * FROM native_asset_reads")
    assert (await service.db.read("SELECT charged_seconds FROM native_read_budgets"))[0][
        "charged_seconds"
    ] == 12
