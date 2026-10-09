"""Real pinned-framework unload/reload and single-worker ownership acceptance."""

import json
import subprocess
import time

from tools.container_smoke import PREFIX, ROOT, request, unwrap, wait_ready
from tools.native_corpus_smoke import CONTAINER, runtime_version


def main():
    token = wait_ready()
    version = runtime_version()
    endpoint = "/api/v1/plugins/astrbot_plugin_notido/enabled"
    checks = []
    try:
        status, response = request(
            endpoint, token=token, payload={"enabled": False}, method="PATCH"
        )
        checks.append(
            {
                "check": "framework_unload",
                "pass": status == 200 and response.get("status") != "error",
            }
        )
        probe = subprocess.run(
            [
                "docker",
                "exec",
                CONTAINER,
                "python",
                "-c",
                "from filelock import FileLock; lock=FileLock('/AstrBot/data/plugin_data/astrbot_plugin_notido/worker.lock'); lock.acquire(timeout=0); lock.release()",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        checks.append({"check": "owner_lock_released_on_unload", "pass": probe.returncode == 0})
    finally:
        status, response = request(endpoint, token=token, payload={"enabled": True}, method="PATCH")
        checks.append(
            {
                "check": "framework_reload",
                "pass": status == 200 and response.get("status") != "error",
            }
        )
    loaded = False
    for _ in range(20):
        status, state = request(PREFIX + "status", token=token)
        state = unwrap(state)
        loaded = status == 200 and state.get("worker") == "running" and not state.get("maintenance")
        if loaded:
            break
        time.sleep(0.25)
    checks.append({"check": "reloaded_worker_healthy", "pass": loaded})
    probe = subprocess.run(
        [
            "docker",
            "exec",
            CONTAINER,
            "python",
            "-c",
            "from filelock import FileLock; lock=FileLock('/AstrBot/data/plugin_data/astrbot_plugin_notido/worker.lock'); lock.acquire(timeout=0)",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    checks.append({"check": "second_owner_refused", "pass": probe.returncode != 0})
    target = (
        "container-lifecycle-verified.json"
        if CONTAINER == "notido-acceptance"
        else f"container-lifecycle-{CONTAINER}-{version['runtime_code_sha256'][:12]}-verified.json"
    )
    (ROOT / "runtime-data" / target).write_text(
        json.dumps({**version, "checks": checks, "container": CONTAINER}, indent=2),
        encoding="utf-8",
    )
    for check in checks:
        print(json.dumps(check))
    if not all(x["pass"] for x in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
