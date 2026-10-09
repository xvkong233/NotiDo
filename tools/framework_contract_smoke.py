"""Run inside the pinned AstrBot image: public event/component contract fixtures."""

import asyncio
import hashlib
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import File, Node, Plain
from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType, PlatformMetadata

from notido.bridge import AstrBotBridge
from notido.materials import AsyncFile, BlobStore


class OpaqueMessage(AstrBotMessage):
    @property
    def raw_message(self):
        raise AssertionError("raw_message is outside the plugin contract")


async def main():
    checks = []
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        original = root / "notice.txt"
        content = "原件验收：2027年12月20日提交 PDF。".encode()
        original.write_bytes(content)
        message = OpaqueMessage()
        message.type = MessageType.GROUP_MESSAGE
        message.sender = MessageMember(user_id="opaque:sender/零一", nickname="不可用于授权")
        message.self_id = "opaque:self"
        message.message_id = "opaque:message/2026"
        message.message = [
            Plain("外层正文"),
            Node(
                content=[Plain("原通知"), File(name="notice.txt", file=str(original))],
                name="原作者",
                time=1791331200,
            ),
            Plain("补充"),
        ]
        message.message_str = "外层正文"
        event = AstrMessageEvent(
            "外层正文",
            message,
            PlatformMetadata(
                name="fixture", description="Public contract only", id="fixture-instance"
            ),
            "opaque:session/零一",
        )
        bridge = AstrBotBridge(SimpleNamespace(), "notido-fixture")
        envelope = bridge.normalize_event(event)
        checks.append(
            {
                "check": "public_event_opaque_ids_no_raw",
                "pass": envelope.message_key == message.message_id
                and bool(envelope.actor_key)
                and envelope.reply_origin_ref == event.unified_msg_origin,
            }
        )
        checks.append(
            {
                "check": "component_order_original_time",
                "pass": [x.text for x in envelope.segments] == ["外层正文", "原通知", None, "补充"]
                and envelope.segments[1].published_at.timestamp() == 1791331200
                and envelope.segments[0].published_at is None,
            }
        )
        path, name = await bridge.acquire_material(envelope.segments[2].source_id)
        store = BlobStore(root / "storage")
        source = await asyncio.to_thread(Path(path).open, "rb")
        try:
            blob = await store.save(AsyncFile(source), min_free_bytes=0)
        finally:
            await asyncio.to_thread(source.close)
        checks.append(
            {
                "check": "public_file_original_bytes",
                "pass": name == "notice.txt"
                and blob["hash"] == hashlib.sha256(content).hexdigest(),
            }
        )
        event.stop_event()
        checks.append({"check": "public_propagation_stop", "pass": event.is_stopped()})
        message.message_id = ""
        missing = bridge.normalize_event(event)
        checks.append(
            {"check": "missing_stable_id_not_invented", "pass": missing.message_key is None}
        )
        bridge.release_material_refs(missing)
        checks.append({"check": "component_reference_release", "pass": not bridge.material_refs})
    print(json.dumps(checks))
    if not all(x["pass"] for x in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
