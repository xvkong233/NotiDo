"""Native Provider checks for local conclusions, with no remote task writes.

This is a separate regression batch, never a replacement of the frozen V6
failures. The already approved deletion is not executed a second time.
"""

import json

from tools import native_corpus_smoke as corpus
from tools.container_smoke import PREFIX, ROOT, request, unwrap, wait_ready
from tools.native_performance_smoke import bind_session


def main():
    if corpus.CONTAINER != "notido-native-v6":
        raise RuntimeError("the authorized V6 runtime is required")
    corpus.JOURNAL = ROOT / "runtime-data/native-outcome-followup-results.json"
    installed = corpus.runtime_version()
    state = (
        json.loads(corpus.JOURNAL.read_text(encoding="utf-8"))
        if corpus.JOURNAL.exists()
        else {**installed, "cases": {}, "checks": []}
    )
    if any(state.get(key) != value for key, value in installed.items()):
        raise RuntimeError("do not mix installed versions in a regression batch")
    formal = json.loads(
        (ROOT / "runtime-data/native-corpus-v6-results.json").read_text(encoding="utf-8")
    )
    deletion = formal["cases"]["D10"]
    if deletion["phase"] != "collected" or len(deletion["confirmation"]["operations"]) != 1:
        raise RuntimeError("already approved and verified deletion evidence required")
    original = deletion["actual_preview"]["material_group_id"]
    confirmation = next(
        value["group_id"]
        for name, value in corpus.tool_results(deletion["confirmation"]["output"])
        if name == "notido_record_outcome"
    )
    token = wait_ready()
    cases = [
        (
            "original_delete_conclusion",
            deletion["session"],
            f"刚才获本人确认的V6测试任务已删除并有可靠exists=false回读。"
            f"只更新本地处理结论：原预览材料组{original}及确认材料组{confirmation}均已无未决事项，"
            "请调用notido_record_outcome更新这两组为completed。"
            "不要新建、修改、删除、完成任务或上传，也不要再执行删除。",
        ),
        (
            "direct_missing_time",
            None,
            "新建“交报告”，2027年12月20日，明确不是全天，但没告诉你时刻。"
            "默认清单NotiDo 验收。只问所缺条件，并用原生工具如实登记处理结论。",
        ),
        (
            "direct_unknown_project",
            None,
            "在不存在的“火星清单”新建“整理资料”。"
            "请按真实允许范围核对，缺少条件只问所缺条件，并用原生工具如实登记处理结论。",
        ),
    ]
    for case_id, existing_session, message in cases:
        record = state["cases"].setdefault(case_id, {"id": case_id})
        if "session" not in record:
            record["session"] = existing_session or bind_session(token)
            corpus.save(state)
        result = corpus.send_once(token, state, record, "evaluation", message)
        if result["operations"]:
            raise RuntimeError("local-conclusion regression unexpectedly wrote a remote task")
        conclusions = [
            value
            for name, value in corpus.tool_results(result["output"])
            if name == "notido_record_outcome"
        ]
        if case_id == "original_delete_conclusion":
            if not {original, confirmation} <= {
                value["group_id"] for value in conclusions if value["state"] == "completed"
            }:
                raise RuntimeError("native agent did not close both actual referenced groups")
            for group in (original, confirmation):
                current = unwrap(request(PREFIX + f"notices/{group}", token=token)[1])
                if current["group"]["state"] != "completed":
                    raise RuntimeError("declared completion did not persist")
        elif not conclusions or conclusions[-1]["state"] != "awaiting_clarification":
            raise RuntimeError("native direct clarification was not actually registered")
        record["phase"] = "verified"
        corpus.save(state)
        print(json.dumps({"check": case_id, "pass": True, "remote_writes": 0}), flush=True)


if __name__ == "__main__":
    main()
