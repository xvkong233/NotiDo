"""Pinned AstrBot API/decorator contract; isolated DB, no real account or AI calls."""

import asyncio
import importlib.util
import json
import sys
import tempfile
import types
from pathlib import Path

from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.provider.register import llm_tools
from astrbot.core.star.filter.command import CommandFilter, GreedyStr

from tools.handler_contract_smoke import ContextFixture, event

PROJECT_ROOT = Path(__file__).resolve().parent.parent


async def main():
    root = PROJECT_ROOT
    package = types.ModuleType("notido_native_fixture")
    package.__path__ = [str(root)]
    sys.modules[package.__name__] = package
    spec = importlib.util.spec_from_file_location(package.__name__ + ".main", root / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    checks = []
    with tempfile.TemporaryDirectory() as temporary:
        context = ContextFixture()
        plugin = module.NotiDoPlugin(
            context, {"data_root": temporary, "instance_id": "fixture-main"}
        )
        await plugin.initialize()
        try:
            registered = [llm_tools.get_func(name) for name in module.TOOL_NAMES]
            checks.append(
                {
                    "check": "eleven_native_decorators_and_schemas",
                    "pass": len(registered) == 11
                    and all(
                        tool and tool.parameters.get("type") == "object" for tool in registered
                    ),
                }
            )
            checks.append(
                {
                    "check": "no_interceptor_or_independent_ai",
                    "pass": not hasattr(plugin, "on_message")
                    and not hasattr(plugin.service.bridge, "call_provider")
                    and not hasattr(plugin.service.bridge, "read_visual"),
                }
            )
            other = FunctionTool(
                name="framework_other", description="Framework-owned", parameters={"type": "object"}
            )
            unbound_request = ProviderRequest(
                system_prompt="AstrBot persona",
                contexts=[{"role": "user", "content": "memory context"}],
                func_tool=ToolSet(tools=[*registered, other]),
            )
            await plugin.on_llm_request(event("普通对话", "unbound"), unbound_request)
            checks.append(
                {
                    "check": "unbound_only_notido_tools_hidden",
                    "pass": len(unbound_request.func_tool.tools) == 1
                    and unbound_request.func_tool.tools[0].name == "framework_other"
                    and unbound_request.system_prompt == "AstrBot persona"
                    and not context.models,
                }
            )
            from notido.db import execute

            incoming = event("普通对话", "bound")
            identity = plugin.service.bridge.identity(incoming)
            settings, _ = await plugin.service.db.settings()
            settings.account_ref, settings.credential_generation, settings.min_free_bytes = (
                "fixture-account",
                1,
                0,
            )
            async with plugin.service.db.transaction() as conn:
                await execute(
                    conn,
                    "INSERT INTO account_scopes VALUES ('fixture-account','personal','cn',NULL,1,'active',0)",
                )
                await execute(
                    conn, "UPDATE settings SET payload=:p", {"p": settings.model_dump_json()}
                )
                await execute(
                    conn,
                    "INSERT INTO actor_bindings VALUES ('fixture','personal',:i,:a,:s,1,0)",
                    identity,
                )
            history = [{"role": "user", "content": "我在校学生，这份身份来自 AstrBot 的会话记忆"}]
            request = ProviderRequest(
                system_prompt="native persona and long-term memory",
                contexts=history,
                prompt="正常消息",
                func_tool=ToolSet(tools=[*registered, other]),
            )
            await plugin.on_llm_request(incoming, request)
            enriched = request.func_tool.get_tool("notido_create")
            original = next(t for t in registered if t.name == "notido_create")
            nested = enriched.parameters["properties"]["task"]
            checks.append(
                {
                    "check": "request_only_nested_native_schemas_preserve_handlers_and_other_tools",
                    "pass": enriched is not original
                    and enriched.handler is original.handler
                    and nested["additionalProperties"] is False
                    and "notice" in nested["properties"]
                    and request.func_tool.get_tool("framework_other") is other
                    and "notice"
                    not in original.parameters["properties"]["task"].get("properties", {}),
                }
            )
            checks.append(
                {
                    "check": "native_persona_history_tools_and_prompt_preserved",
                    "pass": request.contexts is history
                    and request.prompt == "正常消息"
                    and request.system_prompt.startswith("native persona and long-term memory")
                    and len(request.func_tool.tools) == len(registered) + 1
                    and not incoming.is_stopped()
                    and not context.models
                    and not await plugin.service.db.read("SELECT * FROM message_records"),
                }
            )
            command_filter = CommandFilter("notido")
            converted = command_filter.validate_and_convert_params(
                ["新建", '{"request_key":', '"事项1",', '"title":', '"提交', '报告"}'],
                {"arguments": GreedyStr},
            )

            class Gateway:
                async def projects(self):
                    return []

            plugin.service.gateway = Gateway()
            replies = [response async for response in plugin.notido_command(incoming, "清单")]
            checks.append(
                {
                    "check": "command_greedy_arguments_and_same_facade",
                    "pass": converted["arguments"].endswith('"提交 报告"}')
                    and len(replies) == 1
                    and not context.models,
                }
            )
            checks.append(
                {
                    "check": "authorized_function_returns_framework_result",
                    "pass": json.loads(await plugin.notido_projects(incoming))
                    == {"projects": [], "scope": "allowed_open_projects", "account_wide": False}
                    and not context.models
                    and not context.sent,
                }
            )
            from astrbot.api.message_components import Node, Plain

            forwarded = event("确认删除", "forwarded-confirmation")
            forwarded.message_obj.message = [
                Node(uin="other", name="other", content=[Plain("确认删除")])
            ]
            checks.append(
                {
                    "check": "only_top_level_user_text_can_confirm_delete",
                    "pass": plugin.service.bridge.confirmation_text(forwarded) == ""
                    and plugin.service.bridge.confirmation_text(
                        event("确认删除", "direct-confirmation")
                    )
                    == "确认删除",
                }
            )
        finally:
            await plugin.terminate()
    print(json.dumps(checks))
    if not all(check["pass"] for check in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
