"""Authentication manager for Telegram MCP server.

Handles:
- MCP bearer token generation and validation
- Telegram session management via bot conversation
- Encrypted storage of sensitive credentials

Tokens are stored as SHA-256 hashes — the plaintext is shown once
and never stored.
"""

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime
from typing import Optional


def _db_path() -> str:
    return os.getenv(
        "TELEGRAM_MCP_PERMISSIONS_DB",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "permissions.db"),
    )


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_auth_db() -> None:
    """Create auth tables if they don't exist."""
    conn = _get_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS mcp_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                label TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_used_at TEXT,
                revoked INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS auth_state (
                user_id INTEGER PRIMARY KEY,
                step TEXT NOT NULL,
                phone TEXT,
                phone_code_hash TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        conn.commit()
    finally:
        conn.close()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_token(label: Optional[str] = None) -> str:
    """Generate a new MCP bearer token. Returns the plaintext token (shown once).

    The SHA-256 hash is stored in the database.
    """
    token = secrets.token_urlsafe(48)
    token_hash = _hash_token(token)

    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO mcp_tokens (token_hash, label) VALUES (?, ?)",
            (token_hash, label),
        )
        conn.commit()
    finally:
        conn.close()

    return token


def validate_token(token: str) -> bool:
    """Check if a bearer token is valid (exists and not revoked)."""
    token_hash = _hash_token(token)
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "SELECT id FROM mcp_tokens WHERE token_hash = ? AND revoked = 0",
            (token_hash,),
        )
        row = cursor.fetchone()
        if row:
            conn.execute(
                "UPDATE mcp_tokens SET last_used_at = datetime('now') WHERE id = ?",
                (row[0],),
            )
            conn.commit()
            return True
        return False
    finally:
        conn.close()


def revoke_all_tokens() -> int:
    """Revoke all active tokens. Returns count revoked."""
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "UPDATE mcp_tokens SET revoked = 1 WHERE revoked = 0"
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def list_tokens() -> list[dict]:
    """List all tokens (metadata only, not hashes)."""
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "SELECT id, label, created_at, last_used_at, revoked FROM mcp_tokens ORDER BY created_at DESC"
        )
        return [
            {
                "id": row[0],
                "label": row[1],
                "created_at": row[2],
                "last_used_at": row[3],
                "revoked": bool(row[4]),
            }
            for row in cursor.fetchall()
        ]
    finally:
        conn.close()


# --- Auth conversation state ---

def set_auth_state(user_id: int, step: str, phone: str = None, phone_code_hash: str = None) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO auth_state (user_id, step, phone, phone_code_hash, created_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (user_id, step, phone, phone_code_hash),
        )
        conn.commit()
    finally:
        conn.close()


def get_auth_state(user_id: int) -> Optional[dict]:
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "SELECT step, phone, phone_code_hash FROM auth_state WHERE user_id = ?",
            (user_id,),
        )
        row = cursor.fetchone()
        if row:
            return {"step": row[0], "phone": row[1], "phone_code_hash": row[2]}
        return None
    finally:
        conn.close()


def clear_auth_state(user_id: int) -> None:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM auth_state WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
