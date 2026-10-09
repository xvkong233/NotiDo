"""Admit a public-event fixture into the running worker DB without initializing it."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Plain
from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType, PlatformMetadata

from notido.bridge import AstrBotBridge
from notido.cli import CLIRunner, DidaGateway
from notido.service import Service


class PublicMessage(AstrBotMessage):
    @property
    def raw_message(self):
        raise AssertionError("raw_message is outside the plugin contract")


async def main():
    fixture = json.loads(sys.stdin.read())
    message = PublicMessage()
    message.type = MessageType.GROUP_MESSAGE
    message.sender = MessageMember(user_id="local-test-user", nickname="验收夹具")
    message.self_id = "notido-acceptance-fixture"
    message.message_id = fixture["message_id"]
    message.message = [Plain(fixture["text"])]
    message.message_str = fixture["text"]
    event = AstrMessageEvent(
        fixture["text"],
        message,
        PlatformMetadata(
            name="notido-acceptance-fixture",
            description="Acceptance only",
            id="notido-acceptance-fixture",
        ),
        "provider-acceptance",
    )
    bridge = AstrBotBridge(SimpleNamespace(), "notido-local", "")
    envelope = bridge.normalize_event(event)
    service = Service(
        Path("/AstrBot/data/plugin_data/astrbot_plugin_notido"),
        bridge,
        DidaGateway(
            CLIRunner(
                "/usr/local/bin/node",
                "/opt/notido-cli/node_modules/@suibiji/dida-cli/dist/index.js",
                Path("/AstrBot/data/plugin_data/astrbot_plugin_notido/cli-home"),
            )
        ),
    )
    try:
        # Never initialize here: that would recover live claims belonging to the owner.
        value = await service.intake(envelope)
        print(json.dumps(value))
    finally:
        await service.db.close()


if __name__ == "__main__":
    asyncio.run(main())
