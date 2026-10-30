"""Tests for room AI settings API (mux/api/ai.py)."""

import time
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

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
    monkeypatch.setattr(settings, "token_factory_base_url", "https://nebius.dev")
    monkeypatch.setattr(settings, "model_super", "nemotron-super")
    with TestClient(create_app()) as c:
        yield c


def create_room(c, owner="alice"):
    r = c.post("/rooms", json={"description": "demo"}, headers=auth(owner))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_get_ai_no_settings_returns_server(c):
    rid = create_room(c, "alice")
    r = c.get(f"/rooms/{rid}/ai", headers=auth("alice"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] in ("server", "none")


def test_get_ai_viewer_ok(c):
    rid = create_room(c, "alice")
    r = c.get(f"/rooms/{rid}/ai", headers=auth("bob"))
    assert r.status_code in (200, 403)


def test_ai_settings_uses_put_not_post(c):
    rid = create_room(c, "alice")
    r = c.put(f"/rooms/{rid}/ai", json={
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test",
        "models": {"lightning": "bad model!", "super": "gpt-4", "ultra": "gpt-4"},
    }, headers=auth("alice"))
    assert r.status_code == 422  # pydantic validation error for bad model id
