# Peer Alias Design

## Goal

Let humans identify and address claude-peers sessions by stable, readable aliases such as `Mac`, `A1`, and `Proxmox`, without removing existing peer-ID compatibility.

## Data model

Add a nullable `alias` column to the broker `peers` table. Existing databases are migrated on startup with an additive `ALTER TABLE` when the column is absent. Aliases are trimmed, non-empty, and unique across the hub using case-insensitive comparison.

The existing `summary` remains a description of current work; it is not reused as identity.

## Registration

The bridge sends its existing `logical_name` as the requested alias. `CLAUDE_PEERS_NAME` remains the explicit registration setting. If not configured, the bridge-generated `hermes@<surface-or-pid>` name is used.

Registration fails with a clear conflict response when another live peer already owns the same case-insensitive alias. A reconnect of the same PID and cwd may reclaim its own alias while replacing its stale row.

## Runtime alias changes

Add a broker endpoint `/set-alias`, a Hermes tool `claude_peers_set_alias(alias)`, and CLI command `hermes claude-peers alias <name>`. A successful change updates both broker state and the bridge's local status. Empty or duplicate aliases are rejected without changing the current alias.

## Discovery and messaging

`list-peers` returns each peer's `alias` along with its ID, summary, cwd, and liveness metadata.

`send-message` accepts either an exact peer ID or a case-insensitive alias in the existing `to_id` field. The broker resolves an alias to the canonical peer ID before storing the message. Missing targets return an explicit error. Alias uniqueness prevents ambiguous delivery.

Incoming messages include `from_alias`; injected Hermes context shows both the readable alias and peer ID. Replies continue to support IDs and may use aliases.

## Compatibility and rollout

Existing ID-based callers keep working. The broker is deployed first, then gateway plugins are updated. Existing peers without an alias remain addressable by ID until they re-register or set an alias.

Rollout order:

1. Deploy alias-aware broker on A1.
2. Update Mac and A1 gateway plugins and set `CLAUDE_PEERS_NAME`.
3. Ask the Proxmox peer to pull/update the plugin and set its alias.
4. Verify list output contains unique `A1` and `Proxmox` aliases.
5. Send A1 → Proxmox and Proxmox → A1 messages using aliases and verify receipt.

## Validation

Automated tests cover alias normalization, duplicate rejection, migration, registration, runtime rename, ID/alias target resolution, and sender alias propagation. Python tests cover registration payload, alias state update, tool schema/handler wiring, and incoming message formatting.

Runtime verification must prove broker health, three unique peer rows, and bidirectional A1/Proxmox delivery. No real hub token or machine-specific credential is committed.
