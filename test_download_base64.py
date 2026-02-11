import pytest
import os
import json
import base64
import tempfile
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["TELEGRAM_API_ID"] = "12345"
os.environ["TELEGRAM_API_HASH"] = "dummy_hash"

from main import download_media_base64


def _make_mock_message(content, filename="report.pdf", mime_type="application/pdf"):
    """Create a mock Telegram message with document media."""
    attr = MagicMock()
    attr.file_name = filename

    doc = MagicMock()
    doc.attributes = [attr]
    doc.mime_type = mime_type

    media = MagicMock()
    media.document = doc

    msg = MagicMock()
    msg.media = media

    return msg


def _make_mock_client(msg, content):
    """Create a mock Telegram client that writes content to the download path."""
    mock_client = AsyncMock()
    mock_client.get_entity = AsyncMock(return_value=MagicMock())
    mock_client.get_messages = AsyncMock(return_value=msg)

    async def fake_download(message, file=None):
        with open(file, "wb") as f:
            f.write(content)

    mock_client.download_media = fake_download
    return mock_client


@pytest.mark.asyncio
async def test_basic_pdf_download():
    """Download a PDF and verify base64 round-trip."""
    content = b"%PDF-1.4 fake content" + os.urandom(1000)
    msg = _make_mock_message(content, filename="Report_test.pdf")
    mock_client = _make_mock_client(msg, content)

    with patch("main.client", mock_client):
        result = await download_media_base64(chat_id=123, message_id=1)

    parsed = json.loads(result)
    assert parsed["filename"] == "Report_test.pdf"
    assert parsed["mime_type"] == "application/pdf"
    assert parsed["size_bytes"] == len(content)
    assert base64.b64decode(parsed["base64_data"]) == content


@pytest.mark.asyncio
async def test_no_media_returns_message():
    """Return a string (not JSON) when message has no media."""
    msg = MagicMock()
    msg.media = None

    mock_client = AsyncMock()
    mock_client.get_entity = AsyncMock(return_value=MagicMock())
    mock_client.get_messages = AsyncMock(return_value=msg)

    with patch("main.client", mock_client):
        result = await download_media_base64(chat_id=123, message_id=1)

    assert "No media" in result


@pytest.mark.asyncio
async def test_no_message_returns_message():
    """Return a string when message doesn't exist."""
    mock_client = AsyncMock()
    mock_client.get_entity = AsyncMock(return_value=MagicMock())
    mock_client.get_messages = AsyncMock(return_value=None)

    with patch("main.client", mock_client):
        result = await download_media_base64(chat_id=123, message_id=1)

    assert "No media" in result


@pytest.mark.asyncio
async def test_temp_file_cleanup():
    """Verify temp files are cleaned up after successful download."""
    content = b"test content"
    msg = _make_mock_message(content)
    mock_client = _make_mock_client(msg, content)

    created_temps = []
    original_ntf = tempfile.NamedTemporaryFile

    def tracking_ntf(**kwargs):
        f = original_ntf(**kwargs)
        created_temps.append(f.name)
        return f

    with (
        patch("main.client", mock_client),
        patch("main.tempfile.NamedTemporaryFile", side_effect=tracking_ntf),
    ):
        await download_media_base64(chat_id=123, message_id=1)

    for path in created_temps:
        assert not os.path.exists(path), f"Temp file not cleaned up: {path}"


@pytest.mark.asyncio
async def test_large_file():
    """Verify a larger file (2MB, typical audit PDF size) encodes correctly."""
    content = os.urandom(2 * 1024 * 1024)
    msg = _make_mock_message(content, filename="large_report.pdf")
    mock_client = _make_mock_client(msg, content)

    with patch("main.client", mock_client):
        result = await download_media_base64(chat_id=123, message_id=1)

    parsed = json.loads(result)
    assert parsed["size_bytes"] == len(content)
    assert base64.b64decode(parsed["base64_data"]) == content


@pytest.mark.asyncio
async def test_image_media():
    """Verify it works for non-PDF media (e.g. photos)."""
    content = b"\x89PNG\r\n\x1a\n" + os.urandom(500)
    msg = _make_mock_message(content, filename="photo.png", mime_type="image/png")
    mock_client = _make_mock_client(msg, content)

    with patch("main.client", mock_client):
        result = await download_media_base64(chat_id=123, message_id=1)

    parsed = json.loads(result)
    assert parsed["filename"] == "photo.png"
    assert parsed["mime_type"] == "image/png"
    assert base64.b64decode(parsed["base64_data"]) == content


@pytest.mark.asyncio
async def test_fallback_mime_type():
    """When filename has no extension, fall back to document mime_type."""
    content = b"some binary data"

    attr = MagicMock()
    attr.file_name = "noextension"

    doc = MagicMock()
    doc.attributes = [attr]
    doc.mime_type = "application/octet-stream"

    media = MagicMock()
    media.document = doc

    msg = MagicMock()
    msg.media = media

    mock_client = _make_mock_client(msg, content)

    with patch("main.client", mock_client):
        result = await download_media_base64(chat_id=123, message_id=1)

    parsed = json.loads(result)
    assert parsed["mime_type"] == "application/octet-stream"
