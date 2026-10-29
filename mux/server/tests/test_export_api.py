"""Tests for checkpoint + GitHub export API (mux/api/export.py)."""

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
    monkeypatch.setattr(settings, "github_client_id", "cid")
    monkeypatch.setattr(settings, "github_client_secret", "csecret")
    monkeypatch.setattr("mux.integrations.github._github_integration", None)
    with TestClient(create_app()) as c:
        yield c


def create_room(c, owner="alice"):
    r = c.post("/rooms", json={"description": "demo app"}, headers=auth(owner))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def c_post(c, path, body, user):
    return c.post(path, json=body, headers=auth(user))


# ---- checkpoints ----

def test_create_and_list_checkpoint(c):
    rid = create_room(c, "alice")
    r = c_post(c, f"/api/export/{rid}/checkpoints", {"description": "after task 1"}, "alice")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["checkpoint_id"] and body["description"] == "after task 1"
    assert body["created_by"] == "alice"

    r = c.get(f"/api/export/{rid}/checkpoints", headers=auth("alice"))
    assert r.status_code == 200
    assert len(r.json()["checkpoints"]) == 1


def test_non_owner_cannot_create_checkpoint(c):
    rid = create_room(c, "alice")
    r = c_post(c, f"/api/export/{rid}/checkpoints", {}, "bob")
    assert r.status_code == 403


def test_get_and_restore_checkpoint(c):
    rid = create_room(c, "alice")
    r = c_post(c, f"/api/export/{rid}/checkpoints", {"description": "cp1"}, "alice")
    cp_id = r.json()["checkpoint_id"]

    r = c.get(f"/api/export/{rid}/checkpoints/{cp_id}", headers=auth("alice"))
    assert r.status_code == 200

    r = c.post(f"/api/export/{rid}/checkpoints/{cp_id}/restore", headers=auth("alice"))
    assert r.status_code == 200
    assert r.json()["restored"] is True


def test_non_owner_cannot_restore(c):
    rid = create_room(c, "alice")
    r = c_post(c, f"/api/export/{rid}/checkpoints", {}, "alice")
    cp_id = r.json()["checkpoint_id"]
    r = c.post(f"/api/export/{rid}/checkpoints/{cp_id}/restore", headers=auth("bob"))
    assert r.status_code == 403


# ---- GitHub export ----

def test_export_requires_token(c):
    rid = create_room(c, "alice")
    c_post(c, f"/api/export/{rid}/checkpoints", {}, "alice")
    r = c_post(c, f"/api/export/{rid}/export", {
        "github_owner": "alice", "github_repo": "repo",
    }, "alice")
    assert r.status_code == 400


def test_export_requires_owner(c):
    rid = create_room(c, "alice")
    r = c_post(c, f"/api/export/{rid}/export", {
        "github_owner": "alice", "github_repo": "repo",
    }, "bob")
    assert r.status_code == 403


def test_export_no_checkpoints(c):
    rid = create_room(c, "alice")
    r = c_post(c, f"/api/export/{rid}/export", {
        "github_owner": "alice", "github_repo": "repo",
    }, "alice")
    assert r.status_code == 400


# ---- GitHub OAuth connect / status / disconnect ----

def test_github_connect_returns_auth_url(c):
    r = c.get("/api/export/github/connect", headers=auth("alice"))
    assert r.status_code == 200
    assert "github.com/login/oauth/authorize" in r.json()["auth_url"]
    assert r.json()["state"]


def test_github_status_not_connected(c):
    r = c.get("/api/export/github/status", headers=auth("alice"))
    assert r.status_code == 200
    assert r.json()["connected"] is False


def test_github_disconnect(c):
    r = c.post("/api/export/github/disconnect", headers=auth("alice"))
    assert r.status_code == 200
    assert r.json()["disconnected"] is True
