# hermes-peers-bridge

**English** | [한국어](README.ko.md)

**Cross-network peer messaging for [Hermes Agent](https://hermes-agent.nousresearch.com) — let AI agents on different machines talk to each other.**

A Hermes Agent plugin that connects multiple Hermes instances (across different machines and networks) through a central [claude-peers](https://github.com/louislva/claude-peers-mcp) broker hub. Each agent registers as a named peer, and agents can send messages to each other by alias — a gateway agent on a cloud VM can ask the agent on your laptop to run a local task, and get the answer back as a normal conversation turn.

```
┌─────────┐      ┌──────────────────┐      ┌─────────┐
│ Agent A │◄────►│   Remote Hub     │◄────►│ Agent B │
│ (laptop)│      │ (broker, HTTPS)  │      │ (VM)    │
└─────────┘      └──────────────────┘      └─────────┘
                        ▲
                        │
                  ┌─────────┐
                  │ Agent C │
                  │ (server)│
                  └─────────┘
```

## Who is this for?

- You run **Hermes Agent on more than one machine** (e.g. a laptop, a home server, a cloud VM) and want the agents to coordinate: delegate work, ask questions, report status to each other.
- You want **agent-to-agent messaging across networks** without exposing each machine — only the hub needs a public endpoint.
- You already use the local `claude-peers` broker for same-machine peers and want to extend it across hosts.

Requirements: Hermes Agent with plugin support, Python 3.10+, and one host that can run the broker (Bun/Node + a public HTTPS endpoint, e.g. behind a reverse proxy or Cloudflare Tunnel).

## Features

- **Named peers** — human-readable aliases (`Laptop`, `HomeServer`, `CloudVM`) instead of opaque IDs; send by alias or ID.
- **Gateway & CLI aware** — gateway processes register against the remote hub; interactive CLI/TUI sessions use the local broker. Subagents are excluded automatically.
- **Real message delivery** — incoming peer messages are injected into the gateway's home-channel session as a normal agent turn, so the receiving agent actually reads and answers them (works on both current and older Hermes gateway builds).
- **Duplicate-safe** — message-ID deduplication and hub-aware delivery marking prevent the same message from being processed twice.
- **Self-healing registration** — heartbeat-based liveness, automatic re-registration after broker restarts or transient outages.

## Installation

### 1. Set up the hub broker (one machine)

The upstream `claude-peers` broker assumes all peers live on the broker host (it checks PIDs locally). For a cross-network hub, use the patched `broker.remote-hub.ts` from this repo, which uses heartbeat age instead:

```bash
git clone https://github.com/louislva/claude-peers-mcp
cd claude-peers-mcp
# replace the stock broker with the remote-hub variant
curl -sL https://raw.githubusercontent.com/insightflo/hermes-peers-bridge/main/broker.remote-hub.ts \
  -o broker.ts
```

Configure and run it as a service (systemd, etc.):

```bash
CLAUDE_PEERS_PORT=7899
CLAUDE_PEERS_DB=/var/lib/claude-peers/peers.db
CLAUDE_PEERS_HUB_TOKEN=<generate-a-long-random-token>
# Optional; peers older than this (ms) are considered stale. Default 90000.
CLAUDE_PEERS_STALE_TIMEOUT_MS=90000
```

Expose port 7899 via HTTPS (reverse proxy / Cloudflare Tunnel), then verify:

```bash
curl -H "Authorization: Bearer $CLAUDE_PEERS_HUB_TOKEN" \
  https://your-broker.example.com/health
# → {"status":"ok","peers":0}
```

> Keep the hub token secret. Store it in a `chmod 600` env file; never commit it.

### 2. Install the plugin (every Hermes machine)

Copy the plugin files into the Hermes plugins directory:

```bash
mkdir -p ~/.hermes/plugins/claude_peers_bridge
cd ~/.hermes/plugins/claude_peers_bridge
for f in __init__.py bridge.py schemas.py plugin.yaml; do
  curl -sLO https://raw.githubusercontent.com/insightflo/hermes-peers-bridge/main/$f
done
python3 -m py_compile bridge.py && echo OK
```

Enable it in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - claude-peers-bridge
```

Configure `~/.hermes/.env`:

```bash
# Remote hub (used by gateway processes)
CLAUDE_PEERS_BROKER_URL=https://your-broker.example.com
CLAUDE_PEERS_BROKER_AUTH=Bearer <hub-token>
# Unique, human-readable name for THIS machine's peer
CLAUDE_PEERS_NAME=Laptop
```

### 3. Wire up message reception (gateway machines)

Two settings decide whether the receiving agent can actually **read and reply** to peer messages:

**a) Home channel** — incoming peer messages are injected into the gateway's home-channel session. Set the home channel for whichever platform your gateway uses, in `~/.hermes/.env`:

```bash
TELEGRAM_HOME_CHANNEL=<chat-id>     # if the gateway runs Telegram
# or
SLACK_HOME_CHANNEL=<dm-channel-id>  # if the gateway runs Slack
```

**b) Toolset** — the receiving agent needs the `claude-peers` tools to reply. In `~/.hermes/config.yaml`, add `claude-peers` to the toolset list of that platform:

```yaml
platform_toolsets:
  slack:            # or telegram, discord, ...
    - claude-peers
    # ...existing entries...
```

Without (b), the agent will receive messages but silently fail to answer — it has no send tool in that session.

### 4. Restart and verify

```bash
hermes gateway restart          # or: systemctl --user restart hermes-gateway
```

From any agent, list peers and send a round-trip test:

```text
claude_peers_list_peers()
claude_peers_send_message(to_id="CloudVM", message="ping — please ACK")
```

The remote agent should receive the message in its home-channel session and reply within its next poll cycle (a few seconds).

## Usage

Agent tools provided by the plugin:

| Tool | Purpose |
|------|---------|
| `claude_peers_list_peers` | List live peers (alias, id, summary, last-seen) |
| `claude_peers_send_message` | Send a message to a peer by alias or id |
| `claude_peers_check_messages` | Manually poll for unread messages |
| `claude_peers_set_alias` | Rename this peer |
| `claude_peers_set_summary` | Update this peer's "what I'm working on" summary |
| `claude_peers_bridge_status` | Show registration state, broker URL, polling status |

Aliases are 1–64 ASCII characters (letters, digits, spaces, `.`, `_`, `@`, `-`), unique case-insensitively across the hub. Incoming messages carry both `from_alias` and `from_id`.

## Architecture notes

| Process | Broker target |
|---------|--------------|
| Gateway agent (`_HERMES_GATEWAY=1`) | Remote hub (`CLAUDE_PEERS_BROKER_URL`) |
| CLI / TUI agent (`HERMES_INTERACTIVE=1`) | Local broker (`127.0.0.1:7899`) |
| Subagent (kanban / delegate) | Registration skipped |

- **Broker resolved per loop iteration, not at import** — plugin modules import before gateway env vars are set; re-resolving each cycle lets the gateway pick up the remote hub automatically.
- **Gateway injection with fallback** — received messages are delivered via the gateway wake mechanism; on older Hermes builds without `gateway.wake`, the bridge falls back to injecting a synthetic message event. Either way the message becomes a real agent turn.
- **Hub-aware delivery marking** — the remote hub marks messages delivered at poll time and has no `/mark-message-delivered` endpoint; the bridge treats that 404 as already-delivered instead of re-queuing (prevents infinite re-injection).
- **Custom User-Agent** — Cloudflare blocks Python's default urllib UA; all requests send `User-Agent: hermes-peers-bridge/1.0`.
- **Liveness by heartbeat** — the hub uses `last_seen` age instead of host-local PID checks; dead registrations get `reregister: true` so bridges recover on their own.

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|-------------------|
| Send reports `ok: true` but no reply ever comes | Receiving machine runs an old `bridge.py` (no injection fallback) → update the plugin there and restart its gateway |
| Peer receives messages but never replies | `claude-peers` missing from that platform's `platform_toolsets` on the receiving machine |
| Messages not injected at all | Missing `*_HOME_CHANNEL` env var on the receiving gateway |
| `Peer X not found` when sending from CLI/TUI | CLI sessions talk to the **local** broker; remote peers are only visible to gateway sessions. Route via the gateway peer, or send from a gateway session |
| Same message injected repeatedly | Old `bridge.py` without 404-tolerant delivery marking → update the plugin |
| Broker rejects requests behind Cloudflare | Ensure plugin version sends the custom User-Agent header |

## License

MIT (plugin code). The broker builds on [claude-peers-mcp](https://github.com/louislva/claude-peers-mcp) — see that repository for its license.
