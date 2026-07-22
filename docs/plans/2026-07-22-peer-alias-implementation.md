# Peer Alias Implementation Plan

> **For Hermes:** Execute task-by-task with strict RED-GREEN verification. The user has approved implementation and requested live A1↔Proxmox testing.

**Goal:** Add unique human-readable peer aliases that can be set at registration or runtime and used instead of IDs for messaging.

**Architecture:** Extend the remote broker's SQLite schema with peer aliases and immutable sender-alias snapshots, resolve send targets by ID or case-insensitive alias, and expose alias registration/change through the existing Hermes bridge. Preserve all current ID-based behavior.

**Tech Stack:** Bun, TypeScript, SQLite, Python 3, unittest, Hermes plugin API.

---

### Task 1: Add failing broker HTTP integration tests

**Files:**
- Create: `tests/test_broker_aliases.py`
- Exercise: `broker.remote-hub.ts`

1. Start the broker as a subprocess with a temporary DB, random local port, and test hub token.
2. Add tests for registration alias visibility, case-insensitive duplicate rejection, runtime rename, ID/alias target resolution, sender alias propagation, and old-schema migration.
3. Run `python3 -m unittest tests.test_broker_aliases -v`.
4. Confirm RED failures are caused by missing alias behavior.

### Task 2: Implement broker alias storage and routing

**Files:**
- Modify: `broker.remote-hub.ts`

1. Add additive schema migration helpers for `peers.alias` and `messages.from_alias`.
2. Add a case-insensitive unique alias index and trim/validate helper.
3. Store `logical_name` on registration, rejecting conflicts with HTTP 409.
4. Add `/set-alias`; preserve current alias when validation fails.
5. Resolve `/send-message` target by exact ID first, then alias; store canonical ID and sender alias snapshot.
6. Return `from_alias` from polling.
7. Run the focused broker tests until GREEN.

### Task 3: Add failing bridge/tool tests

**Files:**
- Create: `tests/test_bridge_aliases.py`
- Test: `bridge.py`, `schemas.py`, `__init__.py`

1. Test that registration sends the configured alias.
2. Test successful and rejected runtime alias updates.
3. Test incoming message formatting includes sender alias and ID.
4. Test tool schema/handler and CLI alias command wiring.
5. Run `python3 -m unittest tests.test_bridge_aliases -v` and confirm RED.

### Task 4: Implement bridge alias surfaces

**Files:**
- Modify: `bridge.py`
- Modify: `schemas.py`
- Modify: `__init__.py`

1. Add `set_alias()` with update-after-success semantics.
2. Add `claude_peers_set_alias(alias)` tool and `hermes claude-peers alias <name>` CLI command.
3. Document that `to_id` accepts either ID or alias.
4. Include `from_alias` in injected messages and reply guidance.
5. Run bridge tests and full unittest suite until GREEN.

### Task 5: Document configuration and verify artifacts

**Files:**
- Modify: `README.md`

1. Document `CLAUDE_PEERS_NAME=Mac|A1|Proxmox`, runtime rename, uniqueness, and alias addressing.
2. Run Python compile, full unit tests, Bun build on A1, and secret/personal-path scan.
3. Review exact git diff and commit only scoped files.

### Task 6: Deploy and run live bidirectional test

1. Back up and deploy `broker.remote-hub.ts` on A1; restart broker.
2. Deploy plugin files to Mac and A1, set aliases without printing `.env` contents, and restart gateways only when authorized/required.
3. Push the commit to GitHub so Proxmox can pull the exact artifact.
4. Ask the live Proxmox peer to pull, configure alias `Proxmox`, restart its gateway, and ACK.
5. Verify broker DB has unique aliases and health remains three peers beyond the heartbeat window.
6. Send A1 → Proxmox using alias and verify receipt.
7. Send Proxmox → A1 using alias and verify receipt.
8. Verify GitHub local/remote SHA equality and report exact runtime evidence.
