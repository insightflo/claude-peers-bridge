"""Background bridge between Hermes and the claude-peers broker."""

from __future__ import annotations

import atexit
import json
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BROKER_PORT = int(os.environ.get("CLAUDE_PEERS_PORT", "7899"))

# Broker target is decided at __init__ time, not module-import time,
# because plugin modules can be imported before _HERMES_GATEWAY is set.
DEFAULT_POLL_INTERVAL = float(os.environ.get("CLAUDE_PEERS_BRIDGE_POLL_INTERVAL_SEC", "1.0"))
DEFAULT_HEARTBEAT_INTERVAL = float(
    os.environ.get("CLAUDE_PEERS_BRIDGE_HEARTBEAT_INTERVAL_SEC", "15.0")
)
DEFAULT_SUMMARY = os.environ.get("CLAUDE_PEERS_SUMMARY", "Hermes Agent")


def _resolve_broker():
    """Return (broker_url, is_remote, broker_auth) based on current env.

    Gateway agents connect to the REMOTE broker (cross-network hub).
    CLI agents connect to the LOCAL broker (intra-network peers).
    """
    is_gateway = os.environ.get("_HERMES_GATEWAY") == "1"
    remote_url = os.environ.get("CLAUDE_PEERS_BROKER_URL")
    if is_gateway and remote_url:
        return remote_url, True, os.environ.get("CLAUDE_PEERS_BROKER_AUTH")
    return f"http://127.0.0.1:{DEFAULT_BROKER_PORT}", False, None


def _is_subagent() -> bool:
    """Return True if this process is a kanban worker, delegate child, or
    other subagent that should NOT register as a peer.

    Only gateway agents and user-launched CLI agents should register.
    """
    if os.environ.get("HERMES_KANBAN_TASK"):
        return True
    if os.environ.get("HERMES_DELEGATE_CHILD") == "1":
        return True
    return False


def _json_response(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _git_root(cwd: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    result = proc.stdout.strip()
    return result or None


def _tty_name() -> str | None:
    for fd in (0, 1, 2):
        try:
            return os.ttyname(fd)
        except OSError:
            continue
    return None


def _logical_name(prefix: str | None) -> str | None:
    explicit = os.environ.get("CLAUDE_PEERS_NAME")
    if explicit:
        return explicit.strip() or None

    prefix = (prefix or os.environ.get("CLAUDE_PEERS_NAME_PREFIX") or "hermes").strip()
    if not prefix:
        return None

    surface = os.environ.get("CMUX_SURFACE_ID")
    if surface:
        suffix = surface.strip().lower()[:8]
    else:
        tty = _tty_name()
        if tty:
            suffix = Path(tty).name.lower()
        else:
            suffix = str(os.getpid())
    return f"{prefix}@{suffix}"


def _find_broker_script() -> Path | None:
    env_home = os.environ.get("CLAUDE_PEERS_HOME")
    candidates = []
    if env_home:
        candidates.append(Path(env_home) / "broker.ts")
    candidates.append(Path.home() / ".local" / "share" / "claude-peers-mcp" / "broker.ts")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


@dataclass
class BrokerPeerState:
    peer_id: str | None = None
    logical_name: str | None = None
    summary: str = DEFAULT_SUMMARY
    broker_url: str = ""
    is_remote: bool = False
    broker_auth: str | None = None
    running: bool = False
    autostart: bool = True
    last_error: str | None = None


class ClaudePeersBridge:
    """Register Hermes as a claude-peers peer and inject incoming messages."""

    def __init__(self, ctx):
        self._ctx = ctx
        # Subagents (kanban workers, delegate children) must not register as peers.
        is_sub = _is_subagent()
        # Resolve broker target at init time (not module-import time).
        broker_url, is_remote, broker_auth = _resolve_broker()
        self._state = BrokerPeerState(
            logical_name=_logical_name(os.environ.get("CLAUDE_PEERS_NAME_PREFIX") or "hermes"),
            broker_url=broker_url,
            is_remote=is_remote,
            broker_auth=broker_auth,
            autostart=(not is_sub) and (os.environ.get("CLAUDE_PEERS_BRIDGE_AUTOSTART", "1") != "0"),
        )
        if is_sub:
            self._state.last_error = "subagent detected (HERMES_KANBAN_TASK/HERMES_DELEGATE_CHILD) — peer registration skipped"
        self._cwd = os.getcwd()
        self._git_root = _git_root(self._cwd)
        self._tty = _tty_name()
        self._pending_injections: "queue.Queue[tuple[int | None, str]]" = queue.Queue()
        # Dedupe guard: poll runs with mark_delivered=False, so the same
        # undelivered message comes back every iteration until injection
        # succeeds. Without this set the pending queue grows one duplicate
        # per poll tick.
        self._queued_message_ids: set[int] = set()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        atexit.register(self.stop)

    @property
    def state(self) -> BrokerPeerState:
        return self._state

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._state.running = True
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="claude-peers-bridge",
                daemon=True,
            )
            self._thread.start()
            self._state.running = True

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            thread = self._thread
            self._thread = None
            self._state.running = False
        if thread and thread.is_alive():
            thread.join(timeout=1.0)
        try:
            self._unregister()
        except Exception:
            pass

    def status_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "running": self._state.running,
            "peer_id": self._state.peer_id,
            "logical_name": self._state.logical_name,
            "summary": self._state.summary,
            "broker_url": self._state.broker_url,
            "is_remote": self._state.is_remote,
            "cwd": self._cwd,
            "git_root": self._git_root,
            "tty": self._tty,
            "last_error": self._state.last_error,
            "autostart": self._state.autostart,
        }

    def list_peers(self, scope: str = "machine") -> str:
        self._ensure_registered()
        payload = self._post_json(
            "/list-peers",
            {
                "scope": scope,
                "cwd": self._cwd,
                "git_root": self._git_root,
                "exclude_id": self._state.peer_id,
            },
        )
        return _json_response({"ok": True, "scope": scope, "peers": payload})

    def send_message(self, to_id: str, message: str) -> str:
        self._ensure_registered()
        payload = self._post_json(
            "/send-message",
            {
                "from_id": self._state.peer_id,
                "from_pid": os.getpid(),
                "to_id": to_id,
                "text": message,
            },
        )
        return _json_response(payload)

    def check_messages(self) -> str:
        self._ensure_registered()
        payload = self._poll_messages()
        return _json_response({"ok": True, "messages": payload})

    def set_summary(self, summary: str) -> str:
        self._state.summary = summary
        self._ensure_registered()
        payload = self._post_json(
            "/set-summary",
            {
                "id": self._state.peer_id,
                "summary": summary,
            },
        )
        return _json_response(payload)

    def set_alias(self, alias: str) -> str:
        self._ensure_registered()
        payload = self._post_json(
            "/set-alias",
            {
                "id": self._state.peer_id,
                "alias": alias,
            },
        )
        # Update local state only after the broker accepts and normalizes it.
        accepted_alias = payload.get("alias")
        if isinstance(accepted_alias, str):
            self._state.logical_name = accepted_alias
        return _json_response(payload)

    def _run_loop(self) -> None:
        last_heartbeat = 0.0
        while not self._stop_event.is_set():
            try:
                # Re-resolve broker target each iteration — _HERMES_GATEWAY
                # may be set after this thread starts (plugin discovery via
                # model_tools.py import runs before gateway sets it).
                new_url, new_remote, new_auth = _resolve_broker()
                if new_url != self._state.broker_url:
                    if self._state.peer_id:
                        self._unregister()
                    self._state.broker_url = new_url
                    self._state.is_remote = new_remote
                    self._state.broker_auth = new_auth
                self._ensure_registered()
                now = time.time()
                if now - last_heartbeat >= DEFAULT_HEARTBEAT_INTERVAL:
                    self._heartbeat()
                    last_heartbeat = now
                self._drain_pending_injections()
                messages = self._poll_messages(mark_delivered=False)
                if messages:
                    for message in messages:
                        self._queue_injection(
                            self._format_injected_message(message),
                            message_id=message.get("id"),
                        )
                    self._drain_pending_injections()
                self._state.last_error = None
            except Exception as exc:
                self._state.last_error = str(exc)
            self._stop_event.wait(DEFAULT_POLL_INTERVAL)

    def _queue_injection(self, content: str, message_id: int | None = None) -> None:
        if message_id is not None:
            if message_id in self._queued_message_ids:
                return
            self._queued_message_ids.add(message_id)
        self._pending_injections.put((message_id, content))

    def _drain_pending_injections(self) -> None:
        while True:
            try:
                message_id, content = self._pending_injections.get_nowait()
            except queue.Empty:
                return
            injected = False
            try:
                injected = bool(self._ctx.inject_message(content, role="system"))
            except Exception:
                injected = False
            if not injected:
                # Gateway mode: ctx.inject_message requires _cli_ref, which is
                # None in the gateway process, so incoming peer messages were
                # silently dropped. Route them through the gateway's own wake
                # path (home channel) instead.
                injected = self._inject_via_gateway(content)
            if not injected:
                self._pending_injections.put((message_id, content))
                return
            if message_id is not None:
                try:
                    self._mark_message_delivered(message_id)
                except Exception as exc:
                    self._state.last_error = f"mark delivered failed for message {message_id}: {exc}"
                    self._pending_injections.put((message_id, content))
                    return
                self._queued_message_ids.discard(message_id)

    def _inject_via_gateway(self, content: str) -> bool:
        """Deliver an incoming peer message inside a gateway process.

        ``ctx.inject_message`` only works in interactive CLI sessions
        (``_cli_ref``). In the gateway there is no CLI, so we wake the home
        channel session via the same ``deliver_wake`` path the kanban
        notifier uses: schedule a synthetic internal MessageEvent on the
        gateway event loop. The agent then sees the peer message and can
        reply with ``claude_peers_send_message``.

        Returns True once the wake turn was scheduled on the loop.
        """
        import asyncio

        try:
            from gateway.run import _gateway_runner_ref, _home_target_env_var, _home_thread_env_var
            from gateway.session import SessionSource
        except Exception:
            return False
        # gateway.wake only exists in newer Hermes builds. Fall back to the
        # synthetic-MessageEvent path (what deliver_wake does for push
        # adapters) so older gateways (e.g. A1) still inject peer messages.
        try:
            from gateway.wake import adapter_supports_push, deliver_wake
        except Exception:
            deliver_wake = None

            def adapter_supports_push(adapter):
                return bool(getattr(adapter, "supports_async_delivery", True))

        runner = None
        try:
            runner = _gateway_runner_ref()
        except Exception:
            runner = None
        if runner is None:
            return False
        loop = getattr(runner, "_gateway_loop", None)
        if loop is None or loop.is_closed():
            return False
        adapters = getattr(runner, "adapters", {}) or {}
        for platform, adapter in adapters.items():
            if adapter is None or not adapter_supports_push(adapter):
                continue
            platform_name = platform.value if hasattr(platform, "value") else str(platform)
            try:
                chat_id = (os.environ.get(_home_target_env_var(platform_name)) or "").strip()
            except Exception:
                chat_id = ""
            if not chat_id:
                continue
            try:
                thread_id = (os.environ.get(_home_thread_env_var(platform_name)) or "").strip() or None
            except Exception:
                thread_id = None
            source = SessionSource(
                platform=platform,
                chat_id=chat_id,
                chat_type="private",
                thread_id=thread_id,
            )
            try:
                if deliver_wake is not None:
                    _coro = deliver_wake(adapter, text=content, source=source)
                else:
                    from gateway.platforms.base import MessageEvent, MessageType

                    _coro = adapter.handle_message(
                        MessageEvent(
                            text=content,
                            message_type=MessageType.TEXT,
                            source=source,
                            internal=True,
                        )
                    )
                future = asyncio.run_coroutine_threadsafe(_coro, loop)
            except Exception as exc:
                self._state.last_error = f"gateway wake scheduling failed: {exc}"
                continue

            def _log_wake_result(fut) -> None:
                try:
                    fut.result()
                except Exception as exc:  # pragma: no cover - best-effort logging
                    self._state.last_error = f"gateway wake turn failed: {exc}"

            future.add_done_callback(_log_wake_result)
            return True
        self._state.last_error = (
            "gateway injection failed: no push-capable adapter with a configured home channel"
        )
        return False

    def _format_injected_message(self, message: dict[str, Any]) -> str:
        from_id = message.get("from_id", "unknown")
        from_alias = message.get("from_alias") or from_id
        from_kind = message.get("from_kind", "peer")
        sent_at = message.get("sent_at", "")
        reply_note = (
            f"If a reply is needed, use the tool `claude_peers_send_message` with to_id=`{from_id}`."
            if from_kind == "peer"
            else "This sender is marked as system-only. Do not reply through peer messaging."
        )
        return (
            "[claude-peers] New incoming message\n"
            f"from_alias: {from_alias}\n"
            f"from_id: {from_id}\n"
            f"from_kind: {from_kind}\n"
            f"sent_at: {sent_at}\n\n"
            f"{message.get('text', '')}\n\n"
            f"{reply_note}"
        )

    def _ensure_registered(self) -> None:
        if self._state.peer_id:
            try:
                payload = self._post_json("/heartbeat", {"id": self._state.peer_id})
            except RuntimeError:
                # Broker may have restarted or evicted this row.  Fall through
                # to a fresh registration instead of keeping a stale peer id.
                self._state.peer_id = None
            else:
                if not payload.get("reregister"):
                    return
                self._state.peer_id = None

        self._ensure_broker()
        payload = self._post_json(
            "/register",
            {
                "pid": os.getpid(),
                "parent_pid": os.getppid(),
                "logical_name": self._state.logical_name,
                "cwd": self._cwd,
                "git_root": self._git_root,
                "tty": self._tty,
                "summary": self._state.summary,
            },
        )
        peer_id = payload.get("id")
        if not peer_id:
            raise RuntimeError(f"Broker register failed: {payload}")
        self._state.peer_id = peer_id
        registered_alias = payload.get("alias")
        if isinstance(registered_alias, str):
            self._state.logical_name = registered_alias

    def _heartbeat(self) -> None:
        if not self._state.peer_id:
            return
        payload = self._post_json("/heartbeat", {"id": self._state.peer_id})
        if payload.get("reregister"):
            self._state.peer_id = None
            self._ensure_registered()

    def _poll_messages(self, *, mark_delivered: bool = True) -> list[dict[str, Any]]:
        if not self._state.peer_id:
            return []
        payload = self._post_json(
            "/poll-messages",
            {"id": self._state.peer_id, "mark_delivered": mark_delivered},
        )
        if payload.get("reregister"):
            self._state.peer_id = None
            self._ensure_registered()
            return []
        return payload.get("messages", []) or []

    def _mark_message_delivered(self, message_id: int) -> None:
        try:
            self._post_json("/mark-message-delivered", {"id": message_id})
        except RuntimeError as exc:
            # The remote hub broker (broker.remote-hub.ts) marks messages
            # delivered at poll time and has no /mark-message-delivered
            # endpoint (HTTP 404). Treat that as already-delivered instead
            # of failing — otherwise the message is requeued and injected
            # repeatedly.
            if "404" in str(exc):
                return
            raise

    def _unregister(self) -> None:
        if not self._state.peer_id:
            return
        try:
            self._post_json("/unregister", {"id": self._state.peer_id})
        finally:
            self._state.peer_id = None

    def _ensure_broker(self) -> None:
        if self._health_ok():
            return
        if self._state.is_remote:
            raise RuntimeError(
                f"Remote broker at {self._state.broker_url} is not reachable. "
                "Check CLAUDE_PEERS_BROKER_URL and network/tunnel connectivity."
            )
        broker_script = _find_broker_script()
        if broker_script is None:
            raise RuntimeError(
                "claude-peers broker.ts not found. Set CLAUDE_PEERS_HOME or install claude-peers-mcp first."
            )
        try:
            subprocess.Popen(
                ["bun", str(broker_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("bun is required to launch the claude-peers broker") from exc

        deadline = time.time() + 3.0
        while time.time() < deadline:
            if self._health_ok():
                return
            time.sleep(0.1)
        raise RuntimeError(f"claude-peers broker did not become healthy at {self._state.broker_url}")

    def _health_ok(self) -> bool:
        try:
            req = urllib.request.Request(f"{self._state.broker_url}/health")
            req.add_header("User-Agent", "hermes-peers-bridge/1.0")
            if self._state.broker_auth:
                req.add_header("Authorization", self._state.broker_auth)
            with urllib.request.urlopen(req, timeout=3.0) as response:
                data = json.loads(response.read().decode("utf-8"))
            return data.get("status") == "ok"
        except Exception:
            return False

    def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "hermes-peers-bridge/1.0"}
        if self._state.broker_auth:
            headers["Authorization"] = self._state.broker_auth
        request = urllib.request.Request(
            f"{self._state.broker_url}{path}",
            data=data,
            headers=headers,
            method="POST",
        )
        timeout = 5.0 if self._state.is_remote else 2.0
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"broker {path} failed: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"broker {path} failed: {exc.reason}") from exc
