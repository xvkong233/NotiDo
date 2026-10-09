"""Journaled real Provider late originals, explicit reprovision and backend supplement."""

import hashlib
import json
import time
import urllib.request
import uuid

from tools import native_corpus_smoke as collector
from tools.container_smoke import BASE, PREFIX, ROOT, request, unwrap, wait_ready
from tools.native_material_smoke import upload
from tools.native_performance_smoke import bind_session

JOURNAL = ROOT / "runtime-data/native-late-material-results.json"
TITLE = "NotiDo 验收 · 迟到原件与明确补件"


def group(token, identifier):
    return unwrap(request(PREFIX + "notices/" + identifier, token=token)[1])


def file_message(attachment, text):
    return [
        {"type": "plain", "text": text},
        {
            "type": "file",
            "attachment_id": attachment["attachment_id"],
            "filename": attachment["filename"],
        },
    ]


def supplement(token, path, group_id, operation_id, revision, request_id):
    boundary = "NotiDo" + uuid.uuid4().hex
    values = {
        "request_id": request_id,
        "expected_revision": str(revision),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "name": path.name,
        "target_operation_id": operation_id,
    }
    body = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, value in values.items()
    )
    body += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    body += path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + PREFIX + f"groups/{group_id}/files",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req, timeout=40) as response:
        return unwrap(json.loads(response.read()))


def main():
    collector.JOURNAL = JOURNAL
    token = wait_ready()
    version = collector.runtime_version()
    state = json.loads(JOURNAL.read_text("utf-8")) if JOURNAL.exists() else {}
    if not state:
        settings = unwrap(request(PREFIX + "settings", token=token)[1])["settings"]
        project = json.loads((ROOT / "runtime-data/test-project.json").read_text("utf-8-sig"))
        if project["name"] != "NotiDo 验收" or settings["allowed_projects"] != [project["id"]]:
            raise RuntimeError("dedicated authorized test scope required")
        state = {
            **version,
            "record": {"id": "late-material", "session": bind_session(token)},
            "checks": [],
        }
        collector.save(state)
    if any(state.get(k) != v for k, v in version.items()):
        raise RuntimeError("don't mix implementation versions")
    record = state["record"]
    created = collector.send_once(
        token,
        state,
        record,
        "created",
        f"在NotiDo 验收清单新建无日期任务“{TITLE}”。这是直接记事，暂时不需附件。",
    )
    rows = created["operations"]
    if len(rows) != 1 or rows[0]["kind"] != "create" or rows[0]["state"] != "succeeded":
        raise RuntimeError("one actual verified task required; never resend")
    operation = rows[0]
    group_id = operation["plan"]["group_id"]
    folder = ROOT / "runtime-data/native-late-material-files"
    folder.mkdir(exist_ok=True)
    path = folder / "NotiDo-迟到原件.txt"
    if not path.exists():
        path.write_text(f"本原件仅属于{TITLE}。\n合成验收编号：{uuid.uuid4()}\n", "utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if "attachment" not in state:
        state["attachment"] = upload(token, path)
        state["original_hash"] = digest
        collector.save(state)
    late = collector.send_once(
        token,
        state,
        record,
        "same_session_late",
        file_message(
            state["attachment"],
            f"补上刚才“{TITLE}”的原件。只挂到已经保存的那一个任务，不新建任务。",
        ),
    )
    uploads = late["operations"]
    if len(uploads) != 1 or uploads[0]["kind"] != "upload" or uploads[0]["state"] != "succeeded":
        raise RuntimeError("one verified late upload required; inspect without replay")
    if (
        uploads[0]["plan"]["task_id"] != operation["remote_id"]
        or json.loads(uploads[0]["result"])["actual_fields"].get("sha256") != digest
    ):
        raise RuntimeError("late original target/download hash mismatch")
    if "other" not in state:
        state["other"] = {"id": "explicit-reprovision", "session": bind_session(token)}
        collector.save(state)
    reprovided = collector.send_once(
        token,
        state,
        state["other"],
        "evaluation",
        file_message(
            state["attachment"],
            f"我在这个会话重新提供“{TITLE}”的同一原件。只查询核对这个唯一任务并挂原件；"
            "若同一目标已有完全相同原件则直接复用，不新建任务、不重复上传。",
        ),
    )
    if reprovided["operations"]:
        raise RuntimeError("explicit reprovision unexpectedly rewrote an existing original")
    returned_uploads = [
        v
        for name, v in collector.tool_results(reprovided["output"])
        if name == "notido_attach" and v.get("state") == "succeeded"
    ]
    if not returned_uploads or returned_uploads[-1].get("operation_id") != uploads[0]["id"]:
        raise RuntimeError("actual idempotent upload receipt required")
    # Establish a real completed conclusion before adding controlled backend material.
    collector.send_once(
        token,
        state,
        record,
        "before_backend",
        f"原件已核验。请只读取材料组{group_id}并登记该组completed，不新建、修改或重新上传。",
    )
    before = group(token, group_id)
    backend_path = folder / "NotiDo-后台补充原件.txt"
    if not backend_path.exists():
        backend_path.write_text(f"{TITLE}的后台补充原件，编号{uuid.uuid4()}。", "utf-8")
    if "backend" not in state:
        if before["group"]["state"] != "completed":
            raise RuntimeError("actual completed original group required")
        state["backend"] = {
            "request_id": str(uuid.uuid4()),
            "revision": before["group"]["revision"],
            "before": sorted(collector.ledger_ids(token)),
        }
        collector.save(state)
    backend = state["backend"]
    if "response" not in backend:
        backend["response"] = supplement(
            token,
            backend_path,
            group_id,
            operation["id"],
            backend["revision"],
            backend["request_id"],
        )
        collector.save(state)
    if "invalidated" not in backend:
        invalidated = group(token, group_id)
        if (
            invalidated["native_outcome"][0]["declaration"] is not None
            or invalidated["group"]["state"] == "completed"
        ):
            raise RuntimeError("new backend material did not invalidate completion")
        backend["invalidated"] = invalidated["group"]["state"]
        collector.save(state)
    deadline = time.monotonic() + 80
    while time.monotonic() < deadline:
        matching = [
            row
            for row in collector.new_operations(token, backend["before"])
            if row["kind"] == "upload"
            and row["plan"].get("asset_id") == backend["response"]["asset_id"]
        ]
        if matching and matching[0]["state"] == "succeeded":
            backend["operation"] = matching[0]
            collector.save(state)
            break
        time.sleep(1)
    else:
        raise RuntimeError("backend original upload not verified; don't resubmit")
    actual = json.loads(backend["operation"]["result"])["actual_fields"]
    if actual.get("sha256") != hashlib.sha256(backend_path.read_bytes()).hexdigest():
        raise RuntimeError("backend original download hash mismatch")
    final = collector.send_once(
        token,
        state,
        record,
        "after_backend",
        f"我在后台向材料组{group_id}补充了已挂到原任务的原件。请读取该组全部材料、核查上传操作"
        f"{backend['operation']['id']}，如已无未决事项登记completed，不新建任务或重新上传。",
    )
    if final["operations"] or group(token, group_id)["group"]["state"] != "completed":
        raise RuntimeError("native conclusion did not close without repeated writes")
    state["checks"] = [
        {"check": name, "pass": True}
        for name in (
            "same_session_late_exact_target_hash",
            "explicit_cross_session_reprovision_zero_reupload",
            "backend_material_invalidates_completion",
            "backend_exact_target_download_hash",
            "native_read_and_check_close_original_group_zero_writes",
        )
    ]
    state["phase"] = "verified"
    collector.save(state)
    print(json.dumps(state["checks"]), flush=True)


if __name__ == "__main__":
    main()
