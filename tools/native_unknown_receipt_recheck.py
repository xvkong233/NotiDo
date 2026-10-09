"""Recheck existing authorized unknown fault cases with current native tools, no writes."""

import json

from tools import native_corpus_smoke as collector
from tools.container_smoke import ROOT, wait_ready

JOURNAL = ROOT / "runtime-data/native-unknown-receipt-recheck-results.json"


def main():
    token = wait_ready()
    version = collector.runtime_version()
    collector.JOURNAL = JOURNAL
    state = json.loads(JOURNAL.read_text("utf-8")) if JOURNAL.exists() else {**version, "cases": {}}
    if any(state.get(k) != v for k, v in version.items()):
        raise RuntimeError("don't mix versions in a receipt review")
    previous = json.loads(
        (ROOT / "runtime-data/native-shutdown-v7-r2-results.json").read_text("utf-8")
    )
    for mode, source in previous["cases"].items():
        record = state["cases"].setdefault(
            mode,
            {
                "id": mode,
                "session": source["session"],
                "original_operation": source["operation"]["id"],
                "title": source["title"],
            },
        )
        result = collector.send_once(
            token,
            state,
            record,
            "evaluation",
            f"之前“{record['title']}”的创建结果不确定。请查询完整标题并核查实际操作"
            f" {record['original_operation']}，说明当前事实和可行的后续处理。只做只读核查。",
        )
        if result["operations"]:
            raise RuntimeError("read-only receipt check caused a new write")
        collector.save(state)
        print(json.dumps({"case": mode, "writes": 0, "semantic_review_pending": True}), flush=True)


if __name__ == "__main__":
    main()
