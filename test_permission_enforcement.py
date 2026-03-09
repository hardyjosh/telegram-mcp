"""Test permission enforcement without Telegram credentials.

Tests the permission checking logic, tool permission map building,
and the check_permission function directly.
"""

import os
import sys
import tempfile

# Use a temp DB so we don't touch any real data
_temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["PERMISSIONS_DB_PATH"] = _temp_db.name

import permissions as perms


def setup():
    """Reset DB for each test group."""
    # Recreate tables
    conn = perms._get_conn()
    conn.executescript("DROP TABLE IF EXISTS global_permissions; DROP TABLE IF EXISTS chat_allowlist;")
    conn.close()
    perms.init_db()


def test_defaults():
    """Only read should be enabled by default."""
    setup()
    gp = perms.get_global_permissions()
    assert gp["read"] == True, f"read should be True, got {gp['read']}"
    assert gp["write"] == False, f"write should be False, got {gp['write']}"
    assert gp["drafts"] == False, f"drafts should be False, got {gp['drafts']}"
    assert gp["contacts"] == False, f"contacts should be False, got {gp['contacts']}"
    assert gp["groups"] == False, f"groups should be False, got {gp['groups']}"
    assert gp["profile"] == False, f"profile should be False, got {gp['profile']}"
    assert gp["privacy"] == False, f"privacy should be False, got {gp['privacy']}"
    print("PASS: defaults")


def test_tool_permission_map():
    """Test that the permission map is built correctly from annotations."""
    setup()
    tools_info = [
        {"name": "list_messages", "annotations": {"readOnlyHint": True, "destructiveHint": False}},
        {"name": "send_message", "annotations": {"readOnlyHint": False, "destructiveHint": True}},
        {"name": "save_draft", "annotations": {"readOnlyHint": False, "destructiveHint": False}},
        {"name": "list_contacts", "annotations": {"readOnlyHint": True, "destructiveHint": False}},
        {"name": "add_contact", "annotations": {"readOnlyHint": False, "destructiveHint": True}},
        {"name": "create_group", "annotations": {"readOnlyHint": False, "destructiveHint": True}},
        {"name": "update_profile", "annotations": {"readOnlyHint": False, "destructiveHint": True}},
        {"name": "get_privacy_settings", "annotations": {"readOnlyHint": True, "destructiveHint": False}},
        {"name": "get_me", "annotations": {"readOnlyHint": True, "destructiveHint": False}},
    ]
    perms.build_tool_permission_map(tools_info)

    assert perms.TOOL_PERMISSION_MAP["list_messages"] == perms.Permission.READ
    assert perms.TOOL_PERMISSION_MAP["send_message"] == perms.Permission.WRITE
    assert perms.TOOL_PERMISSION_MAP["save_draft"] == perms.Permission.DRAFTS
    assert perms.TOOL_PERMISSION_MAP["list_contacts"] == perms.Permission.CONTACTS
    assert perms.TOOL_PERMISSION_MAP["add_contact"] == perms.Permission.CONTACTS
    assert perms.TOOL_PERMISSION_MAP["create_group"] == perms.Permission.GROUPS
    assert perms.TOOL_PERMISSION_MAP["update_profile"] == perms.Permission.PROFILE
    assert perms.TOOL_PERMISSION_MAP["get_privacy_settings"] == perms.Permission.PRIVACY
    # get_me is not in any override, so it should be READ from annotations
    assert perms.TOOL_PERMISSION_MAP["get_me"] == perms.Permission.READ
    print("PASS: tool_permission_map")


def test_write_blocked_globally():
    """Write tools should be blocked when write is disabled."""
    setup()
    perms.build_tool_permission_map([
        {"name": "send_message", "annotations": {"readOnlyHint": False, "destructiveHint": True}},
    ])
    allowed, reason = perms.check_permission("send_message", chat_id=None)
    assert not allowed, "send_message should be blocked"
    assert "write" in reason.lower()
    print("PASS: write_blocked_globally")


def test_drafts_blocked_globally():
    setup()
    perms.build_tool_permission_map([
        {"name": "save_draft", "annotations": {"readOnlyHint": False, "destructiveHint": False}},
    ])
    allowed, reason = perms.check_permission("save_draft", chat_id=None)
    assert not allowed, "save_draft should be blocked"
    assert "drafts" in reason.lower()
    print("PASS: drafts_blocked_globally")


def test_contacts_blocked_globally():
    setup()
    perms.build_tool_permission_map([
        {"name": "list_contacts", "annotations": {"readOnlyHint": True, "destructiveHint": False}},
    ])
    allowed, reason = perms.check_permission("list_contacts", chat_id=None)
    assert not allowed, "list_contacts should be blocked"
    assert "contacts" in reason.lower()
    print("PASS: contacts_blocked_globally")


def test_groups_blocked_globally():
    setup()
    perms.build_tool_permission_map([
        {"name": "create_group", "annotations": {"readOnlyHint": False, "destructiveHint": True}},
    ])
    allowed, reason = perms.check_permission("create_group", chat_id=None)
    assert not allowed, "create_group should be blocked"
    assert "groups" in reason.lower()
    print("PASS: groups_blocked_globally")


def test_profile_blocked_globally():
    setup()
    perms.build_tool_permission_map([
        {"name": "update_profile", "annotations": {"readOnlyHint": False, "destructiveHint": True}},
    ])
    allowed, reason = perms.check_permission("update_profile", chat_id=None)
    assert not allowed, "update_profile should be blocked"
    assert "profile" in reason.lower()
    print("PASS: profile_blocked_globally")


def test_privacy_blocked_globally():
    setup()
    perms.build_tool_permission_map([
        {"name": "get_privacy_settings", "annotations": {"readOnlyHint": True, "destructiveHint": False}},
    ])
    allowed, reason = perms.check_permission("get_privacy_settings", chat_id=None)
    assert not allowed, "get_privacy_settings should be blocked"
    assert "privacy" in reason.lower()
    print("PASS: privacy_blocked_globally")


def test_read_allowed_with_allowlisted_chat():
    """Read should work for allowlisted chats when read is enabled."""
    setup()
    perms.build_tool_permission_map([
        {"name": "list_messages", "annotations": {"readOnlyHint": True, "destructiveHint": False}},
    ])
    perms.add_chat(12345, "Test Chat")
    allowed, reason = perms.check_permission("list_messages", chat_id=12345)
    assert allowed, f"list_messages should be allowed for allowlisted chat, got: {reason}"
    print("PASS: read_allowed_with_allowlisted_chat")


def test_read_blocked_non_allowlisted_chat():
    """Read should be blocked for non-allowlisted chats."""
    setup()
    perms.build_tool_permission_map([
        {"name": "list_messages", "annotations": {"readOnlyHint": True, "destructiveHint": False}},
    ])
    allowed, reason = perms.check_permission("list_messages", chat_id=99999)
    assert not allowed, "list_messages should be blocked for non-allowlisted chat"
    assert "allowlist" in reason.lower()
    print("PASS: read_blocked_non_allowlisted_chat")


def test_write_blocked_even_if_allowlisted():
    """Write should still be blocked globally even for allowlisted chats."""
    setup()
    perms.build_tool_permission_map([
        {"name": "send_message", "annotations": {"readOnlyHint": False, "destructiveHint": True}},
    ])
    perms.add_chat(12345, "Test Chat")
    allowed, reason = perms.check_permission("send_message", chat_id=12345)
    assert not allowed, "send_message should be blocked (write disabled globally)"
    print("PASS: write_blocked_even_if_allowlisted")


def test_write_allowed_when_enabled_and_allowlisted():
    """Write should work when globally enabled AND chat is allowlisted."""
    setup()
    perms.build_tool_permission_map([
        {"name": "send_message", "annotations": {"readOnlyHint": False, "destructiveHint": True}},
    ])
    perms.toggle_global_permission("write")  # enable write
    perms.add_chat(12345, "Test Chat")
    allowed, reason = perms.check_permission("send_message", chat_id=12345)
    assert allowed, f"send_message should be allowed, got: {reason}"
    print("PASS: write_allowed_when_enabled_and_allowlisted")


def test_write_blocked_non_allowlisted_even_when_enabled():
    """Write should be blocked for non-allowlisted chats even when write is globally on."""
    setup()
    perms.build_tool_permission_map([
        {"name": "send_message", "annotations": {"readOnlyHint": False, "destructiveHint": True}},
    ])
    perms.toggle_global_permission("write")  # enable write
    allowed, reason = perms.check_permission("send_message", chat_id=99999)
    assert not allowed, "send_message should be blocked for non-allowlisted chat"
    assert "allowlist" in reason.lower()
    print("PASS: write_blocked_non_allowlisted_even_when_enabled")


def test_unknown_tool_with_chat_id_checks_allowlist():
    """Tools not in the permission map should still check the allowlist if they have a chat_id."""
    setup()
    allowed, reason = perms.check_permission("some_unknown_tool", chat_id=99999)
    assert not allowed, "unknown tool should be blocked for non-allowlisted chat"
    print("PASS: unknown_tool_checks_allowlist")


def test_unknown_tool_no_chat_id_allowed():
    """Tools not in the permission map with no chat_id should be allowed."""
    setup()
    allowed, reason = perms.check_permission("some_unknown_tool", chat_id=None)
    assert allowed, f"unknown tool without chat_id should be allowed, got: {reason}"
    print("PASS: unknown_tool_no_chat_id_allowed")


def test_disabled_chat_in_allowlist():
    """A chat that's in the allowlist but disabled should be blocked."""
    setup()
    perms.build_tool_permission_map([
        {"name": "list_messages", "annotations": {"readOnlyHint": True, "destructiveHint": False}},
    ])
    perms.add_chat(12345, "Test Chat")
    perms.toggle_chat(12345)  # disable it
    allowed, reason = perms.check_permission("list_messages", chat_id=12345)
    assert not allowed, "list_messages should be blocked for disabled chat"
    print("PASS: disabled_chat_in_allowlist")


if __name__ == "__main__":
    tests = [
        test_defaults,
        test_tool_permission_map,
        test_write_blocked_globally,
        test_drafts_blocked_globally,
        test_contacts_blocked_globally,
        test_groups_blocked_globally,
        test_profile_blocked_globally,
        test_privacy_blocked_globally,
        test_read_allowed_with_allowlisted_chat,
        test_read_blocked_non_allowlisted_chat,
        test_write_blocked_even_if_allowlisted,
        test_write_allowed_when_enabled_and_allowlisted,
        test_write_blocked_non_allowlisted_even_when_enabled,
        test_unknown_tool_with_chat_id_checks_allowlist,
        test_unknown_tool_no_chat_id_allowed,
        test_disabled_chat_in_allowlist,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"FAIL: {test.__name__}: {e}")
            failed += 1

    print(f"\n{passed}/{passed + failed} passed")
    os.unlink(_temp_db.name)
    sys.exit(1 if failed else 0)
