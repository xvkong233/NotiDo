"""Shared local cancellation transition; never performs a remote operation."""

from .db import execute, one
from .errors import NotiDoError


async def cancel_unstarted(conn, operation_id):
    row = await one(conn, "SELECT * FROM operations WHERE id=:id", {"id": operation_id})
    if not row:
        raise NotiDoError("OPERATION_NOT_FOUND", "未找到本地操作。", status=404)
    if row["state"] == "cancelled":
        return {"operation_id": row["id"], "state": "cancelled", "revision": row["revision"]}
    if row["state"] != "validated" or row["attempt"] or row["remote_id"] is not None:
        raise NotiDoError(
            "OPERATION_IN_PROGRESS", "只有尚未开始且没有远端结果的操作可以取消；已发生的结果保留。"
        )
    changed = await execute(
        conn,
        "UPDATE operations SET state='cancelled',revision=revision+1 WHERE id=:id AND state='validated' AND attempt=0 AND remote_id IS NULL AND revision=:r",
        {"id": row["id"], "r": row["revision"]},
    )
    if changed.rowcount != 1:
        raise NotiDoError("REVISION_CONFLICT", "操作已开始或已变更，请重新核查。")
    await execute(
        conn,
        "UPDATE jobs SET state='done',owner=NULL,error='OPERATION_CANCELLED' WHERE kind='execute_operation' AND state='pending' AND json_extract(payload,'$.operation_id')=:id",
        {"id": row["id"]},
    )
    return {"operation_id": row["id"], "state": "cancelled", "revision": row["revision"] + 1}
