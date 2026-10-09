"""Public event fixture helpers; smoke delegates to the native tools contract."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Plain
from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType, PlatformMetadata

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Event(AstrMessageEvent):
    async def send(self, message):
        self.fixture_replies.append(message)


class ContextFixture:
    def __init__(self):
        self.models, self.sent, self.routes = [], [], []
        self.reply_ready = asyncio.Event()

    def register_web_api(self, *args):
        self.routes.append(args)

    async def llm_generate(self, **kwargs):
        self.models.append(kwargs)
        raise AssertionError("NotiDo must not invoke AI outside the native AstrBot pipeline")

    async def send_message(self, origin, chain):
        self.sent.append((origin, chain))
        self.reply_ready.set()
        return True


def event(text, message_id, sender="fixture-actor"):
    obj = AstrBotMessage()
    obj.type, obj.self_id, obj.message_id = MessageType.FRIEND_MESSAGE, "fixture-self", message_id
    obj.sender, obj.message, obj.message_str = (
        MessageMember(sender, "不能授权的昵称"),
        [Plain(text)],
        text,
    )
    instance = Event(
        text,
        obj,
        PlatformMetadata("fixture", "Public contract fixture", "fixture-platform"),
        "fixture-session",
    )
    instance.fixture_replies = []
    return instance


def json_request(payload):
    async def json_body():
        return payload

    return SimpleNamespace(json=json_body)


async def main():
    from tools.native_contract_smoke import main as native_main

    await native_main()


if __name__ == "__main__":
    asyncio.run(main())
