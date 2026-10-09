"""Bounded overlapping text blocks, retaining offsets in the original evidence."""

from .errors import NotiDoError


def material_blocks(segments, budget):
    spans, position = [], 0
    unknowns = []
    for segment in segments:
        if segment["state"] != "read":
            unknowns.append(
                {k: segment[k] for k in ("source_id", "location", "text", "state", "reason")}
            )
            continue
        text = segment["text"]
        if not text:
            continue
        spans.append((position, position + len(text), segment))
        position += len(text) + 1  # A separator is not part of any evidence quote.
    length = max(0, position - 1)
    width, overlap = budget.block_characters, budget.block_overlap
    starts = []
    offset = 0
    while offset < length:
        starts.append(offset)
        if offset + width >= length:
            break
        offset += width - overlap
    if len(starts) > budget.blocks:
        first_unread = starts[budget.blocks] + overlap
        unread = [
            {
                "source_id": s["source_id"],
                "location": s["location"],
                "start": max(0, first_unread - a),
                "end": b - a,
            }
            for a, b, s in spans
            if b > first_unread
        ]
        raise NotiDoError(
            "MODEL_INPUT_LIMIT",
            f"材料需要 {len(starts)} 个文本块，超过配置的 {budget.blocks} 块；未执行。请分组或调整预算。",
            details={"unread_ranges": unread},
        )
    materials = []
    for block, start in enumerate(starts, 1):
        end = min(start + width, length)
        for a, b, segment in spans:
            left, right = max(start, a), min(end, b)
            if right <= left:
                continue
            materials.append(
                {
                    "source_id": segment["source_id"],
                    "location": segment["location"],
                    "text": segment["text"][left - a : right - a],
                    "state": "read",
                    "reason": None,
                    "block": block,
                    "start": left - a,
                    "end": right - a,
                }
            )
    return materials + unknowns, {
        "complete": True,
        "blocks": len(starts),
        "characters": length,
        "block_characters": width,
        "overlap": overlap,
    }
