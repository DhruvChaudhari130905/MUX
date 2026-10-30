"""Tests for account plan API (mux/api/account.py)."""

import time
from unittest.mock import AsyncMock, patch

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
    with TestClient(create_app()) as c:
        yield c


def test_read_plan_default(c):
    r = c.get("/me/plan", headers=auth("alice"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "plan" in body and "rooms_owned" in body


def test_change_plan_requires_auth(c):
    r = c.put("/me/plan", json={"plan": "pro"})
    assert r.status_code == 401


def test_change_plan_invalid(c):
    r = c.put("/me/plan", json={"plan": "no-such-plan"}, headers=auth("alice"))
    assert r.status_code in (400, 422)
