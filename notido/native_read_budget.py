"""Durable decoder wall-time accounting, independent of the AstrBot model loop.

Called under NativeTools.lock and the exclusive service owner. Reserve before
starting a reader: a process crash charges that bounded reservation rather than
granting another full budget. Completed per-asset results avoid re-decoding when
pagination, late materials or a restart invalidate only the group manifest.
"""

import asyncio
import json
from pathlib import Path
from time import monotonic

from .db import execute, one
from .keys import canonical, uid


class NativeReadBudget:
    def __init__(self, service):
        self.service = service

    def available(self, payload):
        directory = (self.service.root / "derived").resolve()
        return all(
            (path := Path(visual["path"]).resolve()).is_relative_to(directory) and path.is_file()
            for visual in payload["visuals"]
        )

    async def summary(self, group):
        result = await self.service.db.read(
            "SELECT limit_seconds,charged_seconds FROM native_read_budgets WHERE group_id=:g",
            {"g": group},
        )
        if not result:
            return None
        return {
            **result[0],
            "remaining_seconds": max(0, result[0]["limit_seconds"] - result[0]["charged_seconds"]),
            "scope": "group_file_decode_and_render",
            "interrupted_read_policy": "charge_reserved_time",
        }

    async def read(self, group, asset, settings):
        saved = await self.service.db.read(
            "SELECT * FROM native_asset_reads WHERE asset_id=:a", {"a": asset["id"]}
        )
        if saved and saved[0]["payload"] and saved[0]["hash"] == asset["hash"]:
            payload = json.loads(saved[0]["payload"])
            if await asyncio.to_thread(self.available, payload):
                return payload
        # Waiting for the decoder slot, resolving the original and SQLite work
        # are outside the timed interval. File acquisition has already completed.
        async with self.service.material_limit:
            path = self.service.blobs.path(asset["path"])
            token = uid()
            async with self.service.db.transaction() as conn:
                await execute(
                    conn,
                    "INSERT OR IGNORE INTO native_read_budgets VALUES (:g,:limit,0)",
                    {"g": group, "limit": settings.time_budgets.group_read_seconds},
                )
                budget = await one(
                    conn, "SELECT * FROM native_read_budgets WHERE group_id=:g", {"g": group}
                )
                remaining = max(0, budget["limit_seconds"] - budget["charged_seconds"])
                if remaining <= 0:
                    return {
                        "segments": [],
                        "visuals": [],
                        "unknowns": ["GROUP_READ_BUDGET_EXCEEDED"],
                    }
                reservation = min(settings.time_budgets.read_seconds, remaining)
                await execute(
                    conn,
                    "UPDATE native_read_budgets SET charged_seconds=charged_seconds+:n WHERE group_id=:g",
                    {"g": group, "n": reservation},
                )
                await execute(
                    conn,
                    "INSERT INTO native_asset_reads VALUES (:a,:g,:hash,:token,:n,NULL) ON CONFLICT(asset_id) DO UPDATE SET hash=excluded.hash,reservation=excluded.reservation,reserved_seconds=excluded.reserved_seconds,payload=NULL",
                    {
                        "a": asset["id"],
                        "g": group,
                        "hash": asset["hash"],
                        "token": token,
                        "n": reservation,
                    },
                )
            result = None
            started = monotonic()
            try:
                try:
                    result = await asyncio.wait_for(
                        self.service.blobs.read(
                            path,
                            asset["name"],
                            budget=settings.materials,
                            deadline_seconds=reservation,
                        ),
                        reservation,
                    )
                except TimeoutError:
                    result = {"segments": [], "visuals": [], "unknowns": ["READ_TIMEOUT"]}
                if (
                    "READ_TIMEOUT" in result["unknowns"]
                    and reservation == remaining
                    and monotonic() - started >= reservation
                ):
                    result["unknowns"].append("GROUP_READ_BUDGET_EXCEEDED")
                return result
            finally:
                elapsed = min(reservation, max(0, monotonic() - started))
                # Shield the short durable settlement from tool cancellation. If
                # the process is killed, the precommitted reservation remains.
                await asyncio.shield(
                    self.settle(group, asset["id"], token, reservation, elapsed, result)
                )

    async def settle(self, group, asset_id, token, reserved, elapsed, result):
        async with self.service.db.transaction() as conn:
            current = await one(
                conn,
                "SELECT reservation FROM native_asset_reads WHERE asset_id=:a",
                {"a": asset_id},
            )
            if not current or current["reservation"] != token:
                return
            await execute(
                conn,
                "UPDATE native_read_budgets SET charged_seconds=MAX(0,charged_seconds-:refund) WHERE group_id=:g",
                {"g": group, "refund": reserved - elapsed},
            )
            await execute(
                conn,
                "UPDATE native_asset_reads SET reservation=NULL,reserved_seconds=0,payload=:p WHERE asset_id=:a",
                {"a": asset_id, "p": canonical(result) if result is not None else None},
            )
