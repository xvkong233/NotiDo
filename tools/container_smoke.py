"""Authenticated smoke of the isolated AstrBot container, never prints auth material."""

import json
import os
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from notido.errors import NotiDoError

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("NOTIDO_SMOKE_PORT", "16185"))
if PORT not in (16185, 16186, 16187, 16188, 16189, 16190):
    raise RuntimeError("only the isolated localhost acceptance ports are supported")
BASE = f"http://127.0.0.1:{PORT}"
PREFIX = "/api/v1/plugins/extensions/astrbot_plugin_notido/"


def request(path, *, token=None, payload=None, method=None, deadline_seconds=15):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=deadline_seconds) as response:
            data = response.read()
            try:
                return response.status, json.loads(data)
            except ValueError:
                return response.status, data.decode()
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def unwrap(data):
    if isinstance(data, dict) and data.get("status") == "error":
        raise RuntimeError("Framework request failed")
    return data.get("data", data) if isinstance(data, dict) else data


def wait_ready():
    credentials = json.loads((ROOT / "runtime-data/acceptance-webui.json").read_text())
    cache = (
        ROOT / f"runtime-data/acceptance-session{('-' + str(PORT)) if PORT != 16185 else ''}.json"
    )
    try:
        token = json.loads(cache.read_text())["token"]
    except (OSError, ValueError, KeyError):
        token = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if not token:
                status, response = request(
                    "/api/v1/auth/login", payload=credentials, deadline_seconds=2
                )
                if status != 200:
                    raise NotiDoError(
                        "SMOKE_LOGIN_REJECTED", "本机验收登录被拒绝，请先检查本机登录信息或限流。"
                    )
                token = unwrap(response)["token"]
                staged = cache.with_suffix(".tmp")
                staged.write_text(json.dumps({"token": token}), encoding="utf-8")
                os.chmod(staged, 0o600)
                os.replace(staged, cache)
            status, response = request(PREFIX + "status", token=token, deadline_seconds=2)
            if status in (401, 403):
                token = None
            else:
                result = unwrap(response)
                if (
                    status == 200
                    and result.get("worker") == "running"
                    and not result.get("maintenance")
                ):
                    return token
        except (OSError, ValueError, KeyError, RuntimeError):
            pass
        time.sleep(2 if not token else 0.5)
    raise RuntimeError("Isolated plugin did not become ready within the probe budget")


def main():
    private = ROOT / "runtime-data"
    credential = private / "acceptance-webui.json"
    if not credential.exists():
        logs = subprocess.check_output(
            ["docker", "logs", "notido-acceptance"], stderr=subprocess.DEVNULL
        ).decode(errors="replace")
        logs = re.sub(r"\x1b\[[0-9;]*m", "", logs)
        initial = re.search(r"Initial password:\s*(\S+)", logs)
        if not initial:
            raise RuntimeError("Isolated initial password unavailable")
        initial = initial[1]
        status, response = request(
            "/api/v1/auth/login", payload={"username": "astrbot", "password": initial}
        )
        if status != 200:
            raise RuntimeError("Isolated login failed")
        token = unwrap(response)["token"]
        password = secrets.token_urlsafe(32) + "aA1!"
        status, response = request(
            "/api/v1/auth/account",
            token=token,
            payload={"password": initial, "new_password": password, "confirm_password": password},
            method="PATCH",
        )
        if status != 200 or response.get("status") == "error":
            raise RuntimeError("Isolated password rotation failed")
        credential.write_text(
            json.dumps({"username": "astrbot", "password": password}), encoding="utf-8"
        )
    token = wait_ready()
    status, baseline = request(PREFIX + "operations", token=token)
    baseline = unwrap(baseline)
    if status != 200:
        raise RuntimeError("operation ledger unavailable before read-only smoke")
    before = {x["id"] for x in baseline["items"]}
    checks = []
    for endpoint in (
        "status",
        "settings",
        "notices",
        "assets",
        "operations",
        "receipts",
        "source-capabilities",
        "assets/missing/download",
    ):
        status, _ = request(PREFIX + endpoint)
        checks.append({"check": f"unauthorized:{endpoint}", "pass": status in (401, 403)})
    status, result = request(PREFIX + "status", token=token)
    result = unwrap(result)
    checks.append(
        {
            "check": "plugin_loaded_worker_running",
            "pass": status == 200
            and result.get("worker") == "running"
            and not result.get("maintenance"),
        }
    )
    status, result = request("/api/v1/plugins", token=token)
    result = unwrap(result)
    plugin = next((x for x in result if x.get("name") == "astrbot_plugin_notido"), {})
    checks.append(
        {
            "check": "page_discovered",
            "pass": status == 200 and "dashboard" in plugin.get("pages", []),
        }
    )
    status, result = request(PREFIX + "source-capabilities", token=token)
    result = unwrap(result)
    checks.append(
        {
            "check": "production_website_disabled",
            "pass": status == 200 and result["sources"][0]["state"] == "disabled",
        }
    )
    # A read-only refreshed status must not cause an operation.
    status, result = request(PREFIX + "operations", token=token)
    result = unwrap(result)
    checks.append(
        {
            "check": "read_only_smoke_no_new_operation",
            "pass": status == 200 and {x["id"] for x in result["items"]} == before,
        }
    )
    import sys

    if "--expect-clean" in sys.argv:
        checks.append(
            {"check": "clean_install_no_write", "pass": status == 200 and not result["items"]}
        )
    (private / "container-verified.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    for check in checks:
        print(json.dumps(check))
    if not all(check["pass"] for check in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
