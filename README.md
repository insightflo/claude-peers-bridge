# claude-peers-bridge

Hermes Agent plugin for cross-network peer communication via claude-peers broker.

## Architecture

| Process | Broker Target |
|---------|--------------|
| Gateway agent (`_HERMES_GATEWAY=1`) | Remote hub (`CLAUDE_PEERS_BROKER_URL`) |
| CLI / TUI agent (`HERMES_INTERACTIVE=1`) | Local broker (`127.0.0.1:7899`) |
| Subagent (kanban/delegate) | Registration blocked |

## Gateway Plugin Setup

1. Copy all files to `~/.hermes/plugins/claude_peers_bridge/`
2. Add to `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - claude-peers-bridge
```

3. Add to `~/.hermes/.env`:

```bash
# Gateway-only: remote broker URL
CLAUDE_PEERS_BROKER_URL=https://your-broker.example.com
CLAUDE_PEERS_BROKER_AUTH=Bearer <hub-token>
# Unique human-readable peer alias
CLAUDE_PEERS_NAME=Mac
```

4. Restart Hermes gateway

## Human-Readable Peer Aliases

Set a stable alias at registration with `CLAUDE_PEERS_NAME`. Aliases are trimmed and unique across the hub using case-insensitive comparison, so `A1` and `a1` cannot belong to different peers.

Change the current peer alias later through the Hermes tool:

```text
claude_peers_set_alias(alias="A1")
```

Or through the plugin CLI:

```bash
hermes claude-peers alias A1
```

`claude_peers_list_peers` returns both `alias` and `id`. The existing send tool accepts either value:

```text
claude_peers_send_message(to_id="Proxmox", message="Please ACK this test.")
```

Peer IDs remain supported for backward compatibility. Incoming messages include both `from_alias` and `from_id`.

## Remote Hub Broker Setup

The upstream `claude-peers` broker assumes every peer PID belongs to the broker host. That is correct for a local broker, but a remote hub would wrongly delete Mac/Proxmox peers because their PIDs do not exist on the hub machine.

Use `broker.remote-hub.ts` for the central remote hub:

1. Install the upstream [`claude-peers-mcp`](https://github.com/louislva/claude-peers-mcp) repository.
2. Copy `broker.remote-hub.ts` over that repository's root `broker.ts`.
3. Configure the broker service with:

```bash
CLAUDE_PEERS_PORT=7899
CLAUDE_PEERS_DB=/var/lib/claude-peers/peers.db
CLAUDE_PEERS_HUB_TOKEN=<64-char-random-token>
# Optional; default is 90000 ms.
CLAUDE_PEERS_STALE_TIMEOUT_MS=90000
```

4. Restart the broker service and verify the remote peer count:

```bash
curl -H "Authorization: Bearer $CLAUDE_PEERS_HUB_TOKEN" \
  https://your-broker.example.com/health
```

Do not commit or share the real hub token. Store it in a protected environment file (`chmod 600`).

## Key Design Decisions

- **Broker resolved at loop time, not import time**: Plugin modules are imported early (via `model_tools.py` side-effect), before `_HERMES_GATEWAY=1` is set. The `_run_loop` re-resolves the broker target every iteration, so the gateway automatically switches to the remote broker once the env is set.

- **User-Agent header**: Cloudflare tunnels block Python's default `Python-urllib/x.x` User-Agent. All HTTP requests include `User-Agent: hermes-peers-bridge/1.0`.

- **Subagent exclusion**: Processes with `HERMES_KANBAN_TASK` or `HERMES_DELEGATE_CHILD=1` env vars skip peer registration.

- **Remote peer liveness**: The remote hub uses `last_seen` heartbeat age instead of checking peer PIDs on the hub host. Missing/deleted peer IDs receive `reregister: true`, allowing the bridge to recover automatically.

- **Retry deduplication**: Remote registrations with the same PID and working directory replace the stale row created by the same running gateway after a transient broker outage.

- **Alias identity**: Human-readable aliases are stored separately from work summaries, enforced case-insensitively as unique, and resolved to canonical peer IDs before message persistence.
