"""Strict native recurrence parameters, translated to the pinned CLI's RRULE fields."""

import calendar
import re
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from .errors import NotiDoError
from .models import StrictModel


class Recurrence(StrictModel):
    frequency: Literal["daily", "weekly", "monthly", "yearly"]
    interval: int = Field(default=1, ge=1, le=365)
    weekdays: list[Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"]] = Field(
        default_factory=list, max_length=7
    )
    month_days: list[int] = Field(default_factory=list, max_length=31)
    months: list[int] = Field(default_factory=list, max_length=12)
    count: int | None = Field(default=None, ge=1, le=10000)
    end_date: str | None = None
    repeat_from: Literal["calendar", "due_date", "completion"] = "calendar"

    @model_validator(mode="after")
    def consistent(self):
        if self.count is not None and self.end_date is not None:
            raise ValueError("choose count or end_date")
        if self.month_days and self.frequency not in ("monthly", "yearly"):
            raise ValueError("month_days require monthly/yearly")
        if self.months and self.frequency != "yearly":
            raise ValueError("months require yearly")
        if self.weekdays and self.month_days:
            raise ValueError("choose weekdays or month_days")
        if any(type(day) is not int or day == 0 or not -31 <= day <= 31 for day in self.month_days):
            raise ValueError("invalid month day")
        if any(type(month) is not int or not 1 <= month <= 12 for month in self.months):
            raise ValueError("invalid month")
        if self.repeat_from == "completion" and (self.weekdays or self.month_days or self.months):
            raise ValueError("completion intervals cannot include calendar filters")
        return self

    def native_fields(self, first_date, timezone):
        if not first_date:
            raise NotiDoError(
                "RECURRENCE_START_REQUIRED", "周期任务需要明确首次日期，请先在 AstrBot 会话对齐。"
            )
        start = datetime.fromisoformat(first_date.replace("Z", "+00:00")).astimezone(
            ZoneInfo(timezone)
        )
        parts = [f"FREQ={self.frequency.upper()}", f"INTERVAL={self.interval}"]
        if self.weekdays:
            parts.append("BYDAY=" + ",".join(dict.fromkeys(self.weekdays)))
            if ("MO", "TU", "WE", "TH", "FR", "SA", "SU")[start.weekday()] not in self.weekdays:
                raise NotiDoError(
                    "RECURRENCE_START_MISMATCH", "首次日期不在所选重复星期内，请核对首次日期。"
                )
        if self.month_days:
            last = calendar.monthrange(start.year, start.month)[1]
            if start.day not in {day if day > 0 else last + day + 1 for day in self.month_days}:
                raise NotiDoError(
                    "RECURRENCE_START_MISMATCH", "首次日期不在所选重复月日内，请核对首次日期。"
                )
            parts.append("BYMONTHDAY=" + ",".join(str(day) for day in sorted(set(self.month_days))))
        if self.months:
            if start.month not in self.months:
                raise NotiDoError(
                    "RECURRENCE_START_MISMATCH", "首次日期不在所选重复月份内，请核对首次日期。"
                )
            parts.append("BYMONTH=" + ",".join(str(month) for month in sorted(set(self.months))))
        if self.count:
            parts.append(f"COUNT={self.count}")
        if self.end_date:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.end_date):
                raise NotiDoError("RECURRENCE_END_INVALID", "周期结束日期必须是明确的 YYYY-MM-DD。")
            end = datetime.fromisoformat(self.end_date + "T23:59:59").replace(
                tzinfo=ZoneInfo(timezone)
            )
            if end.date() < start.date():
                raise NotiDoError("RECURRENCE_END_INVALID", "周期结束日期早于首次日期。")
            from datetime import UTC

            parts.append("UNTIL=" + end.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ"))
        return {
            "repeatFlag": "RRULE:" + ";".join(parts),
            "repeatFrom": {"due_date": "0", "completion": "1", "calendar": "2"}[self.repeat_from],
        }


def canonical_rule(rule):
    if not isinstance(rule, str):
        return None
    fields = {}
    for part in rule.upper().removeprefix("RRULE:").split(";"):
        name, separator, value = part.partition("=")
        if not separator or name in fields:
            return None
        fields[name] = ",".join(sorted(value.split(",")))
    fields.setdefault("INTERVAL", "1")
    fields.setdefault("WKST", "MO")
    return fields
