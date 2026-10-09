import json
import zipfile

from conftest import create_plan, parsed

from notido.db import execute
from tools.diagnostics import create


async def test_diagnostic_never_packages_credentials_identity_source_or_original(service, tmp_path):
    service.bridge.plan = create_plan
    await parsed(service, text="记一下 2027年12月20日提交报告 PRIVATE_NOTICE_7g93")
    async with service.db.transaction() as conn:
        await execute(
            conn,
            "UPDATE jobs SET error='unsafe PRIVATE_NOTICE_7g93 token=SECRET_TOKEN_9a2' WHERE kind='execute_operation'",
        )
        await execute(
            conn,
            "UPDATE users SET identity=:p",
            {"p": json.dumps({"college": "PRIVATE_IDENTITY_4a8"})},
        )
    credential = service.root / "cli-home/.config/dida-cli/config.json"
    credential.parent.mkdir(parents=True)
    credential.write_text('{"access_token":"SECRET_TOKEN_9a2"}')
    (service.root / "staging" / "PRIVATE_ORIGINAL_0b7.txt").write_bytes(
        b"PRIVATE_ORIGINAL_BYTES_9z8"
    )
    output = tmp_path / "diagnostic.zip"
    assert create(service.root, output)["redacted"]
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ["diagnostic.json"]
        content = archive.read("diagnostic.json").decode()
    for private in (
        "PRIVATE_NOTICE",
        "PRIVATE_IDENTITY",
        "PRIVATE_ORIGINAL",
        "SECRET_TOKEN",
        "actor",
        "origin",
        "envelope",
        "access_token",
    ):
        assert private not in content
    report = json.loads(content)
    assert report["counts"]["operations"] == [{"kind": "create", "state": "validated", "count": 1}]
    assert report["safe_job_errors"] == [{"code": "REDACTED_ERROR", "count": 1}]
