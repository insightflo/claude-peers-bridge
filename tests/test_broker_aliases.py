from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BROKER_PATH = REPO_ROOT / "broker.remote-hub.ts"
TOKEN = "test-hub-token"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _create_legacy_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE peers (
          id TEXT PRIMARY KEY,
          pid INTEGER NOT NULL,
          cwd TEXT NOT NULL,
          git_root TEXT,
          tty TEXT,
          summary TEXT NOT NULL DEFAULT '',
          registered_at TEXT NOT NULL,
          last_seen TEXT NOT NULL
        );
        CREATE TABLE messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          from_id TEXT NOT NULL,
          to_id TEXT NOT NULL,
          text TEXT NOT NULL,
          sent_at TEXT NOT NULL,
          delivered INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    db.close()


class BrokerAliasTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "peers.db"
        _create_legacy_db(self.db_path)
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        env = os.environ.copy()
        env.update(
            {
                "CLAUDE_PEERS_PORT": str(self.port),
                "CLAUDE_PEERS_DB": str(self.db_path),
                "CLAUDE_PEERS_HUB_TOKEN": TOKEN,
                "CLAUDE_PEERS_STALE_TIMEOUT_MS": "60000",
            }
        )
        self.process = subprocess.Popen(
            ["bun", str(BROKER_PATH)],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.time() + 5
        while time.time() < deadline:
            if self.process.poll() is not None:
                stdout, stderr = self.process.communicate()
                self.fail(f"broker exited early\nstdout={stdout}\nstderr={stderr}")
            try:
                with urllib.request.urlopen(f"{self.base_url}/health", timeout=0.2):
                    break
            except Exception:
                time.sleep(0.05)
        else:
            self.fail("broker did not become healthy")

    def tearDown(self) -> None:
        self.process.terminate()
        try:
            self.process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.communicate(timeout=2)
        self.tempdir.cleanup()

    def post(self, path: str, body: dict, expected_status: int = 200):
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                status = response.status
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            status = exc.code
            payload = json.loads(exc.read().decode())
        self.assertEqual(expected_status, status, payload)
        return payload

    def register(self, alias: str, *, pid: int, cwd: str) -> str:
        payload = self.post(
            "/register",
            {
                "pid": pid,
                "parent_pid": 1,
                "logical_name": alias,
                "cwd": cwd,
                "git_root": None,
                "tty": None,
                "summary": "Hermes Agent",
            },
        )
        return payload["id"]

    def list_peers(self):
        return self.post(
            "/list-peers",
            {"scope": "machine", "cwd": "/", "git_root": None, "exclude_id": None},
        )

    def test_legacy_schema_is_migrated(self) -> None:
        db = sqlite3.connect(self.db_path)
        peer_columns = {row[1] for row in db.execute("PRAGMA table_info(peers)")}
        message_columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
        db.close()

        self.assertIn("alias", peer_columns)
        self.assertIn("from_alias", message_columns)

    def test_registration_lists_alias_and_rejects_case_insensitive_duplicate(self) -> None:
        peer_id = self.register("A1", pid=101, cwd="/home/a1")

        peers = self.list_peers()
        self.assertEqual("A1", peers[0]["alias"])
        self.assertEqual(peer_id, peers[0]["id"])

        conflict = self.post(
            "/register",
            {
                "pid": 202,
                "logical_name": "a1",
                "cwd": "/root/proxmox",
                "git_root": None,
                "tty": None,
                "summary": "Hermes Agent",
            },
            expected_status=409,
        )
        self.assertEqual("alias_conflict", conflict["error"])
        self.assertEqual(1, len(self.list_peers()))

    def test_runtime_alias_change_preserves_current_alias_on_conflict(self) -> None:
        a1_id = self.register("A1", pid=101, cwd="/home/a1")
        proxmox_id = self.register("Proxmox", pid=202, cwd="/root/proxmox")

        renamed = self.post("/set-alias", {"id": a1_id, "alias": "Controller"})
        self.assertEqual({"ok": True, "alias": "Controller"}, renamed)

        conflict = self.post(
            "/set-alias",
            {"id": proxmox_id, "alias": "controller"},
            expected_status=409,
        )
        self.assertEqual("alias_conflict", conflict["error"])
        aliases = {peer["id"]: peer["alias"] for peer in self.list_peers()}
        self.assertEqual("Controller", aliases[a1_id])
        self.assertEqual("Proxmox", aliases[proxmox_id])

    def test_send_accepts_alias_or_id_and_poll_includes_sender_alias(self) -> None:
        a1_id = self.register("A1", pid=101, cwd="/home/a1")
        proxmox_id = self.register("Proxmox", pid=202, cwd="/root/proxmox")

        sent = self.post(
            "/send-message",
            {"from_id": a1_id, "from_pid": 101, "to_id": "proxmox", "text": "alias route"},
        )
        self.assertEqual(proxmox_id, sent["to_id"])
        self.assertEqual("Proxmox", sent["to_alias"])

        inbox = self.post("/poll-messages", {"id": proxmox_id})["messages"]
        self.assertEqual(1, len(inbox))
        self.assertEqual(a1_id, inbox[0]["from_id"])
        self.assertEqual("A1", inbox[0]["from_alias"])
        self.assertEqual(proxmox_id, inbox[0]["to_id"])

        sent_by_id = self.post(
            "/send-message",
            {"from_id": proxmox_id, "from_pid": 202, "to_id": a1_id, "text": "id route"},
        )
        self.assertEqual(a1_id, sent_by_id["to_id"])
        self.assertEqual("A1", sent_by_id["to_alias"])


if __name__ == "__main__":
    unittest.main()
