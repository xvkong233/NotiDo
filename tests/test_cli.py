import sys

import pytest

from notido.cli import CLIResponse, CLIRunner, DidaGateway
from notido.errors import NotiDoError


@pytest.mark.parametrize(
    ("source", "error", "side_effect"),
    [
        ("print('not JSON')", "CLI_OUTPUT_INVALID", "unknown"),
        ("print('{}{}')", "CLI_OUTPUT_INVALID", "unknown"),
        ("import sys; print('{}'); sys.exit(1)", "CLI_EXIT_FAILURE", "unknown"),
        ("print('x'*20000)", "CLI_OUTPUT_LIMIT", "unknown"),
    ],
)
async def test_writes_do_not_infer_success_from_exit(tmp_path, source, error, side_effect):
    script = tmp_path / "cli.py"
    script.write_text(source)
    response = await CLIRunner(sys.executable, str(script), tmp_path, output_limit=10000).call(
        [], write=True
    )
    assert response.error == error and response.side_effect == side_effect


async def test_timeout_is_unknown_for_write_safe_for_read(tmp_path):
    script = tmp_path / "cli.py"
    script.write_text("import time; time.sleep(5)")
    runner = CLIRunner(sys.executable, str(script), tmp_path)
    assert (await runner.call([], write=True, deadline_seconds=0.1)).side_effect == "unknown"
    assert (await runner.call([], deadline_seconds=0.1)).side_effect == "none"


async def test_envelope_error_preserves_reliable_id(tmp_path):
    script = tmp_path / "cli.py"
    script.write_text(
        'import sys; print(\'{"contract_version":1,"ok":false,"side_effect":"unknown","remote_id":"saved-id","error":{"code":"REGISTER_TIMEOUT"}}\'); sys.exit(1)'
    )
    response = await CLIRunner(sys.executable, str(script), tmp_path).call(
        [], write=True, envelope=True
    )
    assert response.value["id"] == "saved-id" and response.side_effect == "unknown"


@pytest.mark.parametrize(
    "proof,exists,valid",
    [
        ("native_deleted_flag", False, True),
        ("native_active_flag", True, True),
        ("native_deleted_flag", True, False),
        ("active_list_absence", False, False),
    ],
)
async def test_deleted_tombstone_is_explicit_not_inferred_from_active_list(proof, exists, valid):
    class Runner:
        def __init__(self, value):
            self.value = value

        async def call(self, args, **kwargs):
            return CLIResponse(value=self.value)

    official = Runner({"id": "t", "projectId": "p", "exists": True})
    native = Runner({"id": "t", "projectId": "p", "exists": exists, "proof": proof})
    gateway = DidaGateway(
        official, task_extension=official, attachment_runner=native, attachment_verified=True
    )
    if valid:
        result = await gateway.probe_task("p", "t")
        assert result["exists"] is exists and result["proof"] == proof
    else:
        with pytest.raises(NotiDoError):
            await gateway.probe_task("p", "t")


async def test_deleted_probe_authorization_failure_never_proves_absence():
    class Runner:
        async def call(self, args, **kwargs):
            return CLIResponse(value={"id": "t", "projectId": "p", "exists": True})

    class Unauthorized:
        async def call(self, args, **kwargs):
            return CLIResponse(error="ATTACHMENT_AUTH_REQUIRED")

    official = Runner()
    gateway = DidaGateway(
        official,
        task_extension=official,
        attachment_runner=Unauthorized(),
        attachment_verified=True,
    )
    with pytest.raises(NotiDoError) as caught:
        await gateway.probe_task("p", "t")
    assert caught.value.code == "ATTACHMENT_AUTH_REQUIRED"
