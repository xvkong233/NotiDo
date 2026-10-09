"""Resume only our synthetic fixture, using its authoritative generated deadline."""

import json
import sys
import uuid

from tools.container_smoke import PREFIX, ROOT, request, unwrap, wait_ready
from tools.container_vision_smoke import details
from tools.live_same_name_smoke import save


def main():
    kind = sys.argv[1]
    if kind not in ("png", "pdf", "docx"):
        raise ValueError("unknown fixture")
    journal = ROOT / "runtime-data" / f"container-vision-{kind}-results.json"
    state = json.loads(journal.read_text(encoding="utf-8"))
    token = wait_ready()
    group = state["group_id"]
    detail = details(token, group)
    if detail["operations"] or detail["group"]["state"] != "awaiting_clarification":
        raise RuntimeError("resume only paused fixture with no operations")
    question = json.loads(detail["session"]["question"] or "null")
    if not question or question["group_id"] != group:
        status, value = request(
            PREFIX + f"notices/{group}/continue",
            token=token,
            payload={
                "request_id": str(uuid.uuid4()),
                "expected_revision": detail["group"]["revision"],
            },
        )
        value = unwrap(value)
        if status != 200 or "error" in value:
            raise RuntimeError(
                f"fixture continuation rejected: {value.get('error', {}).get('code', status)}"
            )
        detail = details(token, group)
        question = json.loads(detail["session"]["question"])
    expected = state["expected"]
    answer = f"按原件明确的 {expected['date']} {expected['time']} 记录，保持原截止时间和提交要求。"
    key = "resume:" + question["question_ref"]
    state.setdefault("resume_requests", {}).setdefault(key, str(uuid.uuid4()))
    save(journal, state)
    status, value = request(
        PREFIX + f"notices/{group}/resolve",
        token=token,
        payload={
            "request_id": state["resume_requests"][key],
            "expected_revision": detail["group"]["revision"],
            "question_ref": question["question_ref"],
            "answer": answer,
        },
    )
    value = unwrap(value)
    if status != 200 or "error" in value:
        raise RuntimeError(
            f"fixture resolution rejected: {value.get('error', {}).get('code', status)}"
        )
    print(json.dumps({"check": "same_synthetic_fixture_resumed", "kind": kind, "queued": True}))


if __name__ == "__main__":
    main()
