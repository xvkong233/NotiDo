import re
import unicodedata

from .errors import NotiDoError
from .keys import key


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def verify_evidence(evidence, segments):
    matched = []
    for item in evidence:
        segment = next(
            (
                x
                for x in segments
                if x["source_id"] == item.source_id
                and x["location"] == item.location
                and x["state"] == "read"
                and normalize_text(item.quote) in normalize_text(x["text"])
            ),
            None,
        )
        if not segment or normalize_text(item.quote) not in normalize_text(segment["text"]):
            raise NotiDoError("EVIDENCE_INVALID", "行动依据未对应已读取的材料位置。")
        matched.append(segment)
    return matched


def resolve_project(name, projects, settings):
    allowed = [x for x in projects if x["id"] in settings.allowed_projects and not x.get("closed")]
    if name is None:
        matches = [x for x in allowed if x["id"] == settings.default_project]
    elif name in settings.project_aliases:
        matches = [x for x in allowed if x["id"] == settings.project_aliases[name]]
    else:
        matches = [x for x in allowed if x["name"] == name]
    if len(matches) != 1:
        raise NotiDoError("PROJECT_AMBIGUOUS", "清单不存在、重名或未获允许，请选择真实清单。")
    return matches[0]


def managed_notes(notice_id, requirements, evidence, normalized_date):
    content = "\n".join(
        [
            f"notice: {notice_id}",
            *requirements,
            f"时间原文：{normalized_date['raw_text'] or '无日期'}",
            f"日期解析锚点：{normalized_date.get('anchor_basis') or '无（未使用相对日期）'}",
            *[f"依据 {x.source_id} / {x.location}：{x.quote}" for x in evidence],
        ]
    )
    if len(content) > 9800:
        raise NotiDoError("NOTES_TOO_LONG", "提交要求超过备注上限，请精简并保留关键要求。")
    return f"<!-- NOTIDO:{notice_id}:START -->\n{content}\n<!-- NOTIDO:{notice_id}:END -->"


def managed_region(content, notice_id):
    start, end = f"<!-- NOTIDO:{notice_id}:START -->", f"<!-- NOTIDO:{notice_id}:END -->"
    if (
        content.count(start) != 1
        or content.count(end) != 1
        or content.index(start) >= content.index(end)
    ):
        raise NotiDoError("NOTES_CONFLICT", "机器人备注区缺失、重复或顺序错误，请先处理冲突。")
    return content[content.index(start) : content.index(end) + len(end)]


def replace_managed(current, previous, replacement, notice_id):
    region = managed_region(current, notice_id)
    first = current.index(region)
    last = first + len(region)
    if key(region) != key(previous):
        raise NotiDoError("NOTES_CONFLICT", "机器人备注区已被编辑，请确认保留或替换内容。")
    return current[:first] + replacement + current[last:]
