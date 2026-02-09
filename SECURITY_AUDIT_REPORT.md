# Security Audit Report: chigwell/telegram-mcp

**Audited:** 2025-02-05
**Repository:** https://github.com/chigwell/telegram-mcp
**Commit:** 36522e5 (HEAD of main)

---

## Executive Summary

**Verdict: SAFE (with caveats)**

This repository is a legitimate MCP (Model Context Protocol) server that bridges Claude AI with the Telegram API via the Telethon library. After a thorough line-by-line review of all 4,152 lines of Python code plus supporting files, **no malicious behavior, credential exfiltration, backdoors, or obfuscated code was found**. The code does exactly what it claims: it exposes Telegram API functionality as MCP tools. However, there are important caveats about the broad scope of permissions and some minor risks.

---

## Findings Table

| Category | Status | Notes |
|----------|--------|-------|
| Credential Safety | ✅ Safe | Credentials read from env vars only, passed only to Telegram's Telethon client. Never logged or sent elsewhere. |
| Network Connections | ✅ Safe | Only outbound connection is to Telegram's servers via Telethon. Zero HTTP client libraries imported. |
| Dependencies | ⚠️ Minor Issues | All dependencies are legitimate. `httpx` is declared but unused (pulled in by `mcp` library). Dependencies are not pinned to exact versions. |
| Code Quality | ✅ Good | Clean, readable code. 90+ commits, 10+ contributors, CI pipeline. No obfuscation. |
| Telegram API Usage | ⚠️ High Privilege | ~85 tools with **very broad** permissions including sending messages, deleting messages, banning users, joining groups, modifying profile, and admin management. |
| File System Access | ⚠️ Moderate Risk | Reads/writes files for media send/download, log file, and session file. No path sanitization on `file_path` parameters. |

---

## Detailed Findings

### 1. Credential Safety ✅

**No credential exfiltration found.**

- `main.py:62-67` — Credentials are read from environment variables using `os.getenv()`. Standard pattern.
- `main.py:71-76` — Credentials are passed only to `TelegramClient()` constructor, which sends them exclusively to Telegram's servers.
- Logger is set to `ERROR` level (`main.py:80`), and log messages contain only function names, error codes, and parameter metadata (e.g., `chat_id`). **Credentials are never logged.**
- `session_string_generator.py:65-68` — The session string is printed to stdout during interactive generation. This is by design (the user needs to copy it) and only runs when the user explicitly executes the script.

### 2. Network Analysis ✅

**No unexpected outbound connections.**

- The codebase imports **zero HTTP client libraries** (no `requests`, `httpx`, `aiohttp`, `urllib` imports in any `.py` file).
- `httpx` appears in `requirements.txt` and `pyproject.toml` but is a dependency of the `mcp` library, not used directly by this code.
- The only network connection is the Telethon `TelegramClient`, which connects exclusively to Telegram's official MTProto servers.
- No hardcoded IP addresses found in any Python file.
- No webhooks, POST requests to external URLs, or data exfiltration endpoints.

### 3. Dependency Analysis ⚠️

All 7 direct dependencies are legitimate:

| Package | PyPI | Purpose | Verdict |
|---------|------|---------|---------|
| `telethon>=1.39.0` | ✅ Well-known | Telegram client library | Safe |
| `mcp[cli]>=1.8.0` | ✅ Anthropic's MCP SDK | MCP server framework | Safe |
| `python-dotenv>=1.1.0` | ✅ Well-known | `.env` file loading | Safe |
| `nest-asyncio>=1.6.0` | ✅ Well-known | Nested event loop support | Safe |
| `python-json-logger>=3.3.0` | ✅ Well-known | JSON log formatting | Safe |
| `httpx>=0.28.1` | ✅ Well-known | HTTP client (MCP dependency) | Safe |
| `dotenv>=0.9.9` | ⚠️ Redundant | Duplicate of python-dotenv | Harmless but unnecessary |

**Issue: Dependencies are not pinned to exact versions.** Using `>=` allows automatic upgrades, which is a theoretical supply-chain risk. In practice, this is standard for Python projects and the risk is low.

### 4. Dangerous Functions ✅

- **`exec()`, `eval()`, `compile()` — NOT FOUND** in any Python file.
- **`subprocess`, `os.system`, `os.popen` — NOT FOUND.**
- **`importlib`, `__import__` — NOT FOUND.**
- **`base64` — NOT FOUND.** The only `decode()` call is `obj.decode("utf-8")` at `main.py:55` for JSON serialization of bytes, which is benign.
- `import random` at `main.py:3282` is used only for generating random poll IDs, which is appropriate.

### 5. Telegram API Usage ⚠️

This is the most significant security consideration. The code exposes ~85 MCP tools covering:

**Read-only tools (lower risk):**
- `get_chats`, `get_messages`, `list_contacts`, `search_contacts`, `get_me`, `get_history`, `get_participants`, `get_admins`, `get_pinned_messages`, `get_drafts`, `list_folders`, etc.

**Destructive/write tools (higher risk):**
- `send_message` — Send messages as you
- `delete_message` — Delete messages
- `edit_message` — Edit your messages
- `forward_message` — Forward messages between chats
- `ban_user` / `unban_user` — Ban/unban users from groups
- `promote_admin` / `demote_admin` — Change admin status
- `leave_chat` — Leave groups/channels
- `create_group` / `create_channel` — Create new groups
- `update_profile` / `set_profile_photo` — Modify your profile
- `set_privacy_settings` — Change privacy settings
- `join_chat_by_link` / `subscribe_public_channel` — Join chats
- `block_user` / `unblock_user` — Block/unblock users
- `press_inline_button` — Interact with bot inline keyboards
- `send_file` / `send_voice` / `send_sticker` — Send media
- `create_poll` — Create polls
- `save_draft` / `clear_draft` — Manage drafts
- `add_contact` / `delete_contact` / `import_contacts` — Manage contacts
- `create_folder` / `delete_folder` — Manage folders
- `send_reaction` / `remove_reaction` — React to messages

The code does properly annotate tools with `ToolAnnotations` (`readOnlyHint=True` for read operations, `destructiveHint=True` for write operations), which helps Claude make safer decisions.

**No account-destructive operations** (like `DeleteAccountRequest`) were found. The most destructive available actions are banning users, deleting messages, and leaving chats.

### 6. File System Access ⚠️

Files accessed:

| Location | Operation | Risk |
|----------|-----------|------|
| `.env` | Read (python-dotenv) | Standard, safe |
| `mcp_errors.log` | Write (append) | Same directory as script, safe |
| `*.session` / `*.session-journal` | Read/write (Telethon) | Standard session storage |
| User-provided `file_path` in `send_file`, `send_voice`, `send_sticker`, `set_profile_photo`, `edit_chat_photo` | Read | **No path sanitization** |
| User-provided `file_path` in `download_media` | Write | **No path sanitization** |

**Finding (Medium):** The `send_file`, `download_media`, and related functions accept arbitrary `file_path` parameters without sanitization. When connected to Claude, this means the AI could theoretically be instructed to:
- Send any readable file on the server to a Telegram chat (e.g., `/etc/passwd`, config files)
- Download Telegram media to arbitrary filesystem locations

The functions do check `os.path.isfile()` and `os.access()` before proceeding, but don't restrict paths to a safe directory.

### 7. Session String Generator

`session_string_generator.py` is a standalone interactive utility. It:
- Reads API credentials from `.env`
- Authenticates with Telegram interactively (phone number + verification code)
- Prints the session string to stdout
- Optionally writes it to `.env`

This is a standard Telethon pattern. The session string at `main.py:66-68` contains an authenticated session token that grants full account access. This is by design — the session string printed at `session_string_generator.py:66` is equivalent to a password.

### 8. Logging Analysis

- Logger is set to `ERROR` level (`main.py:80,84,92`)
- Log entries contain function names, parameter names/values (like `chat_id`), and error details
- **Credentials (API_ID, API_HASH, SESSION_STRING) are NEVER logged**
- Phone numbers appear in log context for `add_contact` failures (`main.py:1351,1354`) — this is a minor info disclosure in error logs

---

## Red Flag Checklist

| Check | Result |
|-------|--------|
| Credentials sent to non-Telegram servers | **NOT FOUND** |
| Obfuscated code | **NOT FOUND** |
| Remote code execution | **NOT FOUND** |
| Known malicious packages | **NOT FOUND** |
| Data exfiltration to external endpoints | **NOT FOUND** |
| Backdoor accounts or hidden admin access | **NOT FOUND** |
| Destructive actions without user intent | **NOT FOUND** (tools are properly annotated) |

---

## Recommendations

### Before Deploying

1. **Run in a container or restricted environment.** The `send_file` tool can read any file accessible to the process. Use Docker (the repo provides a `Dockerfile`) and mount only necessary directories.

2. **Review which MCP tools to expose.** Consider forking and removing destructive tools you don't need (e.g., `ban_user`, `promote_admin`, `delete_message`, `leave_chat`, `set_privacy_settings`). The codebase is modular — each tool is a standalone decorated function.

3. **Pin dependency versions.** Change `>=` to `==` in `requirements.txt` for reproducible builds:
   ```
   telethon==1.39.0
   mcp[cli]==1.8.0
   ```

4. **Protect the session string.** The `TELEGRAM_SESSION_STRING` environment variable grants full account access. Treat it like a password. Use secrets management (Docker secrets, Vault, etc.) instead of `.env` files in production.

5. **The `file_path` parameters lack sandboxing.** If deploying on a server, ensure the process runs as a restricted user with minimal filesystem access.

### Optional Improvements

- The `dotenv` package in dependencies is redundant with `python-dotenv` — it could be removed from `requirements.txt`/`pyproject.toml`.
- Phone numbers in `add_contact` error logs (`main.py:1351,1354`) could be redacted.

---

## Conclusion

The code is safe to run with your personal Telegram credentials. It is a straightforward MCP-to-Telegram bridge with no hidden functionality. The main risks are operational rather than malicious: the broad scope of available tools means Claude could take unintended actions on your account if given ambiguous instructions. Mitigate this by running in a container, restricting filesystem access, and considering which tools to expose.
