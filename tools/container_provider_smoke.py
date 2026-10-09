"""One durable real Provider -> real task fixture, no automatic write replay."""

import json
import subprocess
import time
import uuid

from tools.container_smoke import PREFIX, ROOT, request, unwrap, wait_ready
from tools.live_same_name_smoke import save


def main():
    token = wait_ready()
    journal = ROOT / "runtime-data/container-provider-results.json"
    if journal.exists():
        state = json.loads(journal.read_text(encoding="utf-8"))
    else:
        state = {
            "message_id": "notido-provider-" + str(uuid.uuid4()),
            "text": "记一下：NotiDo 验收 · Provider 正文报告，2027年12月23日16:20截止，提交要求是 PDF 格式并写明验收编号 ND-P01。",
        }
        save(journal, state)
    if not state.get("group_id"):
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                "-w",
                "/opt/notido-plugin",
                "-e",
                "PYTHONPATH=/AstrBot:/opt/notido-plugin",
                "notido-acceptance",
                "python",
                "-m",
                "tools.container_admit_fixture",
            ],
            input=json.dumps(state).encode(),
            capture_output=True,
            check=False,
            timeout=30,
        )
        if result.returncode:
            raise RuntimeError("public fixture admission failed; inspect private evidence")
        admitted = json.loads(result.stdout)
        state["group_id"] = admitted["group_id"]
        save(journal, state)
    deadline = time.monotonic() + 65
    while time.monotonic() < deadline:
        status, response = request(PREFIX + f"notices/{state['group_id']}", token=token)
        detail = unwrap(response)
        if status != 200:
            raise RuntimeError("fixture ledger unavailable")
        status, response = request(PREFIX + "operations?limit=100", token=token)
        if status != 200:
            raise RuntimeError("operation ledger unavailable")
        operations = []
        for row in unwrap(response)["items"]:
            status, value = request(PREFIX + f"operations/{row['id']}", token=token)
            record = unwrap(value)
            if status == 200 and record["plan"].get("group_id") == state["group_id"]:
                operations.append(record)
        detail["operations"] = operations
        if operations and all(
            x["state"]
            not in (
                "validated",
                "executing",
                "created_unverified",
                "uploaded_unverified",
                "applied_unverified",
            )
            for x in operations
        ):
            state["detail"] = detail
            save(journal, state)
            if len(operations) != 1 or operations[0]["state"] != "succeeded":
                raise RuntimeError("real Provider task did not verify; inspect private journal")
            plan = operations[0]["plan"]
            actual = json.loads(operations[0]["result"])["actual_fields"]
            project = json.loads(
                (ROOT / "runtime-data/test-project.json").read_text(encoding="utf-8-sig")
            )
            if (
                actual.get("title") != "NotiDo 验收 · Provider 正文报告"
                or plan["project_id"] != project["id"]
            ):
                raise RuntimeError("explicit task title or dedicated project mismatch")
            if not all(x in actual.get("content", "") for x in ("PDF", "ND-P01")):
                raise RuntimeError("real Provider omitted submission requirements")
            if (
                not plan["normalized_date"]["local_date"] == "2027-12-23"
                or plan["normalized_date"]["local_time"] != "16:20"
            ):
                raise RuntimeError("real Provider date mismatch")
            state["checks"] = [
                {"check": "real_framework_provider_task_native_datetime", "pass": True},
                {"check": "submission_requirements_readback", "pass": True},
                {
                    "check": "reliable_id_single_attempt",
                    "pass": operations[0]["attempt"] == 1 and bool(operations[0]["remote_id"]),
                },
            ]
            save(journal, state)
            print(json.dumps(state["checks"]))
            return
        if detail["group"]["state"] == "awaiting_clarification":
            state["detail"] = detail
            save(journal, state)
            codes = [
                x.get("code")
                for x in json.loads(detail["session"]["question"] or "{}")
                .get("context", {})
                .get("blocked", [])
            ]
            raise RuntimeError(
                f"real Provider fixture requires clarification: {codes}; inspect private evidence"
            )
        time.sleep(0.5)
    raise RuntimeError("fixture still pending; rerun only observes the same admitted message")


if __name__ == "__main__":
    main()
