import pytest
from acceptance_helpers import recovered_output


def history(message="通知", finished=True):
    call = {"id": "c", "name": "notido_create", "args": {}, "ts": 1, "result": "actual"}
    if finished:
        call["finished_ts"] = 2
    return [
        {"id": 10, "content": {"type": "user", "message": [{"type": "plain", "text": message}]}},
        {
            "id": 11,
            "content": {
                "type": "bot",
                "message": [
                    {"type": "tool_call", "tool_calls": [call]},
                    {"type": "plain", "text": "实际回执"},
                ],
            },
        },
    ]


def test_recovery_requires_same_message_and_finished_tool_receipt():
    out = recovered_output(history(), "通知")
    assert out["final_text"] == "实际回执" and out["end_to_end_seconds"] is None
    assert out["tools"][1]["payload"]["result"] == "actual"
    with pytest.raises(RuntimeError):
        recovered_output(history("其他消息"), "通知")
    with pytest.raises(RuntimeError):
        recovered_output(history(finished=False), "通知")
    with pytest.raises(RuntimeError):
        recovered_output(
            history() + [{"id": 12, "content": {"type": "user", "message": []}}], "通知"
        )
