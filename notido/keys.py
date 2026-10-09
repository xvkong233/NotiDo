import hashlib
import json
from uuid import uuid4


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def key(*parts) -> str:
    return hashlib.sha256(canonical([1, *parts]).encode()).hexdigest()


def uid() -> str:
    return str(uuid4())
