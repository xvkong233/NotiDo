"""Actual framework HTTP multipart/download/revision smoke, local synthetic group only."""

import hashlib
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import uuid

from tools.container_smoke import BASE, PREFIX, ROOT, request, unwrap, wait_ready


def main():
    token = wait_ready()
    group, session = str(uuid.uuid4()), str(uuid.uuid4())
    seed = """import sqlite3,time,sys
root='/AstrBot/data/plugin_data/astrbot_plugin_notido/notido.db'
with sqlite3.connect(root) as c:
 c.execute('PRAGMA foreign_keys=ON')
 c.execute("INSERT INTO sessions (id,user_id) VALUES (?,'personal')",(sys.argv[2],))
 c.execute("INSERT INTO material_groups VALUES (?,'personal',?,'explicit','collecting',?,?,?,0)",(sys.argv[1],sys.argv[2],time.time(),time.time(),time.time()+600))
"""
    subprocess.run(
        ["docker", "exec", "notido-acceptance", "python", "-c", seed, group, session],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    checks = []
    content = "NotiDo 本机页面原件验收\n<不解释为 HTML>".encode()
    boundary = "notido-" + uuid.uuid4().hex
    multipart = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="original.txt"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    metadata = {
        "request_id": str(uuid.uuid4()),
        "expected_revision": "0",
        "sha256": hashlib.sha256(content).hexdigest(),
        "name": "页面验收原件.txt",
    }

    def upload(values):
        req = urllib.request.Request(
            BASE + PREFIX + f"groups/{group}/files?" + urllib.parse.urlencode(values),
            data=multipart,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.status, unwrap(json.loads(response.read()))
        except urllib.error.HTTPError as error:
            return error.code, unwrap(json.loads(error.read()))

    try:
        status, first = upload(metadata)
        checks.append(
            {
                "check": "public_bridge_multipart_query_metadata",
                "pass": status == 200 and first.get("hash") == metadata["sha256"],
            }
        )
        status, duplicate = upload(metadata)
        checks.append(
            {
                "check": "upload_dedup_before_stale_revision",
                "pass": status == 200 and duplicate == first,
            }
        )
        status, conflict = upload({**metadata, "name": "changed.txt"})
        checks.append(
            {
                "check": "upload_request_id_payload_conflict",
                "pass": status == 409
                and conflict.get("error", {}).get("code") == "REQUEST_ID_REUSED",
            }
        )
        status, stale = upload({**metadata, "request_id": str(uuid.uuid4())})
        checks.append(
            {
                "check": "upload_stale_revision_rejected",
                "pass": status == 409 and stale.get("error", {}).get("code") == "REVISION_CONFLICT",
            }
        )
        req = urllib.request.Request(
            BASE + PREFIX + f"assets/{first['asset_id']}/download",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            actual = response.read()
            checks.append(
                {
                    "check": "authenticated_original_download_hash",
                    "pass": response.status == 200
                    and hashlib.sha256(actual).hexdigest() == metadata["sha256"],
                }
            )
        for endpoint in (
            "settings/save",
            "identity/bind",
            "auth/task/set",
            "auth/attachment/set",
            "auth/clear",
            f"notices/{group}/resolve",
            f"notices/{group}/continue",
            f"groups/{group}/close",
            "operations/missing/reconcile",
            "operations/missing/retry",
            "operations/missing/link-existing",
            "operations/missing/cancel-local",
            "operations/missing/revalidate",
            "recovery/check",
            "recovery/confirm",
            "receipts/missing/retry",
            "sources/enable",
        ):
            status, _ = request(PREFIX + endpoint, payload={})
            checks.append(
                {
                    "check": "unauthorized_post:" + endpoint.split("/")[0],
                    "pass": status in (401, 403),
                }
            )
        (ROOT / "runtime-data/container-pages-verified.json").write_text(
            json.dumps(checks, indent=2), encoding="utf-8"
        )
        for check in checks:
            print(json.dumps(check))
        if not all(x["pass"] for x in checks):
            raise SystemExit(1)
    finally:
        cleanup = """import sqlite3,sys
with sqlite3.connect('/AstrBot/data/plugin_data/astrbot_plugin_notido/notido.db') as c:
 c.execute("UPDATE material_groups SET state='cancelled',revision=revision+1 WHERE id=?",(sys.argv[1],))
"""
        subprocess.run(
            ["docker", "exec", "notido-acceptance", "python", "-c", cleanup, group],
            check=True,
            stdout=subprocess.DEVNULL,
        )


if __name__ == "__main__":
    main()
