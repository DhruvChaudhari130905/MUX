"""Tests for uploads API (mux/api/uploads.py)."""

import time
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient
from io import BytesIO

from mux.config import settings
from mux.main import create_app

SECRET = "test-secret-that-is-at-least-32-bytes"


def token(sub: str, aud: str | None = "authenticated") -> str:
    payload = {"sub": sub, "role": "authenticated", "exp": int(time.time()) + 3600}
    if aud:
        payload["aud"] = aud
    return jwt.encode(payload, SECRET, algorithm="HS256")


def auth(sub: str):
    return {"Authorization": f"Bearer {token(sub)}"}


@pytest.fixture
def c(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "supabase_jwt_secret", SECRET)
    monkeypatch.setattr(settings, "supabase_jwt_audience", "")
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{tmp_path}/test.db")
    monkeypatch.setattr(settings, "token_factory_api_key", "")
    with TestClient(create_app()) as c:
        yield c


def create_room(c, owner="alice"):
    r = c.post("/rooms", json={"description": "demo"}, headers=auth(owner))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_upload_png(c):
    rid = create_room(c, "alice")
    files = {"file": ("img.png", BytesIO(b"\x89PNG\r\n\x1a\npayload"), "image/png")}
    r = c.post(f"/rooms/{rid}/uploads", files=files, headers=auth("alice"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] and body["content_type"] == "image/png"

    # fetch it back
    r = c.get(f"/rooms/{rid}/uploads/{body['id']}", headers=auth("alice"))
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


def test_upload_rejects_non_image(c):
    rid = create_room(c, "alice")
    files = {"file": ("note.txt", BytesIO(b"hello"), "text/plain")}
    r = c.post(f"/rooms/{rid}/uploads", files=files, headers=auth("alice"))
    assert r.status_code == 415


def test_upload_too_large(c):
    rid = create_room(c, "alice")
    big = b"x" * (5 * 1024 * 1024 + 1)
    files = {"file": ("big.png", BytesIO(b"\x89PNG\r\n\x1a\n" + big), "image/png")}
    r = c.post(f"/rooms/{rid}/uploads", files=files, headers=auth("alice"))
    assert r.status_code == 413


def test_upload_unknown_id_404(c):
    rid = create_room(c, "alice")
    r = c.get(f"/rooms/{rid}/uploads/{"0"*32}", headers=auth("alice"))
    assert r.status_code == 404


def test_viewer_can_get_upload(c):
    rid = create_room(c, "alice")
    files = {"file": ("img.png", BytesIO(b"\x89PNG\r\n\x1a\npayload"), "image/png")}
    r = c.post(f"/rooms/{rid}/uploads", files=files, headers=auth("alice"))
    uid = r.json()["id"]
    r = c.get(f"/rooms/{rid}/uploads/{uid}", headers=auth("alice"))
    assert r.status_code == 200
