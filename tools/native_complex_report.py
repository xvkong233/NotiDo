"""Publish hash-bound manual reviews and actual readbacks; never model-grade."""

import argparse
import hashlib
import json
from pathlib import Path

from tools.container_smoke import ROOT
from tools.native_complex_material_smoke import check


def main(batch):
    state = json.loads(
        (ROOT / f"runtime-data/native-complex-material-{batch}-results.json").read_text(
            encoding="utf-8"
        )
    )
    review_path = ROOT / f"docs/acceptance/complex-material-{batch}-semantic-review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if any(review.get(k) != state[k] for k in ("main_sha256", "runtime_code_sha256")):
        raise RuntimeError("review must belong to the captured runtime")
    lines = [
        f"# 原生复杂材料 {batch.upper()}（{state.get('date', '2026-10-08')}）",
        "",
        "使用已授权的中国版专用测试清单、AstrBot 4.28.2、deepseek/deepseek-flash / deepseek-flash。CPU读取后仅通过原生MCP图像与工具循环理解材料；无独立OCR、规划器或模型评分器。",
        "",
        f"安装main.py SHA-256：`{state['main_sha256']}`；安装代码SHA-256：`{state['runtime_code_sha256']}`。运行前采集，未归到后续源码版本。",
        "",
        "任务日期、要求、原件下载hash与关联目标单独核验；字段正确不能代替来源或回执正确。复用/更新不冒称新增，失败不覆盖、不自动清理测试任务。"
        + (
            "本批事项名称及材料明确标识为独立事项，不修改历史批次任务。"
            if batch in ("v3", "v4", "v5")
            else "V2原件增加批次标识，业务事项与V1相同，来源先后不能仅凭此标识确定。"
        ),
        "",
        "| 编号 | 原件字节 | 本次新增 / 更新 / 上传 | 字段核对 | 语义结论 | 复核说明 |",
        "| --- | ---: | --- | --- | --- | --- |",
    ]
    summary = {"collected": len(state["cases"]), "pass": 0, "fail": 0, "pending": 0}
    for key, r in state["cases"].items():
        if r["phase"] != "collected":
            raise RuntimeError("finish collection before publishing")
        output = r["evaluation"]["output"]
        semantic = review["cases"][key]
        digest = hashlib.sha256(
            json.dumps(output, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        if digest != semantic["evaluation_sha256"]:
            raise RuntimeError("actual output differs from reviewed evidence")
        rows = r["evaluation"]["operations"]
        fields = all(check(r).values())
        summary[semantic["status"]] += 1
        counts = " / ".join(
            str(sum(o["kind"] == k for o in rows)) for k in ("create", "update", "upload")
        )
        size = sum(Path(p).stat().st_size for p in r["files"])
        lines.append(
            f"| {key} | {size} | {counts} | {'pass' if fields else '未满足'} | {semantic['status']} | {semantic['note']} |"
        )
    lines += [
        "",
        f"逐条语义复核：通过{summary['pass']}，失败{summary['fail']}，待对齐{summary['pending']}。完整PRD仍未通过。",
        "",
        "原始调用、回执与操作ID仅保存在runtime-data；公开复核JSON保存每条输出hash。采集器发送前记账，网络中断不重发。",
        "",
    ]
    (ROOT / f"docs/acceptance/native-complex-material-{batch}.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    print(json.dumps(summary))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", choices=("v1", "v2", "v3", "v4", "v5"), required=True)
    main(parser.parse_args().batch)
