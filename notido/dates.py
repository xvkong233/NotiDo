import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .errors import NotiDoError


def normalize(
    date_text: str | None,
    time_text: str | None,
    *,
    anchor: datetime | None,
    timezone: str,
    kind: str,
    evidence_id: str,
    time_kind="deadline",
    comparison_override=None,
) -> dict:
    raw = " ".join(x for x in (date_text, time_text) if x)
    if kind == "unresolved":
        raise NotiDoError("DATE_UNRESOLVED", "期限未确定，请补充确切日期。")
    if kind == "none":
        if raw:
            raise NotiDoError("DATE_CONTRADICTION", "日期描述与无日期标记不一致。")
        return {
            "kind": "none",
            "precision": "none",
            "local_date": None,
            "local_time": None,
            "timezone": timezone,
            "instant": None,
            "is_all_day": False,
            "comparison": "unspecified",
            "raw_text": "",
            "anchor_basis": None,
            "evidence_id": evidence_id,
            "time_kind": time_kind,
        }
    tz = ZoneInfo(timezone)
    if not date_text:
        raise NotiDoError("DATE_UNRESOLVED", "请补充日期。")
    text = date_text.strip()
    comparison = "before_or_at" if any(x in text for x in ("含当天", "不晚于")) else "at"
    if re.search(r"(?:日|号|\d)前", text) and comparison == "at":
        if comparison_override not in ("before", "before_or_at"):
            raise NotiDoError(
                "DATE_COMPARISON_AMBIGUOUS",
                "截止日当天是否仍可提交？请回答“当天可提交”或“当天不可提交”。",
            )
        comparison = comparison_override
    iso = re.search(r"(?<!\d)(\d{4})[-年/](\d{1,2})[-月/](\d{1,2})(?:日|号)?", text)
    if kind == "date_only" and re.search(r"\d{1,2}[:：]\d{2}", text):
        raise NotiDoError("DATE_CONTRADICTION", "日期原文含明确时刻，请提供 time_text，不能记为全天。")
    if iso:
        try:
            result_date = date(*map(int, iso.groups()))
        except ValueError as exc:
            raise NotiDoError("INVALID_DATE", "日期不存在。") from exc
    else:
        if anchor is None:
            raise NotiDoError("TIME_ANCHOR_UNKNOWN", "请补充原通知发布时间或绝对日期。")
        if anchor.tzinfo is None:
            raise NotiDoError("TIME_ANCHOR_UNKNOWN", "时间锚点缺少时区。")
        base = anchor.astimezone(tz).date()
        relative = next(
            (i for word, i in (("今天", 0), ("明天", 1), ("后天", 2)) if word in text), None
        )
        week = re.search(r"(下周|下星期|周|星期)([一二三四五六日天])", text)
        if relative is not None:
            result_date = base + timedelta(days=relative)
        elif week:
            day = "一二三四五六日".index(week[2].replace("天", "日"))
            offset = (
                7 - base.weekday() + day if week[1].startswith("下") else (day - base.weekday()) % 7
            )
            result_date = base + timedelta(days=offset)
        else:
            raise NotiDoError("DATE_UNRESOLVED", "请提供含年份的公历日期。")
    local_time = None
    instant = None
    if kind == "timed":
        time_pattern = (
            r"\s*(上午|下午|晚上|中午)?\s*(\d{1,2})(?:[:：](\d{2})|[点时](\d{1,2})?分?)?\s*"
        )
        raw_time = time_text or ""
        combined = re.fullmatch(
            r"\s*(\d{4})[-年/](\d{1,2})[-月/](\d{1,2})(?:日|号)?[ T]*" + time_pattern,
            raw_time,
        )
        if combined:
            try:
                supplied_date = date(*map(int, combined.groups()[:3]))
            except ValueError:
                raise NotiDoError("INVALID_DATE", "时刻原文所含日期不存在。") from None
            if supplied_date != result_date:
                raise NotiDoError("DATE_CONTRADICTION", "日期与时刻原文所含日期不一致，请核对。")
            period, hour, minute1, minute2 = combined.groups()[3:]
        else:
            match = re.fullmatch(time_pattern, raw_time)
            if not match:
                raise NotiDoError("TIME_UNRESOLVED", "请提供明确时刻。")
            period, hour, minute1, minute2 = match.groups()
        h, m = int(hour), int(minute1 or minute2 or 0)
        if period:
            if not 1 <= h <= 12:
                raise NotiDoError("INVALID_TIME", "上午/下午时刻须在 1–12 点。")
            if period in ("下午", "晚上", "中午") and h < 12:
                h += 12
            elif period == "上午" and h == 12:
                h = 0
        if h == 24 and m == 0 and not period:
            result_date += timedelta(days=1)
            h = 0
        try:
            value = time(h, m)
        except ValueError as exc:
            raise NotiDoError("INVALID_TIME", "时刻不存在。") from exc
        local_time = value.isoformat(timespec="minutes")
        instant = int(datetime.combine(result_date, value, tz).timestamp() * 1000)
    elif time_text:
        raise NotiDoError("DATE_CONTRADICTION", "全天任务不能同时包含时刻。")
    return {
        "kind": kind,
        "precision": "minute" if local_time else "day",
        "local_date": result_date.isoformat(),
        "local_time": local_time,
        "timezone": timezone,
        "instant": instant,
        "is_all_day": kind == "date_only",
        "comparison": comparison,
        "raw_text": raw,
        "anchor_basis": anchor.isoformat() if anchor and not iso else None,
        "evidence_id": evidence_id,
        "time_kind": time_kind,
    }


def overdue(value: dict, now: datetime) -> bool:
    if value["kind"] == "none":
        return False
    if value["is_all_day"]:
        return (
            date.fromisoformat(value["local_date"])
            < now.astimezone(ZoneInfo(value["timezone"])).date()
        )
    return value["instant"] < int(now.timestamp() * 1000)


def native_fields(value: dict) -> dict:
    if value["kind"] == "none":
        return {"dueDate": None, "isAllDay": False, "timeZone": value["timezone"]}
    local = f"{value['local_date']}T{value['local_time'] or '00:00'}:00"
    instant = datetime.fromisoformat(local).replace(tzinfo=ZoneInfo(value["timezone"]))
    return {
        "dueDate": instant.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "isAllDay": value["is_all_day"],
        "timeZone": value["timezone"],
    }
