from __future__ import annotations

import argparse
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

import peers_bridge as plugin
from peers_bridge import schemas
from peers_bridge.bridge import ClaudePeersBridge


class FakeContext:
    def __init__(self) -> None:
        self.tools = {}
        self.injected = []

    def inject_message(self, content, role="system"):
        self.injected.append((role, content))
        return True

    def register_tool(self, *, name, **kwargs):
        self.tools[name] = kwargs

    def register_cli_command(self, **kwargs):
        self.cli_command = kwargs


class BridgeAliasTests(unittest.TestCase):
    def make_bridge(self, alias: str = "A1") -> ClaudePeersBridge:
        env = {
            "CLAUDE_PEERS_NAME": alias,
            "CLAUDE_PEERS_BRIDGE_AUTOSTART": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            return ClaudePeersBridge(FakeContext())

    def test_registration_sends_configured_alias(self) -> None:
        bridge = self.make_bridge("A1")
        requests = []
        bridge._ensure_broker = lambda: None
        bridge._post_json = lambda path, body: requests.append((path, body)) or {
            "id": "peer-a1",
            "alias": "A1",
        }

        bridge._ensure_registered()

        self.assertEqual("/register", requests[0][0])
        self.assertEqual("A1", requests[0][1]["logical_name"])
        self.assertEqual("peer-a1", bridge.state.peer_id)

    def test_set_alias_updates_local_state_only_after_broker_success(self) -> None:
        bridge = self.make_bridge("Old")
        bridge.state.peer_id = "peer-a1"
        bridge._ensure_registered = lambda: None
        bridge._post_json = lambda path, body: {"ok": True, "alias": body["alias"].strip()}

        payload = json.loads(bridge.set_alias("  A1  "))

        self.assertEqual({"ok": True, "alias": "A1"}, payload)
        self.assertEqual("A1", bridge.state.logical_name)

        bridge._post_json = mock.Mock(side_effect=RuntimeError("alias_conflict"))
        with self.assertRaisesRegex(RuntimeError, "alias_conflict"):
            bridge.set_alias("Proxmox")
        self.assertEqual("A1", bridge.state.logical_name)

    def test_injected_message_shows_sender_alias_and_id(self) -> None:
        bridge = self.make_bridge()

        message = bridge._format_injected_message(
            {
                "from_id": "abc12345",
                "from_alias": "Proxmox",
                "from_kind": "peer",
                "sent_at": "2026-07-22T00:00:00Z",
                "text": "hello",
            }
        )

        self.assertIn("from_alias: Proxmox", message)
        self.assertIn("from_id: abc12345", message)
        self.assertIn("to_id=`Proxmox`", message)

    def test_alias_tool_is_registered(self) -> None:
        context = FakeContext()
        with mock.patch.dict(os.environ, {"CLAUDE_PEERS_BRIDGE_AUTOSTART": "0"}, clear=False):
            plugin.register(context)

        self.assertIn("claude_peers_set_alias", context.tools)
        schema = context.tools["claude_peers_set_alias"]["schema"]
        self.assertEqual(["alias"], schema["parameters"]["required"])
        self.assertIn("alias", schemas.CLAUDE_PEERS_SEND_MESSAGE["parameters"]["properties"]["to_id"]["description"])

    def test_cli_accepts_alias_command(self) -> None:
        parser = argparse.ArgumentParser()
        subparser = parser.add_subparsers().add_parser("claude-peers")
        plugin._setup_cli(subparser)

        args = parser.parse_args(["claude-peers", "alias", "A1"])

        self.assertEqual("alias", args.claude_peers_command)
        self.assertEqual(["A1"], args.alias)


if __name__ == "__main__":
    unittest.main()
