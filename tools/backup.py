"""Offline SQLite snapshot and exact, verified DB/blob/credential restore."""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path, PurePosixPath

from filelock import FileLock

AUTH_FILES = (
    "cli-home/.config/dida-cli/config.json",
    "cli-home/.config/notido-attachments/config.json",
    "auth-fingerprint.key",
)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(65536):
            result.update(chunk)
    return result.hexdigest()


def checked_path(root, relative):
    if not isinstance(relative, str) or "\\" in relative or ":" in relative:
        raise ValueError("Invalid manifest path")
    parts = PurePosixPath(relative)
    if parts.is_absolute() or not parts.parts or any(x in (".", "..") for x in parts.parts):
        raise ValueError("Invalid manifest path")
    if parts.as_posix() != relative:
        raise ValueError("Noncanonical manifest path")
    path = root
    for part in parts.parts:
        path /= part
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("Backup paths must not contain links")
    if not path.resolve().is_relative_to(root):
        raise ValueError("Manifest path escapes root")
    return path


def allowed_file(relative):
    return (
        relative == "notido.db"
        or relative in AUTH_FILES
        or re.fullmatch(r"blobs/[0-9a-f]{64}", relative) is not None
    )


def copy_private(source, target):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(source, target)
    os.chmod(target, 0o600)


def blob_records(conn):
    records = conn.execute("SELECT hash,path,size FROM blobs").fetchall()
    for expected, relative, _ in records:
        if relative != f"blobs/{expected}" or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Invalid DB blob reference")
    has_tombstones = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='blob_tombstones'"
    ).fetchone()[0]
    if not has_tombstones:
        return records
    if conn.execute(
        "SELECT count(*) FROM assets a JOIN blob_tombstones t ON a.hash=t.hash WHERE a.state='ready'"
    ).fetchone()[0]:
        raise ValueError("Expired blob has a live reference")
    removed = {x[0] for x in conn.execute("SELECT hash FROM blob_tombstones")}
    return [x for x in records if x[0] not in removed]


def validate_database(root, files=None):
    database = checked_path(root, "notido.db")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        if (
            conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or conn.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise ValueError("Backup DB integrity failed")
        records = blob_records(conn)
    for expected, relative, size in records:
        if relative != f"blobs/{expected}" or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Invalid DB blob reference")
        path = checked_path(root, relative)
        if (
            (files is not None and relative not in files)
            or not path.is_file()
            or path.stat().st_size != size
            or digest(path) != expected
        ):
            raise ValueError("Blob consistency check failed")
    return records


def backup(root: Path, target: Path):
    root, target = root.resolve(), target.resolve()
    if target.exists() or target.is_relative_to(root) or root.is_relative_to(target):
        raise ValueError("Backup target must be a new directory outside the data root")
    if not checked_path(root, "notido.db").is_file():
        raise ValueError("Data root has no database")
    with FileLock(str(root / "worker.lock"), timeout=0):
        target.mkdir(parents=True, mode=0o700)
        with (
            closing(sqlite3.connect((root / "notido.db").as_uri() + "?mode=ro", uri=True)) as source,
            closing(sqlite3.connect(target / "notido.db")) as destination,
        ):
            source.backup(destination)
            records = blob_records(source)
        for expected, relative, size in records:
            if relative != f"blobs/{expected}" or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("Invalid DB blob reference")
            path = checked_path(root, relative)
            if not path.is_file() or path.stat().st_size != size or digest(path) != expected:
                raise ValueError("Blob consistency check failed")
            copy_private(path, target / relative)
        for relative in AUTH_FILES:
            path = checked_path(root, relative)
            if path.is_file():
                copy_private(path, target / relative)
        validate_database(target)
        files = sorted(x for x in target.rglob("*") if x.is_file())
        for path in files:
            os.chmod(path, 0o600)
        manifest = {
            "version": 1,
            "files": [
                {
                    "path": x.relative_to(target).as_posix(),
                    "sha256": digest(x),
                    "size": x.stat().st_size,
                }
                for x in files
            ],
        }
        manifest_path = target / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.chmod(manifest_path, 0o600)
        return {"files": len(files), "blobs": len(records)}


def restore(source: Path, target: Path):
    source, target = source.resolve(), target.resolve()
    if target.exists() or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("Restore target must be a new, separate directory")
    manifest = json.loads(checked_path(source, "manifest.json").read_text(encoding="utf-8"))
    if set(manifest) != {"version", "files"} or manifest["version"] != 1:
        raise ValueError("Unsupported backup manifest")
    entries = {}
    for entry in manifest["files"]:
        relative = entry["path"]
        if (
            set(entry) != {"path", "size", "sha256"}
            or relative in entries
            or not allowed_file(relative)
            or type(entry["size"]) is not int
            or entry["size"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
        ):
            raise ValueError("Invalid backup entry")
        path = checked_path(source, relative)
        if (
            not path.is_file()
            or path.stat().st_size != entry["size"]
            or digest(path) != entry["sha256"]
        ):
            raise ValueError("Backup manifest mismatch")
        entries[relative] = entry
    if "notido.db" not in entries:
        raise ValueError("Backup has no database")
    actual = {
        x.relative_to(source).as_posix() for x in source.rglob("*") if x.is_file() or x.is_symlink()
    }
    if actual != set(entries) | {"manifest.json"}:
        raise ValueError("Backup contains undeclared files")
    validate_database(source, entries)
    target.mkdir(parents=True, mode=0o700)
    for relative in entries:
        copy_private(checked_path(source, relative), target / relative)
    with closing(sqlite3.connect(target / "notido.db")) as conn, conn:
        conn.execute(
            "UPDATE operations SET state='outcome_unknown',revision=revision+1 WHERE state='executing'"
        )
        conn.execute("UPDATE operations SET paused=1,revision=revision+1 WHERE state='validated'")
    marker = target / "restore-review.required"
    marker.write_text(
        "Local restore cannot undo remote tasks. Review remote history before allowing new writes.\n",
        encoding="utf-8",
    )
    os.chmod(marker, 0o600)
    return {"restored": True, "remote_review_required": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["backup", "restore"])
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    print(json.dumps((backup if args.mode == "backup" else restore)(args.source, args.target)))
