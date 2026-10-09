from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .errors import NotiDoError
from .keys import key, uid


def task_date(task, timezone):
    raw = task.get("dueDate")
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if value.tzinfo is None:
            raise ValueError
        return value.astimezone(ZoneInfo(timezone))
    except (ValueError, AttributeError) as exc:
        raise NotiDoError(
            "TASK_DATE_INVALID", "远端任务日期不可解析，查询范围不完整。", status=503
        ) from exc


async def read_scope(gateway, settings, now, query):
    tasks, completeness = [], {}
    for project_id in settings.allowed_projects:
        try:
            current = await gateway.tasks(project_id)
            for task in current:
                if not isinstance(task.get("id"), str) or task.get("projectId") != project_id:
                    raise NotiDoError("CLI_SCHEMA_INVALID", "任务标识无效。")
                task_date(task, settings.timezone)
            tasks.extend(current)
            completeness[project_id] = True
        except NotiDoError:
            completeness[project_id] = False
    today = now.astimezone(ZoneInfo(settings.timezone)).date()
    monday = today - timedelta(days=today.weekday())
    result = []
    for task in tasks:
        if task.get("status", 0) != 0:
            continue
        value = task_date(task, settings.timezone)
        expired = value is not None and (
            value.date() < today if task.get("isAllDay") else value < now
        )
        scope = query.scope
        if scope == "undated" and value is not None or scope == "overdue" and not expired:
            continue
        if scope in ("today", "week", "seven_days"):
            begin, end = (
                (today, today)
                if scope == "today"
                else (monday, monday + timedelta(days=6))
                if scope == "week"
                else (today, today + timedelta(days=6))
            )
            if value is None or not begin <= value.date() <= end:
                continue
        if query.keyword and query.keyword not in task.get("title", ""):
            continue
        if query.task_id and task["id"] != query.task_id:
            continue
        result.append(
            {
                **task,
                "overdue": expired,
                "date_label": "无日期"
                if value is None
                else value.date().isoformat()
                if task.get("isAllDay")
                else value.strftime("%Y-%m-%d %H:%M"),
            }
        )

    def sort(task):
        value = task_date(task, settings.timezone)
        return (
            0 if task["overdue"] else 2 if value is None else 1,
            value.date().isoformat() if value else "",
            0 if task.get("isAllDay") else 1,
            value.timestamp() if value and not task.get("isAllDay") else 0,
            task["projectId"],
            task["id"],
        )

    result.sort(key=sort)
    complete = bool(completeness) and all(completeness.values())
    return {
        "tasks": result,
        "per_project_complete": completeness,
        "checked_at": now.isoformat(),
        "total_count": len(result) if complete else None,
        "complete": complete,
        "fingerprint": key(
            [
                (x["projectId"], x["id"], x.get("title"), x.get("dueDate"), x.get("status"))
                for x in result
            ]
        ),
    }


def page(snapshot, *, account_ref, session_key, revision, now, offset=0):
    selection = []
    for task in snapshot["tasks"][offset : offset + 10]:
        selection.append(
            {
                "selection_ref": uid(),
                "account_ref": account_ref,
                "project_id": task["projectId"],
                "task_id": task["id"],
                "session_key": session_key,
                "query_revision": revision,
                "expires_at": now.timestamp() + 600,
                "snapshot": task,
            }
        )
    return selection
