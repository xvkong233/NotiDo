"""Probe every registered Pages route without credentials; never log bodies."""

import argparse
import ast
import json

from tools.container_smoke import PORT, PREFIX, ROOT, request
from tools.native_corpus_smoke import runtime_version


def main(batch="v7"):
    if PORT != 16190:
        raise RuntimeError("reuse the sole current acceptance instance")
    source = ast.parse((ROOT / "notido/api.py").read_text(encoding="utf-8"))
    register = next(
        node
        for node in ast.walk(source)
        if isinstance(node, ast.FunctionDef) and node.name == "register"
    )
    assignment = next(
        node
        for node in register.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "routes" for t in node.targets)
    )
    if not isinstance(assignment.value, ast.Dict):
        raise RuntimeError("inspect changed route registration before probing")
    routes = [
        (ast.literal_eval(key), ast.literal_eval(value.elts[0]))
        for key, value in zip(assignment.value.keys, assignment.value.values, strict=True)
    ]
    if not routes or not any(route == "assets/<id>/download" for route, _ in routes):
        raise RuntimeError("inspect changed route inventory")
    version = runtime_version()
    target = ROOT / f"runtime-data/native-pages-{batch}-auth-results.json"
    if target.exists():
        raise RuntimeError("retain existing authentication evidence; select another batch")
    checks = []
    for route, method in routes:
        status, _ = request(
            PREFIX + route.replace("<id>", "00000000-0000-4000-8000-000000000000"),
            method=method,
            payload={} if method == "POST" else None,
        )
        checks.append({"route": route, "method": method, "status": status})
        if status not in (401, 403):
            raise RuntimeError(f"unauthenticated route did not reject: {method} {route}")
    if runtime_version() != version:
        raise RuntimeError("installed version changed during authentication checks")
    target.write_text(json.dumps({**version, "checks": checks}, indent=2), encoding="utf-8")
    print(json.dumps({"routes": len(checks), "all_unauthenticated_rejected": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", choices=("v7", "v7-r2", "read-budget"), default="v7")
    main(parser.parse_args().batch)
