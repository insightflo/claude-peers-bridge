"""Hermes plugin bridge for the local claude-peers broker."""

from __future__ import annotations

import json

from .bridge import ClaudePeersBridge
from .schemas import (
    CLAUDE_PEERS_BRIDGE_STATUS,
    CLAUDE_PEERS_CHECK_MESSAGES,
    CLAUDE_PEERS_LIST_PEERS,
    CLAUDE_PEERS_SEND_MESSAGE,
    CLAUDE_PEERS_SET_ALIAS,
    CLAUDE_PEERS_SET_SUMMARY,
)

# Keep plugin tools in their own toolset. Registering them under an existing
# static toolset such as "hermes-cli" makes them invisible whenever a platform
# uses an explicit platform_toolsets list, because static toolset resolution only
# expands the built-in tool list. The dedicated toolset can be enabled for CLI,
# TUI, Telegram, or other gateways independently.
CLAUDE_PEERS_TOOLSET = "claude-peers"

_BRIDGE: ClaudePeersBridge | None = None



def _bridge() -> ClaudePeersBridge:
    if _BRIDGE is None:
        raise RuntimeError("claude-peers bridge is not initialized")
    return _BRIDGE


def _handle_list_peers(args, **kwargs):
    scope = args.get("scope", "machine")
    return _bridge().list_peers(scope=scope)


def _handle_send_message(args, **kwargs):
    return _bridge().send_message(to_id=args["to_id"], message=args["message"])


def _handle_check_messages(args, **kwargs):
    return _bridge().check_messages()


def _handle_set_summary(args, **kwargs):
    return _bridge().set_summary(summary=args["summary"])


def _handle_set_alias(args, **kwargs):
    return _bridge().set_alias(alias=args["alias"])


def _handle_status(args, **kwargs):
    return json.dumps(_bridge().status_payload(), ensure_ascii=False)


def _cli_handler(args):
    bridge = _bridge()
    command = getattr(args, "claude_peers_command", None)
    if command == "start":
        bridge.start()
        print("claude-peers bridge started")
    elif command == "stop":
        bridge.stop()
        print("claude-peers bridge stopped")
    elif command == "status":
        print(json.dumps(bridge.status_payload(), indent=2, ensure_ascii=False))
    elif command == "check":
        print(bridge.check_messages())
    elif command == "list":
        print(bridge.list_peers(scope=args.scope))
    elif command == "send":
        print(bridge.send_message(to_id=args.to_id, message=" ".join(args.message)))
    elif command == "summary":
        print(bridge.set_summary(summary=" ".join(args.summary)))
    elif command == "alias":
        print(bridge.set_alias(alias=" ".join(args.alias)))
    else:
        print("usage: hermes claude-peers <start|stop|status|check|list|send|summary|alias>")


def _setup_cli(subparser):
    subs = subparser.add_subparsers(dest="claude_peers_command")
    subs.add_parser("start", help="Start background claude-peers polling")
    subs.add_parser("stop", help="Stop background claude-peers polling")
    subs.add_parser("status", help="Show bridge status")
    subs.add_parser("check", help="Manually poll unread broker messages")

    list_parser = subs.add_parser("list", help="List broker peers")
    list_parser.add_argument(
        "--scope",
        choices=["machine", "directory", "repo"],
        default="machine",
        help="Peer discovery scope",
    )

    send_parser = subs.add_parser("send", help="Send a message to a peer id")
    send_parser.add_argument("to_id", help="Target peer id")
    send_parser.add_argument("message", nargs="+", help="Message text")

    summary_parser = subs.add_parser("summary", help="Set this peer summary")
    summary_parser.add_argument("summary", nargs="+", help="Summary text")

    alias_parser = subs.add_parser("alias", help="Set this peer's unique human-readable alias")
    alias_parser.add_argument("alias", nargs="+", help="Unique peer alias")

    subparser.set_defaults(func=_cli_handler)


def register(ctx):
    global _BRIDGE
    _BRIDGE = ClaudePeersBridge(ctx)

    ctx.register_tool(
        name="claude_peers_list_peers",
        toolset=CLAUDE_PEERS_TOOLSET,
        schema=CLAUDE_PEERS_LIST_PEERS,
        handler=_handle_list_peers,
        description="List live claude-peers peers",
    )
    ctx.register_tool(
        name="claude_peers_send_message",
        toolset=CLAUDE_PEERS_TOOLSET,
        schema=CLAUDE_PEERS_SEND_MESSAGE,
        handler=_handle_send_message,
        description="Send a claude-peers broker message",
    )
    ctx.register_tool(
        name="claude_peers_check_messages",
        toolset=CLAUDE_PEERS_TOOLSET,
        schema=CLAUDE_PEERS_CHECK_MESSAGES,
        handler=_handle_check_messages,
        description="Manually poll unread claude-peers messages",
    )
    ctx.register_tool(
        name="claude_peers_set_summary",
        toolset=CLAUDE_PEERS_TOOLSET,
        schema=CLAUDE_PEERS_SET_SUMMARY,
        handler=_handle_set_summary,
        description="Update the Hermes peer summary",
    )
    ctx.register_tool(
        name="claude_peers_set_alias",
        toolset=CLAUDE_PEERS_TOOLSET,
        schema=CLAUDE_PEERS_SET_ALIAS,
        handler=_handle_set_alias,
        description="Set the unique human-readable alias for this peer",
    )
    ctx.register_tool(
        name="claude_peers_bridge_status",
        toolset=CLAUDE_PEERS_TOOLSET,
        schema=CLAUDE_PEERS_BRIDGE_STATUS,
        handler=_handle_status,
        description="Show claude-peers bridge status",
    )
    ctx.register_cli_command(
        name="claude-peers",
        help="Manage the claude-peers Hermes bridge",
        setup_fn=_setup_cli,
        handler_fn=_cli_handler,
        description="Background claude-peers bridge for Hermes",
    )

    if _BRIDGE.state.autostart:
        _BRIDGE.start()
