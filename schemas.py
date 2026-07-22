"""Tool schemas exposed by the claude-peers Hermes bridge."""

CLAUDE_PEERS_LIST_PEERS = {
    "name": "claude_peers_list_peers",
    "description": (
        "List live claude-peers broker sessions on this machine, in the current directory, "
        "or in the current git repository. Use this before sending a peer message when you "
        "need the current peer id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["machine", "directory", "repo"],
                "description": "Discovery scope. Defaults to machine.",
            }
        },
    },
}

CLAUDE_PEERS_SEND_MESSAGE = {
    "name": "claude_peers_send_message",
    "description": (
        "Send a message through the claude-peers broker to another peer id or unique alias. "
        "Use this to reply to incoming peer messages or to initiate contact."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "to_id": {
                "type": "string",
                "description": "Target peer id or unique alias from claude_peers_list_peers or an injected inbox event.",
            },
            "message": {
                "type": "string",
                "description": "The message text to send.",
            },
        },
        "required": ["to_id", "message"],
    },
}

CLAUDE_PEERS_SET_ALIAS = {
    "name": "claude_peers_set_alias",
    "description": (
        "Set this peer's unique human-readable alias. The alias is matched case-insensitively "
        "and can be used instead of a peer id when sending messages."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "alias": {
                "type": "string",
                "description": "Unique non-empty alias, for example Mac, A1, or Proxmox.",
            }
        },
        "required": ["alias"],
    },
}

CLAUDE_PEERS_CHECK_MESSAGES = {
    "name": "claude_peers_check_messages",
    "description": (
        "Manually poll the claude-peers broker for unread messages that belong to this Hermes session. "
        "Normally the bridge polls in the background, but this is useful for debugging."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

CLAUDE_PEERS_SET_SUMMARY = {
    "name": "claude_peers_set_summary",
    "description": (
        "Update this Hermes peer summary shown to other peers in list_peers."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Short summary of current work.",
            }
        },
        "required": ["summary"],
    },
}

CLAUDE_PEERS_BRIDGE_STATUS = {
    "name": "claude_peers_bridge_status",
    "description": (
        "Show bridge registration state, logical peer name, broker URL, and polling status."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}
