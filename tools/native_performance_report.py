"""Publish only aggregate timings and synthetic case metadata from private journals."""

import argparse
import json
from pathlib import Path

from tools.native_performance_smoke import p95

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs/acceptance/native-performance.md"


def tool_seconds(timing, name):
    pending, total = {}, 0.0
    for event in timing["tools"]:
        payload = event["payload"]
        if event["kind"] == "tool_call" and payload.get("name") == name:
            pending[payload["id"]] = event["seconds"]
        elif event["kind"] == "tool_call_result" and payload.get("id") in pending:
            total += event["seconds"] - pending.pop(payload["id"])
    if pending:
        raise RuntimeError("missing tool completion; do not publish partial timings")
    return total


def main(batch="v3"):
    journal = ROOT / f"runtime-data/native-performance-{batch}-results.json"
    report = ROOT / f"docs/acceptance/native-performance-{batch}.md"
    state = json.loads(journal.read_text(encoding="utf-8"))
    cases = state["cases"]
    if len(cases) != 40 or any(r["phase"] != "verified" for r in cases.values()):
        raise RuntimeError("all 30 text and 10 file correctness checks must finish first")
    summary, machine = state["summary"], state["machine"]
    source_hash = state.get("main_sha256")
    runtime_hash = state.get("runtime_code_sha256")
    if not source_hash or not runtime_hash:
        raise RuntimeError(
            "journal has no captured runtime hashes; cannot attribute historical timing to current files"
        )
    files = [r for r in cases.values() if r["kind"] != "text"]
    materials = [tool_seconds(r["timing"], "notido_materials") for r in files]
    attachments = [tool_seconds(r["timing"], "notido_attach") for r in files]
    uploads = [r["astrbot_file_upload_seconds"] for r in files]
    corrections = [key for key, r in cases.items() if r.get("self_correction_operation_ids")]
    lines = [
        f"# 原生会话性能验收（{state.get('date', '2026-10-08')}）",
        "",
        "PRD v1.7，插件 0.1.0；这是正常外部依赖下的合成样本性能证据，不能代替人工标注准确率或故障验收。",
        "",
        "## 环境与计量",
        "",
        f"- 主机：{machine['host_os']}；{machine['host_processor']}；{machine['cpu_count']} 个逻辑 CPU。AstrBot 运行于本机 Linux Docker。",
        f"- AstrBot {machine['astrbot']}；任务 CLI {machine['task_cli']}；Node {machine['node']}；{machine['provider']} / {machine['model']}；时区 {machine['timezone']}。",
        f"- 本批 main.py SHA-256：`{source_hash}`。",
        f"- 本批安装代码 SHA-256：`{runtime_hash}`；发送首条样本前采集，恢复时要求一致。",
        "- 顺序运行独立授权会话，使用专用清单；没有并行模型请求。文字为指定时刻的明确创建；文件为清晰截图、单页扫描 PDF、DOCX 嵌图（原图 1800×800），要求仅由材料取得，并挂原件。",
        "- 端到端时间从 POST /api/chat/send 到 SSE 结束，包括框架、模型、工具循环、任务写入和原生附件核验。原件预上传 AstrBot 的时间独立计量，不含在端到端时间内。",
        "- 工具耗时从框架 tool_call 到同 ID 的 tool_call_result；材料工具含读取和图像交付，不等同于模型视觉理解时长；附件工具含原件上传、登记与下载 hash 核验。",
        "- p95 使用 nearest rank（向上取整）；文字 30 次取第 29 个值，文件 10 次取最大值。",
        "",
        "## 结果",
        "",
        "| 指标 | 样本数 | p95 秒 | 目标 / 结论 |",
        "| --- | ---: | ---: | --- |",
        f"| 文字端到端 | 30 | {summary['text_p95_seconds']:.3f} | ≤20，{'通过' if summary['text_target_pass'] else '未通过'} |",
        f"| 短图文端到端 | 10 | {summary['file_p95_seconds']:.3f} | ≤120，{'通过' if summary['file_target_pass'] else '未通过'} |",
        f"| AstrBot 原件预上传 | 10 | {p95(uploads):.3f} | 独立计量 |",
        f"| 材料工具累计 | 10 | {p95(materials):.3f} | 独立计量 |",
        f"| 附件工具累计 | 10 | {p95(attachments):.3f} | 独立计量 |",
        f"| 原生接收事件 | 40 | {summary['framework_message_saved_p95_seconds']:.3f} | 服务器接收计时；不等同屏幕渲染或任务完成 |",
        "",
        "40/40 的任务日期、时刻、非全天标志与唯一创建已核验；10/10 文件要求和原件下载 hash 已核验。每项任务创建和附件上传均只尝试一次。",
        "",
        (
            "同一请求内的同目标自修正："
            + "、".join(corrections)
            + "。原始创建字段与修改操作保留；计时包括全部工具循环，未重发请求或替换时长。采集器按实际工具返回的操作ID核查，不仅按标题前缀查找。"
            if corrections
            else "本批未出现额外修改操作。"
        ),
        "",
        "2026-10-08 用户确认 3 秒轻量反馈复用 AstrBot 原生接收／处理中提示。网页发送即建立加载占位；上表保存消息事件是服务器接收证据，不能据此声称测量了浏览器绘制延迟。见 [反馈边界核对](native-feedback.md)。",
        "",
        f"本批 {batch.upper()} 的 40 个会话独立采集；旧批次的结果保留在原报告中，没有拼入本批。",
        "",
        "## 逐样本记录",
        "",
        "| 编号 | 类型 | 原件字节 | 端到端秒 | 材料工具秒 | 附件工具秒 | 核验 |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for case_id, record in sorted(cases.items()):
        timing = record["timing"]
        lines.append(
            f"| {case_id} | {record['kind']} | {record.get('bytes', 0)} | "
            f"{timing['end_to_end_seconds']:.3f} | "
            f"{tool_seconds(timing, 'notido_materials'):.3f} | "
            f"{tool_seconds(timing, 'notido_attach'):.3f} | pass |"
        )
    lines.extend(
        [
            "",
            "可复现采集器：tools/native_performance_smoke.py；脱敏报告生成器：tools/native_performance_report.py。完整工具参数、原件、任务/会话标识及逐事件日志仅保存在被 Git/Docker 排除的 runtime-data，未进入本报告。采集器保存发送前状态，恢复时不重发已发送请求。",
            "",
        ]
    )
    report.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", choices=("v3", "v4", "v5"), default="v3")
    main(parser.parse_args().batch)
