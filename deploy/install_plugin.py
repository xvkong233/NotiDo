"""Install bundled code inside AstrBot's plugin root; Pages rejects escaping symlinks."""

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

SOURCE = Path("/opt/notido-plugin")
STORE = Path("/AstrBot/data/plugins")
TARGET = STORE / "astrbot_plugin_notido"
ENTRIES = (
    "main.py",
    "metadata.yaml",
    "logo.png",
    "_conf_schema.json",
    "requirements.txt",
    "LICENSE",
    "notido",
    "pages",
    "tools/task-extension.mjs",
    "tools/attachment-cli.mjs",
)


def manifest():
    paths = []
    for entry in ENTRIES:
        path = SOURCE / entry
        paths.extend(
            [path]
            if path.is_file()
            else sorted(
                x
                for x in path.rglob("*")
                if x.is_file() and "__pycache__" not in x.parts and x.suffix != ".exe"
            )
        )
    return {
        x.relative_to(SOURCE).as_posix(): hashlib.sha256(x.read_bytes()).hexdigest() for x in paths
    }


def main():
    STORE.mkdir(parents=True, exist_ok=True)
    bundled = manifest()
    marker = TARGET / ".notido-managed.json"
    if TARGET.exists() and not TARGET.is_symlink():
        if not marker.exists():
            # A manually installed plugin belongs to the maintainer; don't overwrite it.
            return
        previous = json.loads(marker.read_text())
        if bundled == previous:
            return
        for relative, expected in previous.items():
            path = TARGET / relative
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise RuntimeError(
                    "Managed plugin code was edited. Preserve it before updating the image."
                )
    stage = STORE / (".notido-install-" + uuid.uuid4().hex)
    stage.mkdir(mode=0o700)
    try:
        for relative in bundled:
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE / relative, destination)
        (stage / ".notido-managed.json").write_text(json.dumps(bundled, sort_keys=True))
        os.symlink("/opt/notido-cli/node_modules", stage / "node_modules", target_is_directory=True)
        if TARGET.is_symlink():
            if TARGET.resolve() != SOURCE.resolve():
                raise RuntimeError("Existing plugin symlink is not owned by NotiDo")
            TARGET.unlink()
        elif TARGET.exists():
            archive = Path("/AstrBot/data/plugin_data/astrbot_plugin_notido/code-history")
            archive.mkdir(parents=True, exist_ok=True)
            TARGET.rename(archive / uuid.uuid4().hex)
        stage.rename(TARGET)
    except BaseException:
        # The unactivated stage is retained for diagnostics; never modify an unrelated checkout.
        raise


if __name__ == "__main__":
    main()
