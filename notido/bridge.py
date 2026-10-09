import asyncio
from datetime import UTC, datetime

from .errors import NotiDoError
from .keys import key, uid
from .models import InputEnvelope, Segment


class AstrBotBridge:
    def __init__(self, context, instance_id):
        self.context, self.instance_id = context, instance_id
        self.material_refs = {}

    def normalize_event(self, event):
        from astrbot.api.message_components import File, Forward, Image, Node, Nodes, Plain

        segments = []
        material_refs = {}
        count, characters = 0, 0
        forwarded = False

        def visit(components, author=None, published=None):
            nonlocal count, characters, forwarded
            for component in components:
                count += 1
                if count > 100:
                    raise NotiDoError(
                        "NODE_LIMIT", "消息超过 100 节点，暂未接收，请分组发送。", status=400
                    )
                source = uid()
                if isinstance(component, Plain):
                    characters += len(component.text)
                    if characters > 50000:
                        raise NotiDoError(
                            "TEXT_LIMIT", "正文超过 50,000 字，请分组发送。", status=400
                        )
                    segments.append(
                        Segment(
                            source_id=source,
                            kind="text",
                            text=component.text,
                            author=author,
                            published_at=published,
                        )
                    )
                elif isinstance(component, File | Image):
                    material_refs[source] = component
                    segments.append(
                        Segment(
                            source_id=source,
                            kind="file" if isinstance(component, File) else "image",
                            author=author,
                            published_at=published,
                        )
                    )
                elif isinstance(component, Node):
                    forwarded = True
                    timestamp = component.time
                    actual = (
                        datetime.fromtimestamp(timestamp, UTC)
                        if isinstance(timestamp, int) and timestamp > 0
                        else None
                    )
                    visit(component.content, component.name, actual)
                elif isinstance(component, Nodes):
                    forwarded = True
                    visit(component.nodes)
                elif isinstance(component, Forward):
                    forwarded = True
                    segments.append(
                        Segment(
                            source_id=source,
                            kind="unavailable",
                            unavailable_reason="FORWARD_NOT_EXPANDED",
                        )
                    )
                elif component.__class__.__name__ not in ("At", "AtAll", "Reply", "Face"):
                    segments.append(
                        Segment(
                            source_id=source,
                            kind="unavailable",
                            unavailable_reason="COMPONENT_NOT_READABLE",
                        )
                    )

        visit(event.get_messages())
        platform = event.get_platform_id()
        actor = event.get_sender_id()
        origin = event.unified_msg_origin
        message = getattr(event.message_obj, "message_id", None)
        if not actor or not platform or not origin:
            raise NotiDoError("FRAMEWORK_ID_UNAVAILABLE", "框架身份标识缺失，不能自动写入。")
        envelope = InputEnvelope(
            event_id=uid(),
            framework_instance_id=self.instance_id,
            session_key=key(self.instance_id, origin),
            actor_key=key(self.instance_id, platform, actor),
            message_key=message if isinstance(message, str) and message else None,
            received_at=datetime.fromtimestamp(event.created_at, UTC),
            source_kind="user_forward" if forwarded else "manual_notice",
            segments=segments,
            reply_origin_ref=origin,
        )
        self.material_refs.update(material_refs)
        return envelope

    def identity(self, event):
        platform, actor, origin = (
            event.get_platform_id(),
            event.get_sender_id(),
            event.unified_msg_origin,
        )
        return {
            "i": self.instance_id,
            "a": key(self.instance_id, platform, actor),
            "s": key(self.instance_id, origin),
        }

    def release_material_refs(self, envelope):
        for segment in envelope.segments:
            self.material_refs.pop(segment.source_id, None)

    @staticmethod
    def confirmation_text(event):
        from astrbot.api.message_components import Plain

        # Only the sender's top-level text counts; forwarded nodes/files are evidence.
        return "\n".join(
            component.text for component in event.get_messages() if isinstance(component, Plain)
        )

    async def acquire_material(self, source_id):
        component = self.material_refs.pop(source_id, None)
        if component is None:
            raise NotiDoError("MATERIAL_RESEND_REQUIRED", "框架材料引用已失效，请补发原件。")
        from astrbot.api.message_components import Image

        try:
            path = await asyncio.wait_for(
                component.convert_to_file_path()
                if isinstance(component, Image)
                else component.get_file(allow_return_url=False),
                60,
            )
        except Exception as exc:
            raise NotiDoError("MATERIAL_UNAVAILABLE", "框架未能交付原件，请补发。") from exc
        if not path:
            raise NotiDoError("MATERIAL_UNAVAILABLE", "框架未交付可用原件，请补发。")
        return path, getattr(component, "name", None) or (
            "截图.png" if isinstance(component, Image) else "原件"
        )

    async def reply(self, origin, body):
        from astrbot.api.event import MessageChain

        try:
            result = await self.context.send_message(origin, MessageChain().message(body))
            return "sent" if result is True else "unknown"
        except Exception:
            return "failed"
