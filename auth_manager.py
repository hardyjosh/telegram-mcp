"""Authentication manager for Telegram MCP server.

Handles:
- MCP bearer token generation and validation
- Telegram session management via bot conversation
- Encrypted storage of sensitive credentials

Tokens are stored as SHA-256 hashes — the plaintext is shown once
and never stored.

Session strings are encrypted at rest using Fernet (AES-128-CBC + HMAC)
with a key derived from the SESSION_ENCRYPTION_KEY environment variable.
"""

import base64
import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


# ============================================================================
# Encryption
# ============================================================================

def _get_fernet() -> Fernet:
    """Get a Fernet cipher from SESSION_ENCRYPTION_KEY env var.

    The env var can be:
    - A 32-byte URL-safe base64 Fernet key (44 chars)
    - Any passphrase (will be SHA-256 hashed and base64-encoded)
    """
    key = os.environ.get("SESSION_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "SESSION_ENCRYPTION_KEY environment variable is required. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )

    # If it looks like a raw Fernet key (44 chars, base64), use directly
    if len(key) == 44 and key.endswith("="):
        return Fernet(key.encode())

    # Otherwise derive from passphrase
    derived = hashlib.sha256(key.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(derived)
    return Fernet(fernet_key)


def _encrypt(plaintext: str) -> str:
    """Encrypt a string, return base64-encoded ciphertext."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext, return plaintext."""
    f = _get_fernet()
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise RuntimeError("Failed to decrypt — SESSION_ENCRYPTION_KEY may have changed")


# ============================================================================
# Database
# ============================================================================

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
                phone_enc TEXT,
                phone_code_hash_enc TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS telegram_session (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                session_string_enc TEXT NOT NULL,
                owner_id INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)

        # Migrate old plaintext columns if they exist
        # (safe to run multiple times — ignores if columns don't exist)
        try:
            cursor = conn.execute("PRAGMA table_info(telegram_session)")
            columns = {row[1] for row in cursor.fetchall()}
            if "session_string" in columns and "session_string_enc" not in columns:
                conn.execute("ALTER TABLE telegram_session RENAME COLUMN session_string TO session_string_enc")
                conn.commit()
        except Exception:
            pass

        try:
            cursor = conn.execute("PRAGMA table_info(auth_state)")
            columns = {row[1] for row in cursor.fetchall()}
            if "phone" in columns and "phone_enc" not in columns:
                conn.execute("ALTER TABLE auth_state RENAME COLUMN phone TO phone_enc")
                conn.execute("ALTER TABLE auth_state RENAME COLUMN phone_code_hash TO phone_code_hash_enc")
                conn.commit()
        except Exception:
            pass

        conn.commit()
    finally:
        conn.close()


# ============================================================================
# MCP Token Management
# ============================================================================

_MAX_ACTIVE_TOKENS = 10


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_token(label: Optional[str] = None) -> str:
    """Generate a new MCP bearer token. Returns the plaintext token (shown once).

    The SHA-256 hash is stored in the database.
    Enforces a maximum of MAX_ACTIVE_TOKENS active tokens.
    """
    conn = _get_conn()
    try:
        # Check active token count
        cursor = conn.execute(
            "SELECT COUNT(*) FROM mcp_tokens WHERE revoked = 0"
        )
        count = cursor.fetchone()[0]
        if count >= _MAX_ACTIVE_TOKENS:
            raise RuntimeError(
                f"Maximum of {_MAX_ACTIVE_TOKENS} active tokens reached. "
                "Revoke old tokens first with /revoke_keys."
            )

        token = secrets.token_urlsafe(48)
        token_hash = _hash_token(token)

        conn.execute(
            "INSERT INTO mcp_tokens (token_hash, label) VALUES (?, ?)",
            (token_hash, label),
        )
        conn.commit()
    finally:
        conn.close()

    return token


def validate_token(token: str) -> bool:
    """Check if a bearer token is valid (exists and not revoked).

    Updates last_used_at on success.
    """
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


# ============================================================================
# Auth Conversation State (encrypted, auto-expiring)
# ============================================================================

_AUTH_STATE_TTL_MINUTES = 5


def set_auth_state(user_id: int, step: str, phone: str = None, phone_code_hash: str = None) -> None:
    phone_enc = _encrypt(phone) if phone else None
    pch_enc = _encrypt(phone_code_hash) if phone_code_hash else None

    conn = _get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO auth_state (user_id, step, phone_enc, phone_code_hash_enc, created_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (user_id, step, phone_enc, pch_enc),
        )
        conn.commit()
    finally:
        conn.close()


def get_auth_state(user_id: int) -> Optional[dict]:
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "SELECT step, phone_enc, phone_code_hash_enc, created_at FROM auth_state WHERE user_id = ?",
            (user_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        # Check TTL
        created = datetime.fromisoformat(row[3])
        if datetime.utcnow() - created > timedelta(minutes=_AUTH_STATE_TTL_MINUTES):
            # Expired — clean up
            conn.execute("DELETE FROM auth_state WHERE user_id = ?", (user_id,))
            conn.commit()
            return None

        return {
            "step": row[0],
            "phone": _decrypt(row[1]) if row[1] else None,
            "phone_code_hash": _decrypt(row[2]) if row[2] else None,
        }
    finally:
        conn.close()


def clear_auth_state(user_id: int) -> None:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM auth_state WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def cleanup_expired_auth_states() -> int:
    """Remove all expired auth states. Returns count removed."""
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "DELETE FROM auth_state WHERE created_at < datetime('now', ?)",
            (f"-{_AUTH_STATE_TTL_MINUTES} minutes",),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


# ============================================================================
# Session String Storage (encrypted)
# ============================================================================

def store_session(session_string: str, owner_id: int = None) -> None:
    """Store the Telegram session string encrypted in the DB."""
    encrypted = _encrypt(session_string)
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO telegram_session (id, session_string_enc, owner_id, created_at)
               VALUES (1, ?, ?, datetime('now'))""",
            (encrypted, owner_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_session() -> Optional[str]:
    """Retrieve and decrypt the stored session string, or None."""
    conn = _get_conn()
    try:
        cursor = conn.execute("SELECT session_string_enc FROM telegram_session WHERE id = 1")
        row = cursor.fetchone()
        if not row or not row[0]:
            return None
        return _decrypt(row[0])
    finally:
        conn.close()
