"""Chat permissions module for Telegram MCP server.

Provides a per-chat allowlist with granular permission categories.
Permissions are set globally (not per-chat) — the user defines what
capabilities are enabled, then toggles which chats those apply to.

Permission categories map to MCP tool annotations:
- read: Tools with readOnlyHint=True that take a chat_id
- write: Tools with destructiveHint=True that take a chat_id
- Global tools (get_me, list_contacts, etc.) are unaffected

Storage: SQLite database, one per user session.
"""

import json
import os
import sqlite3
from dataclasses import dataclass, field
try:
    from enum import StrEnum
except ImportError:
    from enum import Enum

    class StrEnum(str, Enum):
        pass
from pathlib import Path
from typing import Optional


class Permission(StrEnum):
    """Permission categories for chat access."""

    READ = "read"
    WRITE = "write"
    DRAFTS = "drafts"
    CONTACTS = "contacts"
    GROUPS = "groups"
    PROFILE = "profile"
    PRIVACY = "privacy"


# Default permissions — only read is on
DEFAULT_PERMISSIONS = {
    Permission.READ: True,
    Permission.WRITE: False,
    Permission.DRAFTS: False,
    Permission.CONTACTS: False,
    Permission.GROUPS: False,
    Permission.PROFILE: False,
    Permission.PRIVACY: False,
}

# Explicit tool → permission mappings (overrides annotation-based detection)
TOOL_PERMISSION_OVERRIDES: dict[str, Permission] = {
    # Drafts
    "save_draft": Permission.DRAFTS,
    "get_drafts": Permission.DRAFTS,
    "clear_draft": Permission.DRAFTS,
    # Contacts
    "list_contacts": Permission.CONTACTS,
    "search_contacts": Permission.CONTACTS,
    "get_contact_ids": Permission.CONTACTS,
    "add_contact": Permission.CONTACTS,
    "delete_contact": Permission.CONTACTS,
    "import_contacts": Permission.CONTACTS,
    "export_contacts": Permission.CONTACTS,
    "get_contact_chats": Permission.CONTACTS,
    "get_direct_chat_by_contact": Permission.CONTACTS,
    "get_last_interaction": Permission.CONTACTS,
    "block_user": Permission.CONTACTS,
    "unblock_user": Permission.CONTACTS,
    "get_blocked_users": Permission.CONTACTS,
    # Group/channel management
    "create_group": Permission.GROUPS,
    "create_channel": Permission.GROUPS,
    "invite_to_group": Permission.GROUPS,
    "leave_chat": Permission.GROUPS,
    "edit_chat_title": Permission.GROUPS,
    "edit_chat_photo": Permission.GROUPS,
    "delete_chat_photo": Permission.GROUPS,
    "promote_admin": Permission.GROUPS,
    "demote_admin": Permission.GROUPS,
    "ban_user": Permission.GROUPS,
    "unban_user": Permission.GROUPS,
    "get_admins": Permission.GROUPS,
    "get_banned_users": Permission.GROUPS,
    "get_invite_link": Permission.GROUPS,
    "join_chat_by_link": Permission.GROUPS,
    "export_chat_invite": Permission.GROUPS,
    "import_chat_invite": Permission.GROUPS,
    "subscribe_public_channel": Permission.GROUPS,
    # Profile
    "update_profile": Permission.PROFILE,
    "set_profile_photo": Permission.PROFILE,
    "delete_profile_photo": Permission.PROFILE,
    "get_user_photos": Permission.PROFILE,
    # Privacy
    "get_privacy_settings": Permission.PRIVACY,
    "set_privacy_settings": Permission.PRIVACY,
}

# Map tool names to required permissions.
# Tools not in this map are considered global (no chat-level restriction).
# This is populated at startup by scanning tool annotations.
TOOL_PERMISSION_MAP: dict[str, Permission] = {}


def _db_path() -> str:
    """Get the path to the permissions database."""
    return os.getenv(
        "TELEGRAM_MCP_PERMISSIONS_DB",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "permissions.db"),
    )


def _get_conn() -> sqlite3.Connection:
    """Get a SQLite connection with WAL mode."""
    conn = sqlite3.connect(_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create the permissions tables if they don't exist."""
    conn = _get_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS global_permissions (
                permission TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS chat_allowlist (
                chat_id INTEGER PRIMARY KEY,
                chat_title TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                added_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """
        )
        # Seed default global permissions (insert any missing)
        for perm, default in DEFAULT_PERMISSIONS.items():
            conn.execute(
                "INSERT OR IGNORE INTO global_permissions (permission, enabled) VALUES (?, ?)",
                (perm.value, int(default)),
            )
        conn.commit()
    finally:
        conn.close()


def get_global_permissions() -> dict[str, bool]:
    """Get all global permission settings."""
    conn = _get_conn()
    try:
        cursor = conn.execute("SELECT permission, enabled FROM global_permissions")
        return {row[0]: bool(row[1]) for row in cursor.fetchall()}
    finally:
        conn.close()


def set_global_permission(permission: str, enabled: bool) -> None:
    """Set a global permission on or off."""
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO global_permissions (permission, enabled) VALUES (?, ?)",
            (permission, int(enabled)),
        )
        conn.commit()
    finally:
        conn.close()


def toggle_global_permission(permission: str) -> bool:
    """Toggle a global permission. Returns the new state."""
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "SELECT enabled FROM global_permissions WHERE permission = ?", (permission,)
        )
        row = cursor.fetchone()
        new_state = not bool(row[0]) if row else True
        conn.execute(
            "INSERT OR REPLACE INTO global_permissions (permission, enabled) VALUES (?, ?)",
            (permission, int(new_state)),
        )
        conn.commit()
        return new_state
    finally:
        conn.close()


def get_allowlisted_chats() -> list[dict]:
    """Get all allowlisted chats."""
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "SELECT chat_id, chat_title, enabled FROM chat_allowlist ORDER BY chat_title"
        )
        return [
            {"chat_id": row[0], "chat_title": row[1], "enabled": bool(row[2])}
            for row in cursor.fetchall()
        ]
    finally:
        conn.close()


def add_chat(chat_id: int, chat_title: str) -> None:
    """Add a chat to the allowlist."""
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO chat_allowlist (chat_id, chat_title, enabled) VALUES (?, ?, 1)",
            (chat_id, chat_title),
        )
        conn.commit()
    finally:
        conn.close()


def remove_chat(chat_id: int) -> None:
    """Remove a chat from the allowlist."""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM chat_allowlist WHERE chat_id = ?", (chat_id,))
        conn.commit()
    finally:
        conn.close()


def toggle_chat(chat_id: int) -> bool:
    """Toggle a chat's enabled state. Returns the new state."""
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "SELECT enabled FROM chat_allowlist WHERE chat_id = ?", (chat_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return False
        new_state = not bool(row[0])
        conn.execute(
            "UPDATE chat_allowlist SET enabled = ? WHERE chat_id = ?",
            (int(new_state), chat_id),
        )
        conn.commit()
        return new_state
    finally:
        conn.close()


def is_chat_allowed(chat_id: int) -> bool:
    """Check if a chat is in the allowlist and enabled."""
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "SELECT enabled FROM chat_allowlist WHERE chat_id = ?", (chat_id,)
        )
        row = cursor.fetchone()
        return bool(row[0]) if row else False
    finally:
        conn.close()


def check_permission(tool_name: str, chat_id: Optional[int] = None) -> tuple[bool, str]:
    """Check if a tool call is permitted.

    Args:
        tool_name: The MCP tool name being called.
        chat_id: The chat_id parameter if the tool takes one.

    Returns:
        (allowed, reason) tuple.
    """
    # Check global read/write permission if tool is in the permission map
    required_perm = TOOL_PERMISSION_MAP.get(tool_name)
    if required_perm is not None:
        global_perms = get_global_permissions()
        if not global_perms.get(required_perm.value, False):
            return False, f"Global '{required_perm.value}' permission is disabled"

    # If tool targets a chat, enforce allowlist regardless of read/write
    if chat_id is not None:
        try:
            chat_id_int = int(chat_id)
        except (ValueError, TypeError):
            # Username-based — can't filter by ID, allow if global perm passed
            return True, ""

        if not is_chat_allowed(chat_id_int):
            return False, f"Chat {chat_id_int} is not in the allowlist"

    return True, ""


def build_tool_permission_map(tools_with_annotations: list[dict]) -> None:
    """Build the TOOL_PERMISSION_MAP from tool annotations.

    Args:
        tools_with_annotations: List of dicts with 'name' and 'annotations' keys.
            annotations should have readOnlyHint and/or destructiveHint booleans.
    """
    global TOOL_PERMISSION_MAP

    # Tools that operate on chats (take chat_id as a parameter)
    chat_tools = set()
    for tool in tools_with_annotations:
        name = tool["name"]
        annotations = tool.get("annotations", {})

        # Only map tools that have chat-level scope
        # (we'll check if they take chat_id at enforcement time)
        # Explicit overrides take priority
        if name in TOOL_PERMISSION_OVERRIDES:
            TOOL_PERMISSION_MAP[name] = TOOL_PERMISSION_OVERRIDES[name]
        elif annotations.get("readOnlyHint"):
            TOOL_PERMISSION_MAP[name] = Permission.READ
        elif annotations.get("destructiveHint"):
            TOOL_PERMISSION_MAP[name] = Permission.WRITE
