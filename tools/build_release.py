"""Build a deterministic, source-only AstrBot plugin ZIP without runtime data.

No install, account operation, model invocation or publication is performed.
The allowlist matches the release files kept by .gitattributes.
"""

import argparse
import ast
import hashlib
import json
import re
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = "astrbot_plugin_notido"
MAX_ZIP_BYTES = 16_000_000
FILES = (
    "main.py",
    "metadata.yaml",
    "logo.png",
    "_conf_schema.json",
    "requirements.txt",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "Dockerfile",
    "compose.yaml",
    ".dockerignore",
    "tools/attachment-cli.mjs",
    "tools/task-extension.mjs",
    "tools/backup.py",
    "tools/diagnostics.py",
    "tools/export_schema.py",
    "tools/build_release.py",
)
FOLDERS = {
    "notido": {".py", ".sql"},
    "pages": {".html", ".js", ".css", ".png", ".svg"},
    "schemas": {".json"},
    "docs": {".md", ".json"},
    "deploy": {".py", ".sh"},
    "assets": {".svg", ".png"},
}


def release_files(root):
    paths = [root / relative for relative in FILES]
    for directory, suffixes in FOLDERS.items():
        paths.extend(
            p
            for p in (root / directory).rglob("*")
            if p.suffix in suffixes and "__pycache__" not in p.parts
        )
    for path in paths:
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)() or not path.is_file():
            raise ValueError(f"Missing or linked release file: {path.relative_to(root)}")
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Release file escapes repository")
        for parent in path.parents:
            if parent == root:
                break
            if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
                raise ValueError("Linked directory in release path")
    return sorted(set(paths), key=lambda p: p.relative_to(root).as_posix())


def build(root, destination, tag=None):
    version = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version):
        raise ValueError("Project version must be SemVer")
    if tag is not None and tag != f"v{version}":
        raise ValueError("Release tag differs from the project version")
    metadata = (root / "metadata.yaml").read_text("utf-8")
    if not re.search(rf"^name: {PLUGIN}$", metadata, re.M) or not re.search(
        rf"^version: {re.escape(version)}$", metadata, re.M
    ):
        raise ValueError("metadata.yaml name/version differs from the release identity")
    registrations = [
        decorator
        for node in ast.parse((root / "main.py").read_text("utf-8")).body
        if isinstance(node, ast.ClassDef)
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == "register"
    ]
    if len(registrations) != 1 or len(registrations[0].args) < 4:
        raise ValueError("Expected a single plugin registration in main.py")
    registration = registrations[0]
    if (ast.literal_eval(registration.args[0]), ast.literal_eval(registration.args[3])) != (
        PLUGIN, version
    ):
        raise ValueError("main.py registration differs from the release identity")
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"{PLUGIN}-{version}.zip"
    paths = release_files(root)
    # Keep the plugin root at the top level; AstrBot also handles repository ZIPs.
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for path in paths:
            info = zipfile.ZipInfo(
                f"{PLUGIN}/{path.relative_to(root).as_posix()}", date_time=(2026, 1, 1, 0, 0, 0)
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if path.suffix == ".sh" else 0o100644) << 16
            output.writestr(info, path.read_bytes())
    if archive.stat().st_size > MAX_ZIP_BYTES:
        raise ValueError("Plugin ZIP exceeds the AstrBot market limit of 16 MB")
    with zipfile.ZipFile(archive) as check:
        if check.testzip() is not None:
            raise ValueError("Plugin ZIP CRC check failed")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    result = {
        "plugin": PLUGIN,
        "version": version,
        "archive": str(archive),
        "files": len(paths),
        "bytes": archive.stat().st_size,
        "sha256": digest,
        "published": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--tag", help="Require an exact v<project version> release tag")
    args = parser.parse_args()
    build(ROOT, args.output, args.tag)
