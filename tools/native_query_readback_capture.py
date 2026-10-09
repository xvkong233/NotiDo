"""Capture immutable ledger metadata for explicit current-query notice readbacks.

This reads only; it never turns a same-title query into operation verification.
Historical batches without the native readback contract are left untouched.
"""

import argparse
import hashlib
import json

from tools import native_corpus_smoke as collector
from tools.container_smoke import PREFIX, ROOT, request, unwrap, wait_ready


def main(batch):
    journal = ROOT / f"runtime-data/native-corpus-{batch}-results.json"
    state = json.loads(journal.read_text("utf-8"))
    if len(state["cases"]) != 50 or any(c["phase"] != "collected" for c in state["cases"].values()):
        raise RuntimeError("complete batch required before separate read-only capture")
    token = wait_ready()
    if any(state.get(k) != v for k, v in collector.runtime_version().items()):
        raise RuntimeError("the exact batch implementation must still be installed")
    for case in state["cases"].values():
        slot = case["evaluation"]
        output = slot["output"]
        output_hash = hashlib.sha256(
            json.dumps(output, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        notices = {
            v["notice_ref"]
            for name, v in collector.tool_results(output)
            if name == "notido_materials" and v.get("notice_ref")
        }
        captures = []
        for name, value in collector.tool_results(output):
            if name != "notido_query":
                continue
            returned = {task["id"]: task for task in value.get("tasks", [])}
            for reference in value.get("notice_readbacks", []):
                if (
                    reference.get("verified") is not True
                    or reference.get("task_id") not in returned
                ):
                    raise RuntimeError("invalid current query receipt")
                row = unwrap(
                    request(PREFIX + "operations/" + reference["operation_id"], token=token)[1]
                )
                plan = row["plan"]
                result = json.loads(row["result"])
                task = returned[reference["task_id"]]
                if (
                    row["state"] != "succeeded"
                    or row["kind"] not in ("create", "update")
                    or result.get("verification", {}).get("verified") is not True
                    or row["remote_id"] != task["id"]
                    or plan.get("project_id") != task["projectId"]
                    or plan.get("notice_id") not in notices
                ):
                    raise RuntimeError("readback lacks verified exact notice operation")
                captures.append(
                    {
                        "source_output_sha256": output_hash,
                        "operation": row,
                        "current_actual_fields": task,
                    }
                )
        slot["verified_notice_query_readbacks"] = captures
    collector.JOURNAL = journal
    collector.save(state)
    print(
        json.dumps(
            {
                "read_only": True,
                "batch": batch,
                "captured": sum(
                    len(c["evaluation"]["verified_notice_query_readbacks"])
                    for c in state["cases"].values()
                ),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", choices=["v9", "v10", "v11", "v12", "v13", "v14"], default="v9")
    main(parser.parse_args().batch)
