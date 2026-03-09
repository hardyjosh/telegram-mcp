"""Telegram bot for managing MCP server chat permissions.

Provides an inline keyboard interface for:
1. Toggling global permissions (read/write)
2. Browsing and toggling chat allowlist

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
import time

from dotenv import load_dotenv
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession

import permissions

load_dotenv()

# Bot configuration
BOT_TOKEN = os.getenv("PERMISSIONS_BOT_TOKEN")
OWNER_ID = int(os.getenv("PERMISSIONS_BOT_OWNER_ID", "0"))
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")

# For fetching the user's chat list, we need their Telethon client
SESSION_STRING = os.getenv("TELEGRAM_SESSION_STRING")
TELEGRAM_SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME")

CHATS_PER_PAGE = 8
CHAT_CACHE_TTL = 300  # 5 minutes

# Cached chat list
_chat_cache: list[dict] = []
_chat_cache_time: float = 0

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
    global _chat_cache, _chat_cache_time
    if not force_refresh and _chat_cache and (time.time() - _chat_cache_time) < CHAT_CACHE_TTL:
        return _chat_cache
    dialogs = await user_client.get_dialogs()
    chats = []
    for dialog in dialogs:
        entity = dialog.entity
        chat_id = entity.id
        title = getattr(entity, "title", None) or getattr(entity, "first_name", "Unknown")
        chats.append({"chat_id": chat_id, "title": title})
    _chat_cache = chats
    _chat_cache_time = time.time()
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

    # Back button
    buttons.append([Button.inline("\u2b05\ufe0f Back to Permissions", data="home")])

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
    await event.edit(
        "**Select chats to allow access:**\n\n"
        "\u2705 = allowed, \u2b1c = not allowed",
        buttons=keyboard,
    )


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


@bot.on(events.CallbackQuery(pattern=b"noop"))
async def noop_handler(event):
    """Handle no-op button presses (page indicator)."""
    await event.answer()


async def main():
    """Start both bot and user clients."""
    if not BOT_TOKEN:
        print("Error: PERMISSIONS_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    if not OWNER_ID:
        print("Error: PERMISSIONS_BOT_OWNER_ID not set", file=sys.stderr)
        sys.exit(1)

    # Initialise permissions database
    permissions.init_db()

    # Start user client (for fetching chat list)
    await user_client.start()

    # Start bot client
    await bot.start(bot_token=BOT_TOKEN)

    print(f"Permissions bot started. Only responding to user ID: {OWNER_ID}")
    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
