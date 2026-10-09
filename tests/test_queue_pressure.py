import asyncio
import time

import pytest
from conftest import envelope

from notido.db import execute
from notido.errors import NotiDoError
from notido.keys import uid


async def test_concurrent_admission_keeps_reserved_slots_and_deduplicates_at_capacity(service):
    outcomes = await asyncio.gather(
        *(service.intake(envelope(f"burst-{index}")) for index in range(200)),
        return_exceptions=True,
    )
    admitted = [x for x in outcomes if isinstance(x, dict)]
    refused = [x for x in outcomes if isinstance(x, NotiDoError)]
    assert len(admitted) == 90 and len(refused) == 110
    assert all(x.code == "QUEUE_FULL" and "暂未接收" in x.message for x in refused)
    records = await service.db.read("SELECT * FROM message_records")
    assert len(records) == 90 and len(await service.db.read("SELECT * FROM jobs")) == 90
    assert not service.bridge.calls and not service.gateway.writes
    duplicate = await service.intake(envelope(records[0]["message_key"]))
    assert duplicate["duplicate"]
    async with service.db.transaction() as conn:
        await service.db.job(conn, "reconcile", "reserved-reconcile", {}, priority=2)
        await service.db.job(conn, "send_receipt", "reserved-receipt", {}, priority=1)
    assert len(await service.db.read("SELECT * FROM jobs")) == 92
    async with service.db.transaction() as conn:
        for index in range(8):
            await service.db.job(conn, "reconcile", f"remaining-reserve-{index}", {}, priority=2)
        with pytest.raises(NotiDoError) as error:
            await service.db.job(conn, "reconcile", "over-capacity", {}, priority=2)
        assert error.value.code == "QUEUE_FULL"
    assert len(await service.db.read("SELECT * FROM jobs")) == 100


async def test_aged_normal_job_gets_a_turn_amid_priority_burst_and_model_slots_stay_bounded(
    service,
):
    old, now = uid(), time.time()
    async with service.db.transaction() as conn:
        for index in range(50):
            await execute(
                conn,
                "INSERT INTO jobs(id,kind,dedupe_key,payload,priority,state,available_at,created_at) VALUES (:id,'parse_group',:k,'{}',:p,'pending',0,:t)",
                {
                    "id": old if index == 0 else uid(),
                    "k": f"pressure-{index}",
                    "p": 5 if index == 0 else 1,
                    "t": now - 40 if index == 0 else now + index / 1000,
                },
            )
    release, saturated, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    started, active, peak, done = [], 0, 0, 0

    async def perform(job):
        nonlocal active, peak, done
        started.append(job["id"])
        async with service.model_limit:
            active += 1
            peak = max(peak, active)
            if active == 2:
                saturated.set()
            await release.wait()
            active -= 1
        async with service.db.transaction() as conn:
            await execute(conn, "UPDATE jobs SET state='done' WHERE id=:id", {"id": job["id"]})
        done += 1
        if done == 50:
            finished.set()

    service.perform = perform
    worker = asyncio.create_task(service.run())
    try:
        await asyncio.wait_for(saturated.wait(), 3)
        assert started[0] == old and len(started) <= 6
        release.set()
        await asyncio.wait_for(finished.wait(), 8)
        assert peak == 2 and len(set(started)) == 50
        jobs = await service.db.read("SELECT state,attempt FROM jobs")
        assert all(x["state"] == "done" and x["attempt"] == 1 for x in jobs)
    finally:
        release.set()
        service.stopping = True
        service.wakeup.set()
        await asyncio.wait_for(worker, 3)
