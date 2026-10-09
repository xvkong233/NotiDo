import hashlib
import zipfile
from pathlib import Path

import pytest

from tools.build_release import FILES, PLUGIN, build


@pytest.fixture
def release_root(tmp_path):
    root = tmp_path / "source"
    for name in FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("public fixture\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    (root / "metadata.yaml").write_text(f"name: {PLUGIN}\nversion: 0.1.0\n")
    (root / "main.py").write_text(
        f'@register("{PLUGIN}", "NotiDo contributors", "fixture", "0.1.0")\nclass Plugin: pass\n'
    )
    return root


def test_release_excludes_runtime_credentials_and_is_reproducible(release_root, tmp_path):
    for name in (".env", "runtime-data/credentials.json", "data/private.json", "tests/secret.py"):
        path = release_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("PRIVATE_TEST_SECRET")
    first = build(release_root, tmp_path / "first", "v0.1.0")
    second = build(release_root, tmp_path / "second", "v0.1.0")
    assert first["sha256"] == second["sha256"]
    with zipfile.ZipFile(first["archive"]) as archive:
        assert archive.testzip() is None
        assert all(name.startswith(f"{PLUGIN}/") for name in archive.namelist())
        assert all(b"PRIVATE_TEST_SECRET" not in archive.read(name) for name in archive.namelist())
    archive = Path(first["archive"])
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == first["sha256"]
    assert archive.with_suffix(".zip.sha256").read_text().split()[0] == first["sha256"]


def test_release_rejects_wrong_tag_before_writing(release_root, tmp_path):
    output = tmp_path / "dist"
    with pytest.raises(ValueError, match="tag differs"):
        build(release_root, output, "v0.2.0")
    assert not output.exists()


def test_release_rejects_metadata_mismatch(release_root, tmp_path):
    (release_root / "metadata.yaml").write_text(f"name: {PLUGIN}\nversion: 0.2.0\n")
    with pytest.raises(ValueError, match="metadata.yaml"):
        build(release_root, tmp_path / "dist", "v0.1.0")


def test_release_refuses_to_replace_existing_package(release_root, tmp_path):
    output = tmp_path / "dist"
    build(release_root, output, "v0.1.0")
    with pytest.raises(FileExistsError):
        build(release_root, output, "v0.1.0")


def test_release_rejects_registration_mismatch(release_root, tmp_path):
    main = release_root / "main.py"
    main.write_text(main.read_text().replace('"0.1.0"', '"0.2.0"'))
    with pytest.raises(ValueError, match="registration differs"):
        build(release_root, tmp_path / "dist", "v0.1.0")
