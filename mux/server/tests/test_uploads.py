"""Tests for room image uploads (mux/rooms/uploads.py)."""

import tempfile
from pathlib import Path

import pytest

from mux.rooms.uploads import MAX_UPLOAD_BYTES, UploadStore, room_uploads, sniff_image


# ---------------------------------------------------------------------------
# sniff_image
# ---------------------------------------------------------------------------

def test_sniff_png():
    assert sniff_image(b"\x89PNG\r\n\x1a\nrest") == "png"


def test_sniff_jpg():
    assert sniff_image(b"\xff\xd8\xffrest") == "jpg"


def test_sniff_gif():
    assert sniff_image(b"GIF87a rest") == "gif"
    assert sniff_image(b"GIF89a rest") == "gif"


def test_sniff_webp():
    assert sniff_image(b"RIFF....WEBP") == "webp"


def test_sniff_unknown_returns_none():
    assert sniff_image(b"hello world") is None
    assert sniff_image(b"") is None


# ---------------------------------------------------------------------------
# UploadStore
# ---------------------------------------------------------------------------

def test_save_and_find_round_trip(tmp_path):
    store = UploadStore(tmp_path)
    png = b"\x89PNG\r\n\x1a\n" + b"payload"
    upload_id, content_type = store.save(png)
    assert content_type == "image/png"
    found = store.find(upload_id)
    assert found is not None
    path, ct = found
    assert path.read_bytes() == png
    assert ct == "image/png"


def test_save_rejects_non_image(tmp_path):
    store = UploadStore(tmp_path)
    with pytest.raises(ValueError, match="Only PNG"):
        store.save(b"not an image")


def test_find_unknown_id_returns_none(tmp_path):
    store = UploadStore(tmp_path)
    assert store.find("0" * 32) is None


def test_find_malformed_id_returns_none(tmp_path):
    store = UploadStore(tmp_path)
    assert store.find("not-a-hex-id!!!") is None


def test_save_jpg_and_webp(tmp_path):
    store = UploadStore(tmp_path)
    _, ct = store.save(b"\xff\xd8\xffx")
    assert ct == "image/jpeg"
    _, ct = store.save(b"RIFF....WEBP")
    assert ct == "image/webp"


# ---------------------------------------------------------------------------
# room_uploads uses cwd
# ---------------------------------------------------------------------------

def test_room_uploads_path(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    store = room_uploads("room-1")
    assert store.root == tmp_path / ".mux" / "uploads" / "room-1"
