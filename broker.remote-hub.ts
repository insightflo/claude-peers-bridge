#!/usr/bin/env bun
/**
 * claude-peers broker daemon
 *
 * A singleton HTTP server on localhost:7899 backed by SQLite.
 * Tracks all registered Claude Code peers and routes messages between them.
 *
 * Auto-launched by the MCP server if not already running.
 * Run directly: bun broker.ts
 */

import { Database } from "bun:sqlite";
import type {
  RegisterRequest,
  RegisterResponse,
  HeartbeatRequest,
  SetSummaryRequest,
  ListPeersRequest,
  SendMessageRequest,
  PollMessagesRequest,
  PollMessagesResponse,
  Peer,
  Message,
} from "./shared/types.ts";

const PORT = parseInt(process.env.CLAUDE_PEERS_PORT ?? "7899", 10);
const DB_PATH = process.env.CLAUDE_PEERS_DB ?? `${process.env.HOME}/.claude-peers.db`;
const HUB_TOKEN = process.env.CLAUDE_PEERS_HUB_TOKEN ?? "";
const REMOTE_HUB = HUB_TOKEN.length > 0;
const STALE_PEER_TIMEOUT_MS = parseInt(
  process.env.CLAUDE_PEERS_STALE_TIMEOUT_MS ?? "90000",
  10,
);

// --- Database setup ---

const db = new Database(DB_PATH);
db.run("PRAGMA journal_mode = WAL");
db.run("PRAGMA busy_timeout = 3000");

db.run(`
  CREATE TABLE IF NOT EXISTS peers (
    id TEXT PRIMARY KEY,
    pid INTEGER NOT NULL,
    cwd TEXT NOT NULL,
    git_root TEXT,
    tty TEXT,
    summary TEXT NOT NULL DEFAULT '',
    registered_at TEXT NOT NULL,
    last_seen TEXT NOT NULL
  )
`);

db.run(`
  CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    text TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (from_id) REFERENCES peers(id),
    FOREIGN KEY (to_id) REFERENCES peers(id)
  )
`);

function hasColumn(table: string, column: string): boolean {
  const columns = db.query(`PRAGMA table_info(${table})`).all() as { name: string }[];
  return columns.some((candidate) => candidate.name === column);
}

// Additive migrations keep existing broker databases compatible.
if (!hasColumn("peers", "alias")) {
  db.run("ALTER TABLE peers ADD COLUMN alias TEXT");
}
if (!hasColumn("messages", "from_alias")) {
  db.run("ALTER TABLE messages ADD COLUMN from_alias TEXT");
}
db.run(`
  CREATE UNIQUE INDEX IF NOT EXISTS idx_peers_alias_nocase
  ON peers(alias COLLATE NOCASE)
  WHERE alias IS NOT NULL
`);

// Local brokers can validate PIDs directly. A remote hub cannot: peer PIDs
// belong to other machines, so use heartbeat age instead.
function cleanStalePeers() {
  const peers = db.query("SELECT id, pid, last_seen FROM peers").all() as {
    id: string;
    pid: number;
    last_seen: string;
  }[];
  for (const peer of peers) {
    if (REMOTE_HUB) {
      const lastSeen = Date.parse(peer.last_seen);
      if (Number.isFinite(lastSeen) && Date.now() - lastSeen <= STALE_PEER_TIMEOUT_MS) {
        continue;
      }
      db.run("DELETE FROM peers WHERE id = ?", [peer.id]);
      db.run("DELETE FROM messages WHERE to_id = ? AND delivered = 0", [peer.id]);
      continue;
    }
    try {
      // Check if process is still alive (signal 0 doesn't kill, just checks)
      process.kill(peer.pid, 0);
    } catch {
      // Process doesn't exist, remove it
      db.run("DELETE FROM peers WHERE id = ?", [peer.id]);
      db.run("DELETE FROM messages WHERE to_id = ? AND delivered = 0", [peer.id]);
    }
  }
}

cleanStalePeers();

// Periodically clean stale peers (every 30s)
setInterval(cleanStalePeers, 30_000);

// --- Prepared statements ---

const insertPeer = db.prepare(`
  INSERT INTO peers (id, pid, cwd, git_root, tty, summary, registered_at, last_seen, alias)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
`);

const updateLastSeen = db.prepare(`
  UPDATE peers SET last_seen = ? WHERE id = ?
`);

const updateSummary = db.prepare(`
  UPDATE peers SET summary = ? WHERE id = ?
`);

const updateAlias = db.prepare(`
  UPDATE peers SET alias = ? WHERE id = ?
`);

const deletePeer = db.prepare(`
  DELETE FROM peers WHERE id = ?
`);

const selectAllPeers = db.prepare(`
  SELECT * FROM peers
`);

const selectPeersByDirectory = db.prepare(`
  SELECT * FROM peers WHERE cwd = ?
`);

const selectPeersByGitRoot = db.prepare(`
  SELECT * FROM peers WHERE git_root = ?
`);

const insertMessage = db.prepare(`
  INSERT INTO messages (from_id, to_id, text, sent_at, delivered, from_alias)
  VALUES (?, ?, ?, ?, 0, ?)
`);

const selectUndelivered = db.prepare(`
  SELECT * FROM messages WHERE to_id = ? AND delivered = 0 ORDER BY sent_at ASC
`);

const markDelivered = db.prepare(`
  UPDATE messages SET delivered = 1 WHERE id = ?
`);

// --- Generate peer ID ---

function generateId(): string {
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
  let id = "";
  for (let i = 0; i < 8; i++) {
    id += chars[Math.floor(Math.random() * chars.length)];
  }
  return id;
}

// --- Request handlers ---

type RegisterWithAliasRequest = RegisterRequest & { logical_name?: string | null };
type PeerWithAlias = Peer & { alias: string | null };
type SetAliasRequest = { id: string; alias: string };

class AliasConflictError extends Error {
  constructor(public alias: string) {
    super(`Alias ${alias} is already in use`);
  }
}

class AliasValidationError extends Error {}
class PeerNotFoundError extends Error {}

function normalizeAlias(value: unknown): string | null {
  if (value === undefined || value === null) {
    return null;
  }
  if (typeof value !== "string") {
    throw new AliasValidationError("Alias must be a string");
  }
  const alias = value.trim();
  if (!alias) {
    throw new AliasValidationError("Alias must not be empty");
  }
  return alias;
}

function findAliasConflict(alias: string, excludeId?: string): { id: string } | null {
  if (excludeId) {
    return db
      .query("SELECT id FROM peers WHERE alias = ? COLLATE NOCASE AND id <> ?")
      .get(alias, excludeId) as { id: string } | null;
  }
  return db.query("SELECT id FROM peers WHERE alias = ? COLLATE NOCASE").get(alias) as {
    id: string;
  } | null;
}

function handleRegister(body: RegisterWithAliasRequest): RegisterResponse & { alias: string | null } {
  const id = generateId();
  const now = new Date().toISOString();
  const alias = normalizeAlias(body.logical_name);

  // A remote PID alone is not globally unique, but PID + cwd is sufficient
  // to collapse retries from the same running gateway after a brief broker
  // outage without conflating the current Mac/A1/Proxmox nodes.
  const existing = REMOTE_HUB
    ? db.query("SELECT id FROM peers WHERE pid = ? AND cwd = ?").get(body.pid, body.cwd) as { id: string } | null
    : db.query("SELECT id FROM peers WHERE pid = ?").get(body.pid) as { id: string } | null;
  if (alias && findAliasConflict(alias, existing?.id)) {
    throw new AliasConflictError(alias);
  }
  if (existing) {
    deletePeer.run(existing.id);
  }

  insertPeer.run(id, body.pid, body.cwd, body.git_root, body.tty, body.summary, now, now, alias);
  return { id, alias };
}

function handleHeartbeat(body: HeartbeatRequest): { ok: boolean; reregister?: boolean } {
  const result = updateLastSeen.run(new Date().toISOString(), body.id);
  if (result.changes === 0) {
    return { ok: false, reregister: true };
  }
  return { ok: true };
}

function handleSetSummary(body: SetSummaryRequest): void {
  updateSummary.run(body.summary, body.id);
}

function handleSetAlias(body: SetAliasRequest): { ok: true; alias: string } {
  const alias = normalizeAlias(body.alias);
  if (!alias) {
    throw new AliasValidationError("Alias must not be empty");
  }
  const peer = db.query("SELECT id FROM peers WHERE id = ?").get(body.id) as { id: string } | null;
  if (!peer) {
    throw new PeerNotFoundError(`Peer ${body.id} not found`);
  }
  if (findAliasConflict(alias, body.id)) {
    throw new AliasConflictError(alias);
  }
  updateAlias.run(alias, body.id);
  return { ok: true, alias };
}

function handleListPeers(body: ListPeersRequest): PeerWithAlias[] {
  let peers: PeerWithAlias[];

  switch (body.scope) {
    case "machine":
      peers = selectAllPeers.all() as PeerWithAlias[];
      break;
    case "directory":
      peers = selectPeersByDirectory.all(body.cwd) as PeerWithAlias[];
      break;
    case "repo":
      if (body.git_root) {
        peers = selectPeersByGitRoot.all(body.git_root) as PeerWithAlias[];
      } else {
        // No git root, fall back to directory
        peers = selectPeersByDirectory.all(body.cwd) as PeerWithAlias[];
      }
      break;
    default:
      peers = selectAllPeers.all() as PeerWithAlias[];
  }

  // Exclude the requesting peer
  if (body.exclude_id) {
    peers = peers.filter((p) => p.id !== body.exclude_id);
  }

  // Remote PIDs cannot be checked from the hub host. Heartbeat expiry is
  // handled centrally by cleanStalePeers().
  if (REMOTE_HUB) {
    return peers;
  }

  // Verify each local peer's process is still alive.
  return peers.filter((p) => {
    try {
      process.kill(p.pid, 0);
      return true;
    } catch {
      // Clean up dead peer
      deletePeer.run(p.id);
      return false;
    }
  });
}

function handleSendMessage(
  body: SendMessageRequest,
): { ok: boolean; error?: string; to_id?: string; to_alias?: string | null } {
  const sender = db.query("SELECT id, alias FROM peers WHERE id = ?").get(body.from_id) as {
    id: string;
    alias: string | null;
  } | null;
  if (!sender) {
    return { ok: false, error: `Sender ${body.from_id} not found` };
  }

  // Preserve exact-ID compatibility, then resolve a human-readable alias.
  const target = (db.query("SELECT id, alias FROM peers WHERE id = ?").get(body.to_id) ??
    db.query("SELECT id, alias FROM peers WHERE alias = ? COLLATE NOCASE").get(body.to_id)) as {
      id: string;
      alias: string | null;
    } | null;
  if (!target) {
    return { ok: false, error: `Peer ${body.to_id} not found` };
  }

  insertMessage.run(body.from_id, target.id, body.text, new Date().toISOString(), sender.alias);
  return { ok: true, to_id: target.id, to_alias: target.alias };
}

function handlePollMessages(body: PollMessagesRequest): PollMessagesResponse {
  const messages = selectUndelivered.all(body.id) as (Message & { from_alias: string | null })[];

  // Mark them as delivered
  for (const msg of messages) {
    markDelivered.run(msg.id);
  }

  return { messages };
}

function handleUnregister(body: { id: string }): void {
  deletePeer.run(body.id);
}

// --- HTTP Server ---

Bun.serve({
  port: PORT,
  hostname: "127.0.0.1",
  async fetch(req) {
    const url = new URL(req.url);
    const path = url.pathname;
    // Shared-secret auth for remote hubs (CLAUDE_PEERS_HUB_TOKEN).
    // When set, every POST must carry Authorization: Bearer <token>.
    if (HUB_TOKEN && req.method === "POST") {
      const auth = req.headers.get("Authorization") || "";
      if (auth !== `Bearer ${HUB_TOKEN}`) {
        return new Response("unauthorized", { status: 401 });
      }
    }

    if (req.method !== "POST") {
      if (path === "/health") {
        return Response.json({ status: "ok", peers: (selectAllPeers.all() as Peer[]).length });
      }
      return new Response("claude-peers broker", { status: 200 });
    }

    try {
      const body = await req.json();

      switch (path) {
        case "/register":
          return Response.json(handleRegister(body as RegisterWithAliasRequest));
        case "/heartbeat":
          return Response.json(handleHeartbeat(body as HeartbeatRequest));
        case "/set-summary":
          handleSetSummary(body as SetSummaryRequest);
          return Response.json({ ok: true });
        case "/set-alias":
          return Response.json(handleSetAlias(body as SetAliasRequest));
        case "/list-peers":
          return Response.json(handleListPeers(body as ListPeersRequest));
        case "/send-message":
          return Response.json(handleSendMessage(body as SendMessageRequest));
        case "/poll-messages":
          return Response.json(handlePollMessages(body as PollMessagesRequest));
        case "/unregister":
          handleUnregister(body as { id: string });
          return Response.json({ ok: true });
        default:
          return Response.json({ error: "not found" }, { status: 404 });
      }
    } catch (e) {
      if (e instanceof AliasConflictError) {
        return Response.json({ error: "alias_conflict", alias: e.alias }, { status: 409 });
      }
      if (e instanceof AliasValidationError) {
        return Response.json({ error: "invalid_alias", message: e.message }, { status: 400 });
      }
      if (e instanceof PeerNotFoundError) {
        return Response.json({ error: "peer_not_found", message: e.message }, { status: 404 });
      }
      const msg = e instanceof Error ? e.message : String(e);
      return Response.json({ error: msg }, { status: 500 });
    }
  },
});

console.error(`[claude-peers broker] listening on 127.0.0.1:${PORT} (db: ${DB_PATH})`);
