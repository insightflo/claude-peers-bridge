# claude-peers-bridge

Hermes Agent plugin for cross-network peer communication via claude-peers broker.

## Architecture

| Process | Broker Target |
|---------|--------------|
| Gateway agent (`_HERMES_GATEWAY=1`) | Remote hub (`CLAUDE_PEERS_BROKER_URL`) |
| CLI / TUI agent (`HERMES_INTERACTIVE=1`) | Local broker (`127.0.0.1:7899`) |
| Subagent (kanban/delegate) | Registration blocked |

## Setup

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
```

4. Restart Hermes gateway

## Key Design Decisions

- **Broker resolved at loop time, not import time**: Plugin modules are imported early (via `model_tools.py` side-effect), before `_HERMES_GATEWAY=1` is set. The `_run_loop` re-resolves the broker target every iteration, so the gateway automatically switches to the remote broker once the env is set.

- **User-Agent header**: Cloudflare tunnels block Python's default `Python-urllib/x.x` User-Agent. All HTTP requests include `User-Agent: hermes-peers-bridge/1.0`.

- **Subagent exclusion**: Processes with `HERMES_KANBAN_TASK` or `HERMES_DELEGATE_CHILD=1` env vars skip peer registration.
