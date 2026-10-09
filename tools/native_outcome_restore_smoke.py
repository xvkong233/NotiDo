"""Verify pending native conclusions survive a real offline backup and restore.

Uses only authorized synthetic history in the dedicated test list, never changes remote tasks or
asks a model to reconstruct missing conclusions. Historical failures stay in
the original corpus evidence.
"""

import argparse
import asyncio
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from tools.live_restore_smoke import prepare, verify
from tools.live_same_name_smoke import save


def conclusions(root: Path):
    immutable = "&immutable=1" if (root / "manifest.json").is_file() else ""
    with closing(
        sqlite3.connect((root / "notido.db").as_uri() + "?mode=ro" + immutable, uri=True)
    ) as database:
        return database.execute(
            "SELECT group_id,account_ref,credential_generation,instance_id,actor_key,session_key,declaration,updated_at FROM native_group_outcomes ORDER BY group_id"
        ).fetchall()


async def main(batch="v6"):
    name = (
        "native-outcome-restore-results.json"
        if batch == "v6"
        else f"native-outcome-restore-{batch}-results.json"
    )
    container = "notido-native-v7" if batch in ("v7-r2", "v7-r3") else "notido-native-" + batch
    installed = None
    if batch in ("v7-r2", "v7-r3"):
        from tools.native_corpus_smoke import runtime_version

        installed = runtime_version()
    journal, state, target = prepare(name, container)
    if installed:
        previous = state.get("source_runtime_version")
        if previous and previous != installed:
            raise RuntimeError("restore batch cannot mix installed versions")
        state["source_runtime_version"] = installed
        save(journal, state)
    snapshot = Path(state["backup_root"])
    original = conclusions(snapshot)
    if not original or not any(
        row[6] and json.loads(row[6])["state"] != "completed" for row in original
    ):
        raise RuntimeError("this probe requires actual declared pending native conclusions")
    if conclusions(target) != original:
        raise RuntimeError("restoration changed native conclusions or their authorization scope")
    await verify(journal, state, target)
    # Startup may hold old groups; semantic declarations themselves must remain
    # intact, including real pending reasons and attachment obligations.
    if conclusions(target) != original:
        raise RuntimeError("startup/review rewrote historical native conclusions")
    state["checks"].append(
        {
            "check": "pending_native_conclusions_and_scope_survive_restore",
            "pass": True,
            "groups": len(original),
            "declared_pending": sum(
                bool(row[6]) and json.loads(row[6])["state"] != "completed" for row in original
            ),
            "snapshot_sha256": hashlib.sha256(
                json.dumps(original, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest(),
        }
    )
    save(journal, state)
    print(json.dumps(state["checks"], ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", choices=("v6", "v7", "v7-r2", "v7-r3"), default="v6")
    asyncio.run(main(parser.parse_args().batch))
