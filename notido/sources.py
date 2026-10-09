from dataclasses import dataclass
from typing import Protocol

from .errors import NotiDoError
from .models import InputEnvelope


@dataclass(frozen=True)
class SourceEnvelope:
    envelope: InputEnvelope
    source_ref: str


class SourceAdapter(Protocol):
    def describe(self) -> dict: ...
    async def poll(self) -> list[SourceEnvelope]: ...
    async def fetch_materials(self, source_ref: str) -> list: ...


class DisabledWebsiteSource:
    def describe(self):
        return {
            "id": "website",
            "state": "disabled",
            "planned": True,
            "supported": False,
            "code": "SOURCE_NOT_IMPLEMENTED",
        }

    async def poll(self):
        raise NotiDoError("SOURCE_NOT_IMPLEMENTED", "官网接口预留，采集未实现。", status=503)

    async def fetch_materials(self, source_ref):
        raise NotiDoError("SOURCE_NOT_IMPLEMENTED", "官网接口预留，采集未实现。", status=503)


class SourceRegistry:
    def __init__(self):
        self.adapters = {"website": DisabledWebsiteSource()}

    def describe(self):
        return [x.describe() for x in self.adapters.values()]

    def enable(self, source):
        raise NotiDoError("SOURCE_NOT_IMPLEMENTED", "官网采集未实现，不能启用。", status=503)


class MemorySourceAdapter:
    """Test-only adapter. Never registered by the production registry."""

    def __init__(self, envelopes):
        self.envelopes = list(envelopes)

    def describe(self):
        return {"id": "memory", "test_only": True}

    async def poll(self):
        result, self.envelopes = self.envelopes, []
        return result

    async def fetch_materials(self, source_ref):
        return []
