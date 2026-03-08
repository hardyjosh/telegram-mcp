"""Tests for the permissions module."""

import os
import tempfile

import pytest

import permissions


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    """Use a temporary database for each test."""
    db_path = str(tmp_path / "test_permissions.db")
    monkeypatch.setenv("TELEGRAM_MCP_PERMISSIONS_DB", db_path)
    permissions.init_db()
    yield db_path


class TestGlobalPermissions:
    def test_default_permissions(self):
        """Default permissions: read=True, write=False."""
        perms = permissions.get_global_permissions()
        assert perms["read"] is True
        assert perms["write"] is False

    def test_toggle_permission(self):
        """Toggle flips the state."""
        # read starts True
        new_state = permissions.toggle_global_permission("read")
        assert new_state is False

        new_state = permissions.toggle_global_permission("read")
        assert new_state is True

    def test_set_permission(self):
        """Explicitly set a permission."""
        permissions.set_global_permission("write", True)
        perms = permissions.get_global_permissions()
        assert perms["write"] is True


class TestChatAllowlist:
    def test_add_and_list(self):
        """Add a chat and verify it appears in the list."""
        permissions.add_chat(12345, "Test Chat")
        chats = permissions.get_allowlisted_chats()
        assert len(chats) == 1
        assert chats[0]["chat_id"] == 12345
        assert chats[0]["chat_title"] == "Test Chat"
        assert chats[0]["enabled"] is True

    def test_toggle_chat(self):
        """Toggle a chat's enabled state."""
        permissions.add_chat(12345, "Test Chat")
        new_state = permissions.toggle_chat(12345)
        assert new_state is False

        chats = permissions.get_allowlisted_chats()
        assert chats[0]["enabled"] is False

    def test_remove_chat(self):
        """Remove a chat from the allowlist."""
        permissions.add_chat(12345, "Test Chat")
        permissions.remove_chat(12345)
        chats = permissions.get_allowlisted_chats()
        assert len(chats) == 0

    def test_is_chat_allowed(self):
        """Check allowlist membership."""
        assert permissions.is_chat_allowed(12345) is False
        permissions.add_chat(12345, "Test Chat")
        assert permissions.is_chat_allowed(12345) is True
        permissions.toggle_chat(12345)
        assert permissions.is_chat_allowed(12345) is False


class TestCheckPermission:
    def test_unknown_tool_always_allowed(self):
        """Tools not in the permission map are always allowed."""
        allowed, reason = permissions.check_permission("unknown_tool", 12345)
        assert allowed is True

    def test_read_tool_allowed_when_chat_in_allowlist(self):
        """Read tool allowed when global read is on and chat is allowlisted."""
        permissions.TOOL_PERMISSION_MAP["get_messages"] = permissions.Permission.READ
        permissions.add_chat(12345, "Test Chat")

        allowed, reason = permissions.check_permission("get_messages", 12345)
        assert allowed is True

    def test_read_tool_denied_when_chat_not_in_allowlist(self):
        """Read tool denied when chat is not allowlisted."""
        permissions.TOOL_PERMISSION_MAP["get_messages"] = permissions.Permission.READ

        allowed, reason = permissions.check_permission("get_messages", 12345)
        assert allowed is False
        assert "not in the allowlist" in reason

    def test_write_tool_denied_when_global_write_off(self):
        """Write tool denied when global write permission is off."""
        permissions.TOOL_PERMISSION_MAP["send_message"] = permissions.Permission.WRITE
        permissions.add_chat(12345, "Test Chat")

        allowed, reason = permissions.check_permission("send_message", 12345)
        assert allowed is False
        assert "disabled" in reason

    def test_write_tool_allowed_when_global_write_on(self):
        """Write tool allowed when global write is on and chat is allowlisted."""
        permissions.TOOL_PERMISSION_MAP["send_message"] = permissions.Permission.WRITE
        permissions.set_global_permission("write", True)
        permissions.add_chat(12345, "Test Chat")

        allowed, reason = permissions.check_permission("send_message", 12345)
        assert allowed is True

    def test_no_chat_id_allowed_if_global_perm_on(self):
        """Tools with no chat_id are allowed if global perm is enabled."""
        permissions.TOOL_PERMISSION_MAP["list_contacts"] = permissions.Permission.READ

        allowed, reason = permissions.check_permission("list_contacts", None)
        assert allowed is True

    def test_username_based_chat_id_allowed(self):
        """Username-based chat IDs can't be filtered by numeric allowlist."""
        permissions.TOOL_PERMISSION_MAP["get_messages"] = permissions.Permission.READ

        allowed, reason = permissions.check_permission("get_messages", "@some_chat")
        assert allowed is True
