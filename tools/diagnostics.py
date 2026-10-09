"""Create a default-redacted diagnostic ZIP containing aggregate metadata only."""

import argparse
import json
import os
import re
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path

TABLES = {
    "operations": ("kind", "state"),
    "jobs": ("kind", "state"),
    "material_groups": ("mode", "state"),
    "assets": ("state",),
    "receipt_records": ("state",),
}


def create(root: Path, output: Path):
    root, output = root.resolve(), output.resolve()
    if output.exists() or output.suffix.lower() != ".zip":
        raise ValueError("Choose a new .zip output")
    report = {
        "diagnostic_version": 1,
        "plugin_version": "0.1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "redacted": True,
        "includes": [
            "aggregate_counts",
            "migration_versions",
            "maintenance_flags",
            "safe_error_codes",
        ],
        "maintenance": {
            "restore_review": (root / "restore-review.required").is_file(),
            "auth_commit_unknown": (root / "maintenance.required").is_file(),
        },
        "counts": {},
    }
    database = root / "notido.db"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as conn:
        conn.execute("BEGIN")
        report["migrations"] = [
            x[0] for x in conn.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]
        for table, columns in TABLES.items():
            fields = ",".join(columns)
            report["counts"][table] = [
                dict(zip((*columns, "count"), row, strict=True))
                for row in conn.execute(f"SELECT {fields},count(*) FROM {table} GROUP BY {fields}")
            ]
        report["safe_job_errors"] = [
            {
                "code": code
                if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code)
                else "REDACTED_ERROR",
                "count": count,
            }
            for code, count in conn.execute(
                "SELECT error,count(*) FROM jobs WHERE error IS NOT NULL GROUP BY error"
            )
        ]
        count, size = conn.execute("SELECT count(*),coalesce(sum(size),0) FROM blobs").fetchone()
        report["blob_totals"] = {"count": count, "bytes": size}
        conn.rollback()
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with output.open("xb") as file:
        os.chmod(output, 0o600)
        with zipfile.ZipFile(file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("diagnostic.json", json.dumps(report, ensure_ascii=False, indent=2))
    return {"created": True, "redacted": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("data_root", type=Path)
    parser.add_argument("output_zip", type=Path)
    args = parser.parse_args()
    print(json.dumps(create(args.data_root, args.output_zip)))
