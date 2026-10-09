"""Actual authenticated new configuration and continuation routes; no remote writes."""

import json
import subprocess
import uuid

from tools.container_smoke import PREFIX, ROOT, request, unwrap, wait_ready


def main():
    token = wait_ready()
    checks = []
    status, initial = request(PREFIX + "settings", token=token)
    initial = unwrap(initial)
    original = initial["settings"]
    proposed = json.loads(json.dumps(original))
    proposed["materials"]["block_characters"] = 5000
    proposed["time_budgets"]["text_seconds"] = 45
    proposed["retention"]["body_days"] = 31
    group, question = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        body = {
            "request_id": str(uuid.uuid4()),
            "expected_revision": initial["revision"],
            "settings": proposed,
        }
        status, result = request(PREFIX + "settings/save", token=token, payload=body)
        first = unwrap(result)
        checks.append(
            {
                "check": "configurable_material_time_retention_budgets",
                "pass": status == 200 and first.get("revision") == initial["revision"] + 1,
            }
        )
        status, duplicate = request(PREFIX + "settings/save", token=token, payload=body)
        checks.append(
            {
                "check": "configuration_request_dedup",
                "pass": status == 200 and unwrap(duplicate) == first,
            }
        )
        status, snapshot = request(PREFIX + "settings", token=token)
        snapshot = unwrap(snapshot)
        checks.append(
            {
                "check": "configuration_readback",
                "pass": status == 200 and snapshot["settings"] == proposed,
            }
        )
        for endpoint in (
            "recovery/status",
            "notices/missing/continue",
            "operations/missing/revalidate",
            "recovery/check",
            "recovery/confirm",
        ):
            status, _ = request(
                PREFIX + endpoint, payload={} if endpoint != "recovery/status" else None
            )
            checks.append(
                {"check": "protected_new_route:" + endpoint, "pass": status in (401, 403)}
            )
        status, recovery = request(PREFIX + "recovery/status", token=token)
        checks.append(
            {
                "check": "fresh_install_no_restore_gate",
                "pass": status == 200 and unwrap(recovery) == {"required": False, "review": None},
            }
        )
        status, rejected = request(
            PREFIX + "recovery/check",
            token=token,
            payload={"request_id": str(uuid.uuid4()), "expected_revision": snapshot["revision"]},
        )
        checks.append(
            {
                "check": "restore_check_requires_actual_marker",
                "pass": status == 409
                and unwrap(rejected).get("error", {}).get("code") == "RESTORE_REVIEW_NOT_REQUIRED",
            }
        )
        seed = """import sqlite3,time,sys,json
with sqlite3.connect('/AstrBot/data/plugin_data/astrbot_plugin_notido/notido.db') as c:
 c.execute('PRAGMA foreign_keys=ON')
 g,q=sys.argv[1:]
 payload=json.dumps({'question_ref':q,'group_id':g,'questions':['本机过期问题夹具'],'context':{},'yes_no':False})
 c.execute("INSERT INTO sessions(id,user_id,question,question_expires) VALUES (?,'personal',?,?)",(g,payload,time.time()-60))
 c.execute("INSERT INTO material_groups VALUES (?,'personal',?,'direct','awaiting_clarification',?,?,?,0)",(g,g,time.time(),time.time(),time.time()))
 c.execute('INSERT INTO question_history VALUES (?,?,?,?,?,?)',(q,g,g,payload,time.time()-60,time.time()-1860))
"""
        subprocess.run(
            ["docker", "exec", "notido-acceptance", "python", "-c", seed, group, question],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        body = {"request_id": str(uuid.uuid4()), "expected_revision": 0}
        status, fresh = request(PREFIX + f"notices/{group}/continue", token=token, payload=body)
        fresh = unwrap(fresh)
        checks.append(
            {
                "check": "expired_question_new_reference",
                "pass": status == 200 and fresh["question"]["question_ref"] != question,
            }
        )
        status, repeated = request(PREFIX + f"notices/{group}/continue", token=token, payload=body)
        checks.append(
            {
                "check": "continuation_request_dedup",
                "pass": status == 200 and unwrap(repeated) == fresh,
            }
        )
        status, rejected = request(
            PREFIX + f"notices/{group}/resolve",
            token=token,
            payload={
                "request_id": str(uuid.uuid4()),
                "expected_revision": fresh["revision"],
                "question_ref": question,
                "answer": "旧问题无效",
            },
        )
        checks.append(
            {
                "check": "old_question_reference_rejected",
                "pass": status == 409
                and unwrap(rejected).get("error", {}).get("code") == "QUESTION_EXPIRED",
            }
        )
        (ROOT / "runtime-data/container-recovery-verified.json").write_text(
            json.dumps(checks, indent=2), encoding="utf-8"
        )
        for check in checks:
            print(json.dumps(check))
        if not all(check["pass"] for check in checks):
            raise SystemExit(1)
    finally:
        status, snapshot = request(PREFIX + "settings", token=token)
        if status == 200 and unwrap(snapshot)["settings"] != original:
            status, _ = request(
                PREFIX + "settings/save",
                token=token,
                payload={
                    "request_id": str(uuid.uuid4()),
                    "expected_revision": unwrap(snapshot)["revision"],
                    "settings": original,
                },
            )
            if status != 200:
                raise RuntimeError("failed to restore acceptance settings")
        cleanup = """import sqlite3,sys
with sqlite3.connect('/AstrBot/data/plugin_data/astrbot_plugin_notido/notido.db') as c:
 c.execute("UPDATE material_groups SET state='cancelled',revision=revision+1 WHERE id=?",(sys.argv[1],))
 c.execute("UPDATE sessions SET question=NULL,question_expires=NULL WHERE id=?",(sys.argv[1],))
"""
        subprocess.run(
            ["docker", "exec", "notido-acceptance", "python", "-c", cleanup, group],
            check=True,
            stdout=subprocess.DEVNULL,
        )


if __name__ == "__main__":
    main()
