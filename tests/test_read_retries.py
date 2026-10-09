import pytest

from notido.bridge import AstrBotBridge
from notido.cli import CLIResponse, DidaGateway
from notido.errors import NotiDoError


def test_bridge_does_not_own_ai_or_vision():
    assert not hasattr(AstrBotBridge, "call_provider")
    assert not hasattr(AstrBotBridge, "read_visual")


class Runner:
    def __init__(self, error):
        self.calls = []
        self.error = error

    async def call(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return CLIResponse(
            error=self.error, side_effect="unknown" if kwargs.get("write") else "none"
        )


async def test_gateway_read_only_retries_and_write_once():
    runner = Runner("CLI_TIMEOUT")
    gateway = DidaGateway(runner)
    with pytest.raises(NotiDoError) as error:
        await gateway.projects()
    assert error.value.code == "CLI_TIMEOUT" and len(runner.calls) == 3
    assert all(0 < x[1]["deadline_seconds"] <= 20 for x in runner.calls)
    await gateway.write("create", "p", {"title": "任务"})
    assert len(runner.calls) == 4 and runner.calls[-1][1] == {"write": True}


async def test_invalid_cli_output_is_not_retried():
    runner = Runner("CLI_OUTPUT_INVALID")
    with pytest.raises(NotiDoError):
        await DidaGateway(runner).projects()
    assert len(runner.calls) == 1
