"""Opt-in local test-account authorization; secrets are never printed or journaled."""

import json
import uuid

from tools.container_smoke import PREFIX, ROOT, request, unwrap, wait_ready

PROVIDER = "deepseek/deepseek-flash"


def snapshot(token):
    status, result = request(PREFIX + "settings", token=token)
    if status != 200:
        raise RuntimeError("settings unavailable")
    return unwrap(result)


def mutate(token, endpoint, values):
    current = snapshot(token)
    status, response = request(
        PREFIX + endpoint,
        token=token,
        payload={
            "request_id": str(uuid.uuid4()),
            "expected_revision": current["revision"],
            **values,
        },
        deadline_seconds=70,
    )
    result = unwrap(response)
    if status != 200 or "error" in result:
        raise RuntimeError(
            f"Setup failed: {endpoint}: {result.get('error', {}).get('code', status)}"
        )
    return result


def main():
    token = wait_ready()
    status, response = request("/api/v1/providers", token=token)
    providers = unwrap(response)["providers"]
    if status != 200 or not any(x["id"] == PROVIDER and x.get("enable") for x in providers):
        raise RuntimeError("user-selected enabled Provider required")
    private = ROOT / "runtime-data"
    project = json.loads((private / "test-project.json").read_text(encoding="utf-8-sig"))
    if project["name"] != "NotiDo 验收":
        raise RuntimeError("dedicated project required")
    current = snapshot(token)
    if not current["settings"]["account_ref"]:
        config = json.loads(
            (private / "cli-home/.config/dida-cli/config.json").read_text(encoding="utf-8")
        )
        mutate(token, "auth/task/set", {"secret": config["access_token"], "account_mode": "new"})
    status, response = request(PREFIX + "projects", token=token)
    result = unwrap(response)
    projects = result.get("projects", result) if isinstance(result, dict) else result
    if status != 200 or not any(
        x["id"] == project["id"] and x["name"] == project["name"] for x in projects
    ):
        raise RuntimeError("existing authorized dedicated account did not match")
    current = snapshot(token)
    settings = current["settings"]
    if settings["allowed_projects"] and settings["allowed_projects"] != [project["id"]]:
        raise RuntimeError("existing scope differs; align before changing it")
    settings.update(
        {
            "provider_id": PROVIDER,
            "default_project": project["id"],
            "allowed_projects": [project["id"]],
        }
    )
    if settings != snapshot(token)["settings"]:
        mutate(token, "settings/save", {"settings": settings})
    status, response = request(PREFIX + "status", token=token)
    state = unwrap(response)
    if not state.get("attachment_authorized"):
        config = json.loads(
            (private / "cli-home/.config/notido-attachments/config.json").read_text(
                encoding="utf-8"
            )
        )
        task = json.loads((private / "live-smoke.json").read_text(encoding="utf-8"))[-1]["task_id"]
        mutate(
            token,
            "auth/attachment/set",
            {
                "secret": config["token"],
                "account_mode": "same",
                "verification_project": project["id"],
                "verification_task": task,
            },
        )
    mutate(
        token,
        "identity/bind",
        {
            "platform_id": "notido-acceptance-fixture",
            "actor_id": "local-test-user",
            "origin": "notido-acceptance-fixture:GroupMessage:provider-acceptance",
            "enabled": True,
        },
    )
    print(
        json.dumps(
            {
                "check": "configured_user_selected_provider_and_dedicated_scope",
                "pass": True,
                "provider_id": PROVIDER,
            }
        )
    )


if __name__ == "__main__":
    main()
