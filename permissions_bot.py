"""Telegram bot for managing MCP server chat permissions and authentication.

Provides an inline keyboard interface for:
1. Toggling global permissions (read/write)
2. Browsing and toggling chat allowlist
3. Generating MCP bearer tokens (/generate-key)
4. Interactive Telegram auth flow (/auth)

The bot runs alongside the MCP server and shares the same permissions database.
It uses a separate bot token (not the user's Telethon session).

Usage:
    PERMISSIONS_BOT_TOKEN=<bot-token> python permissions_bot.py

The bot only responds to the authorized user (PERMISSIONS_BOT_OWNER_ID).
"""

import asyncio
import math
import os
import sys

from dotenv import load_dotenv
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession

import permissions
import auth_manager

load_dotenv()

# Bot configuration
BOT_TOKEN = os.getenv("PERMISSIONS_BOT_TOKEN")
OWNER_ID = int(os.getenv("PERMISSIONS_BOT_OWNER_ID", "0"))
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")

# For fetching the user's chat list, we need their Telethon client
SESSION_STRING = os.getenv("TELEGRAM_SESSION_STRING")
TELEGRAM_SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME")

# Fall back to DB-stored session
if not SESSION_STRING:
    try:
        auth_manager.init_auth_db()
        SESSION_STRING = auth_manager.get_session()
        if SESSION_STRING:
            print("Permissions bot: using session string from database")
    except Exception:
        pass

CHATS_PER_PAGE = 8
# Cached chat list (fetched once on /start, refreshed on demand)
_chat_cache: list[dict] = []

# Bot client (uses bot token)
bot = TelegramClient("permissions_bot", TELEGRAM_API_ID, TELEGRAM_API_HASH)

# User client (for listing their chats)
if SESSION_STRING:
    user_client = TelegramClient(
        StringSession(SESSION_STRING), TELEGRAM_API_ID, TELEGRAM_API_HASH
    )
else:
    user_client = TelegramClient(TELEGRAM_SESSION_NAME, TELEGRAM_API_ID, TELEGRAM_API_HASH)


def is_owner(event) -> bool:
    """Check if the event is from the authorized owner."""
    return event.sender_id == OWNER_ID


async def get_user_chats(force_refresh: bool = False) -> list[dict]:
    """Fetch the user's chat list via their Telethon session (cached)."""
    global _chat_cache
    if not force_refresh and _chat_cache:
        return _chat_cache
    dialogs = await user_client.get_dialogs(limit=None)
    chats = []
    for dialog in dialogs:
        entity = dialog.entity
        chat_id = entity.id
        title = getattr(entity, "title", None) or getattr(entity, "first_name", "Unknown")
        chats.append({"chat_id": chat_id, "title": title})
    _chat_cache = chats
    print(f"Chat cache loaded: {len(chats)} chats")
    return chats


def build_permissions_keyboard() -> list[list[Button]]:
    """Build the global permissions keyboard."""
    global_perms = permissions.get_global_permissions()
    buttons = []
    for perm_name, enabled in global_perms.items():
        icon = "\u2705" if enabled else "\u274c"
        label = perm_name.replace("_", " ").title()
        buttons.append(
            [Button.inline(f"{icon} {label}", data=f"perm:{perm_name}")]
        )
    buttons.append([Button.inline("\U0001f4ac Manage Chats", data="chats:0")])
    return buttons


async def build_chats_keyboard(page: int = 0) -> list[list[Button]]:
    """Build the chat allowlist keyboard with pagination."""
    user_chats = await get_user_chats()
    allowlisted = {c["chat_id"]: c["enabled"] for c in permissions.get_allowlisted_chats()}

    total_pages = max(1, math.ceil(len(user_chats) / CHATS_PER_PAGE))
    page = max(0, min(page, total_pages - 1))

    start = page * CHATS_PER_PAGE
    end = start + CHATS_PER_PAGE
    page_chats = user_chats[start:end]

    buttons = []
    for chat in page_chats:
        chat_id = chat["chat_id"]
        title = chat["title"]
        if chat_id in allowlisted and allowlisted[chat_id]:
            icon = "\u2705"
        else:
            icon = "\u2b1c"

        # Truncate long titles
        title = title or "Untitled"
        display_title = title[:30] + "..." if len(title) > 30 else title
        buttons.append(
            [Button.inline(f"{icon} {display_title}", data=f"chat:{chat_id}:{page}")]
        )

    # Navigation row
    nav = []
    if page > 0:
        nav.append(Button.inline("\u25c0\ufe0f Prev", data=f"chats:{page - 1}"))
    nav.append(Button.inline(f"{page + 1}/{total_pages}", data="noop"))
    if page < total_pages - 1:
        nav.append(Button.inline("Next \u25b6\ufe0f", data=f"chats:{page + 1}"))
    buttons.append(nav)

    # Refresh + Back buttons
    buttons.append([
        Button.inline("\U0001f504 Refresh", data="refresh_chats"),
        Button.inline("\u2b05\ufe0f Back", data="home"),
    ])

    return buttons


@bot.on(events.NewMessage(pattern="/start"))
async def start_handler(event):
    """Handle /start command — show permissions menu."""
    if not is_owner(event):
        return

    keyboard = build_permissions_keyboard()
    await event.respond(
        "**Telegram MCP Permissions**\n\n"
        "Toggle global permissions below, then manage which chats are accessible.",
        buttons=keyboard,
    )


@bot.on(events.CallbackQuery(pattern=b"home"))
async def home_handler(event):
    """Return to the main permissions menu."""
    if not is_owner(event):
        return
    await event.answer()

    keyboard = build_permissions_keyboard()
    await event.edit(
        "**Telegram MCP Permissions**\n\n"
        "Toggle global permissions below, then manage which chats are accessible.",
        buttons=keyboard,
    )


@bot.on(events.CallbackQuery(pattern=rb"perm:(.+)"))
async def toggle_permission_handler(event):
    """Toggle a global permission."""
    if not is_owner(event):
        return
    await event.answer()

    perm_name = event.pattern_match.group(1).decode()
    new_state = permissions.toggle_global_permission(perm_name)

    keyboard = build_permissions_keyboard()
    await event.edit(
        "**Telegram MCP Permissions**\n\n"
        "Toggle global permissions below, then manage which chats are accessible.",
        buttons=keyboard,
    )


@bot.on(events.CallbackQuery(pattern=rb"chats:(\d+)"))
async def chats_page_handler(event):
    """Show a page of chats."""
    if not is_owner(event):
        return
    await event.answer()

    page = int(event.pattern_match.group(1).decode())
    keyboard = await build_chats_keyboard(page)
    user_chats = await get_user_chats()
    total = len(user_chats)
    total_pages = max(1, math.ceil(total / CHATS_PER_PAGE))
    try:
        await event.edit(
            f"**Select chats to allow access:** ({total} chats)\n\n"
            "\u2705 = allowed, \u2b1c = not allowed",
            buttons=keyboard,
        )
    except Exception as e:
        print(f"Error editing page {page}/{total_pages} ({total} chats): {e}")


@bot.on(events.CallbackQuery(pattern=rb"chat:(-?\d+):(\d+)"))
async def toggle_chat_handler(event):
    """Toggle a chat's allowlist status."""
    if not is_owner(event):
        return
    await event.answer()

    chat_id = int(event.pattern_match.group(1).decode())
    page = int(event.pattern_match.group(2).decode())

    # If not in allowlist, add it
    if not permissions.is_chat_allowed(chat_id):
        # Get chat title from user's dialogs
        user_chats = await get_user_chats()
        title = next(
            (c["title"] for c in user_chats if c["chat_id"] == chat_id),
            "Unknown",
        )
        permissions.add_chat(chat_id, title)
    else:
        permissions.toggle_chat(chat_id)

    keyboard = await build_chats_keyboard(page)
    await event.edit(
        "**Select chats to allow access:**\n\n"
        "\u2705 = allowed, \u2b1c = not allowed",
        buttons=keyboard,
    )


@bot.on(events.CallbackQuery(pattern=b"refresh_chats"))
async def refresh_chats_handler(event):
    """Force-refresh the chat list and show page 0."""
    if not is_owner(event):
        return
    await event.answer("Refreshing chats...")

    await get_user_chats(force_refresh=True)
    keyboard = await build_chats_keyboard(0)
    await event.edit(
        "**Select chats to allow access:**\n\n"
        "\u2705 = allowed, \u2b1c = not allowed",
        buttons=keyboard,
    )


@bot.on(events.CallbackQuery(pattern=b"noop"))
async def noop_handler(event):
    """Handle no-op button presses (page indicator)."""
    await event.answer()


# ============================================================================
# KEY GENERATION
# ============================================================================


@bot.on(events.NewMessage(pattern="/generate_key"))
async def generate_key_handler(event):
    """Generate a new MCP bearer token."""
    if not is_owner(event):
        return

    try:
        token = auth_manager.generate_token(label="bot-generated")
    except RuntimeError as e:
        await event.respond(f"**Error:** {e}")
        return

    # Send in a way that's easy to copy but auto-deletes
    msg = await event.respond(
        "**New MCP Bearer Token Generated**\n\n"
        f"`{token}`\n\n"
        "Copy this now — it won't be shown again.\n"
        "Use it as the Bearer token in your MCP client config.\n\n"
        "_This message will auto-delete in 60 seconds._",
        buttons=[Button.inline("🗑 Delete Now", data="delete_token_msg")],
    )

    # Auto-delete after 60 seconds
    await asyncio.sleep(60)
    try:
        await msg.delete()
    except Exception:
        pass


@bot.on(events.NewMessage(pattern="/revoke_keys"))
async def revoke_keys_handler(event):
    """Revoke all active MCP bearer tokens."""
    if not is_owner(event):
        return

    await event.respond(
        "**⚠️ Revoke all MCP tokens?**\n\n"
        "This will invalidate ALL active bearer tokens. "
        "Any connected MCP clients will lose access immediately.\n\n"
        "You'll need to run /generate\\_key to create a new one.",
        buttons=[
            [Button.inline("✅ Yes, revoke all", data="confirm_revoke")],
            [Button.inline("❌ Cancel", data="cancel_revoke")],
        ],
    )


@bot.on(events.CallbackQuery(pattern=b"confirm_revoke"))
async def confirm_revoke_handler(event):
    if not is_owner(event):
        return
    await event.answer()
    count = auth_manager.revoke_all_tokens()
    await event.edit(f"**Done.** Revoked {count} token(s).\n\nRun /generate\\_key to create a new one.")


@bot.on(events.CallbackQuery(pattern=b"cancel_revoke"))
async def cancel_revoke_handler(event):
    if not is_owner(event):
        return
    await event.answer()
    await event.edit("Cancelled. Tokens remain active.")


@bot.on(events.CallbackQuery(pattern=b"delete_token_msg"))
async def delete_token_msg_handler(event):
    if not is_owner(event):
        return
    await event.answer()
    await event.delete()


@bot.on(events.NewMessage(pattern="/list_keys"))
async def list_keys_handler(event):
    """List all MCP tokens (metadata only)."""
    if not is_owner(event):
        return

    tokens = auth_manager.list_tokens()
    if not tokens:
        await event.respond("No tokens found. Run /generate\\_key to create one.")
        return

    lines = ["**MCP Tokens:**\n"]
    for t in tokens:
        status = "🔴 revoked" if t["revoked"] else "🟢 active"
        last_used = t["last_used_at"] or "never"
        lines.append(
            f"• #{t['id']} ({status}) — created {t['created_at']}, last used {last_used}"
        )

    await event.respond("\n".join(lines))


# ============================================================================
# TELEGRAM AUTH FLOW
# ============================================================================

# Temporary client used during auth flow
_auth_client: TelegramClient | None = None


@bot.on(events.NewMessage(pattern="/auth"))
async def auth_handler(event):
    """Start the Telegram authentication flow."""
    if not is_owner(event):
        return

    # Check if already authenticated via env var (DB sessions can be overwritten)
    if os.getenv("TELEGRAM_SESSION_STRING"):
        await event.respond(
            "**Already authenticated via environment variable.**\n\n"
            "A Telegram session is set in TELEGRAM_SESSION_STRING.\n"
            "Remove it from your Fly secrets first to use bot-managed auth.",
        )
        return

    auth_manager.set_auth_state(event.sender_id, "awaiting_phone")
    await event.respond(
        "**Telegram Authentication**\n\n"
        "Send me your phone number (with country code, e.g. `+44...`).\n\n"
        "Telegram will send a verification code to your Telegram app.\n\n"
        "Type /cancel to abort.",
    )


@bot.on(events.NewMessage(pattern="/cancel"))
async def cancel_auth_handler(event):
    if not is_owner(event):
        return
    global _auth_client
    state = auth_manager.get_auth_state(event.sender_id)
    if state:
        auth_manager.clear_auth_state(event.sender_id)
        if _auth_client:
            try:
                await _auth_client.disconnect()
            except Exception:
                pass
            _auth_client = None
        await event.respond("Authentication cancelled.")
    else:
        await event.respond("Nothing to cancel.")


@bot.on(events.NewMessage())
async def auth_conversation_handler(event):
    """Handle auth conversation flow (phone number, code, 2FA)."""
    if not is_owner(event):
        return

    # Don't process commands
    if event.text and event.text.startswith("/"):
        return

    state = auth_manager.get_auth_state(event.sender_id)
    if not state:
        return

    global _auth_client

    if state["step"] == "awaiting_phone":
        phone = event.text.strip()
        if not phone.startswith("+"):
            await event.respond("Please include the country code (e.g. `+447901...`).")
            return

        # Try to delete the phone number message
        try:
            await event.delete()
        except Exception:
            await event.respond("⚠️ Please delete your phone number message manually for security.")

        try:
            _auth_client = TelegramClient(
                StringSession(), TELEGRAM_API_ID, TELEGRAM_API_HASH
            )
            await _auth_client.connect()
            result = await _auth_client.send_code_request(phone)
            auth_manager.set_auth_state(
                event.sender_id, "awaiting_code", phone=phone,
                phone_code_hash=result.phone_code_hash,
            )
            await event.respond(
                "**Code sent!** Check your Telegram app.\n\n"
                "Send me the verification code.\n\n"
                "⚠️ **Important:** Send the digits with spaces or dashes "
                "(e.g. `1 2 3 4 5`) so Telegram doesn't intercept it.\n\n"
                "⏱ This auth session expires in 5 minutes.",
            )
        except Exception as e:
            auth_manager.clear_auth_state(event.sender_id)
            if _auth_client:
                await _auth_client.disconnect()
                _auth_client = None
            await event.respond(f"**Error sending code:** `{e}`\n\nTry /auth again.")

    elif state["step"] == "awaiting_code":
        # Strip spaces, dashes from code
        code = event.text.strip().replace(" ", "").replace("-", "")

        # Try to delete the message containing the code
        # Note: bots can't always delete user messages in DMs
        try:
            await event.delete()
        except Exception:
            await event.respond("⚠️ Please delete your code message manually for security.")

        try:
            await _auth_client.sign_in(
                phone=state["phone"],
                code=code,
                phone_code_hash=state["phone_code_hash"],
            )

            # Success — store session string internally
            session_string = StringSession.save(_auth_client.session)
            auth_manager.store_session(session_string, owner_id=event.sender_id)
            auth_manager.clear_auth_state(event.sender_id)

            await _auth_client.disconnect()
            _auth_client = None

            await event.respond(
                "**Authentication successful!** ✅\n\n"
                "Session stored securely. Restart the MCP server to use it.",
            )

        except Exception as e:
            error_str = str(e).lower()
            if "password" in error_str or "2fa" in error_str or "srp" in error_str:
                auth_manager.set_auth_state(
                    event.sender_id, "awaiting_2fa",
                    phone=state["phone"],
                    phone_code_hash=state["phone_code_hash"],
                )
                await event.respond(
                    "**2FA password required.**\n\n"
                    "Send me your two-factor authentication password.\n\n"
                    "⚠️ The message will be deleted immediately for security.",
                )
            else:
                auth_manager.clear_auth_state(event.sender_id)
                if _auth_client:
                    await _auth_client.disconnect()
                    _auth_client = None
                await event.respond(f"**Sign-in failed:** `{e}`\n\nTry /auth again.")

    elif state["step"] == "awaiting_2fa":
        password = event.text.strip()

        # Try to delete the password message
        try:
            await event.delete()
        except Exception:
            await event.respond("⚠️ Please delete your password message manually for security.")

        try:
            await _auth_client.sign_in(password=password)

            session_string = StringSession.save(_auth_client.session)
            auth_manager.store_session(session_string, owner_id=event.sender_id)
            auth_manager.clear_auth_state(event.sender_id)

            await _auth_client.disconnect()
            _auth_client = None

            await event.respond(
                "**Authentication successful!** ✅\n\n"
                "Session stored securely. Restart the MCP server to use it.",
            )

        except Exception as e:
            auth_manager.clear_auth_state(event.sender_id)
            if _auth_client:
                await _auth_client.disconnect()
                _auth_client = None
            await event.respond(f"**2FA sign-in failed:** `{e}`\n\nTry /auth again.")


async def main():
    """Start both bot and user clients."""
    if not BOT_TOKEN:
        print("Error: PERMISSIONS_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    if not OWNER_ID:
        print("Error: PERMISSIONS_BOT_OWNER_ID not set", file=sys.stderr)
        sys.exit(1)

    # Initialise databases
    permissions.init_db()
    auth_manager.init_auth_db()

    # Start user client (for fetching chat list)
    await user_client.start()

    # Start bot client
    await bot.start(bot_token=BOT_TOKEN)

    print(f"Permissions bot started. Only responding to user ID: {OWNER_ID}")
    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
