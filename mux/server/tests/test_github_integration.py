"""Tests for GitHub OAuth, token encryption, and checkpoint export (mux/integrations/github.py)."""

import base64
import time
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet

from mux.integrations.github import (
    GitHubExportResult,
    GitHubIntegration,
    GitHubOAuthConfig,
    GitHubTokenData,
    _blob_body,
    get_github_integration,
)


# ---------------------------------------------------------------------------
# OAuth URL
# ---------------------------------------------------------------------------

def test_authorization_url_and_state():
    gi = GitHubIntegration(encryption_key=Fernet.generate_key())
    url, state = gi.get_authorization_url("user-1", return_to="/room/abc")
    assert state and len(state) >= 32
    assert "client_id=" in url and "redirect_uri=" in url and "state=" + state in url
    assert "allow_signup=true" in url
    # state is single-use
    assert gi.consume_state(state) == "user-1"
    assert gi.consume_state(state) is None


def test_authorization_url_requires_client_id(monkeypatch):
    monkeypatch.setattr("mux.integrations.github.settings.github_client_id", "")
    monkeypatch.setattr("mux.integrations.github.settings.github_client_secret", "")
    gi = GitHubIntegration.__new__(GitHubIntegration)
    gi.config = GitHubOAuthConfig(client_id="", client_secret="", redirect_uri="", scopes=["repo"])
    with pytest.raises(RuntimeError, match="not configured"):
        gi.get_authorization_url("user-1")


# ---------------------------------------------------------------------------
# Token encrypt / decrypt / store
# ---------------------------------------------------------------------------

def test_token_round_trip():
    key = Fernet.generate_key()
    gi = GitHubIntegration(encryption_key=key)
    token = GitHubTokenData(access_token="gho_secret", scope="repo,user:email")
    gi.store_token("user-1", token, username="alice")
    got = gi.get_token("user-1")
    assert got.access_token == "gho_secret"
    assert got.scope == "repo,user:email"
    assert gi.has_token("user-1") is True
    assert gi.get_connection_status("user-1")["connected"] is True
    assert gi.get_connection_status("user-1")["username"] == "alice"


def test_token_decryption_fails_returns_none():
    key = Fernet.generate_key()
    gi = GitHubIntegration(encryption_key=key)
    gi._token_store["user-1"] = "not-valid-fernet-data"
    assert gi.get_token("user-1") is None


def test_delete_token():
    key = Fernet.generate_key()
    gi = GitHubIntegration(encryption_key=key)
    gi.store_token("user-1", GitHubTokenData(access_token="x"))
    assert gi.delete_token("user-1") is True
    assert gi.delete_token("user-1") is False
    assert gi.has_token("user-1") is False


# ---------------------------------------------------------------------------
# _blob_body: data URLs for binary, utf-8 for text
# ---------------------------------------------------------------------------

def test_blob_body_text_and_binary():
    text = _blob_body("const x = 1\n")
    assert text == {"content": "const x = 1\n", "encoding": "utf-8"}

    # binary data URL -> base64 blob
    data_url = "data:image/png;base64,iVBORw0KGgo="
    data = base64.b64decode("iVBORw0KGgo=")
    body = _blob_body(data_url)
    assert body["encoding"] == "base64"
    assert base64.b64decode(body["content"]) == data


# ---------------------------------------------------------------------------
# exchange_code_for_token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exchange_code_for_token(monkeypatch):
    gi = GitHubIntegration(encryption_key=Fernet.generate_key())
    gi.config.client_id = "cid"
    gi.config.client_secret = "csecret"
    gi.config.redirect_uri = "http://localhost/cb"

    captured = {}

    class FakeResp:
        def __init__(self, data, status=200):
            self._data = data
            self.status_code = status
        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception("bad")
        def json(self):
            return self._data

    async def fake_post(url, *, data, headers, timeout):
        captured.update(url=url, data=data, headers=headers)
        return FakeResp({"access_token": "gho_new", "token_type": "bearer", "expires_in": 3600})

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = fake_post
        token_data = await gi.exchange_code_for_token("code-123", "state-abc")
    assert token_data.access_token == "gho_new"
    assert captured["data"]["code"] == "code-123"
    assert captured["data"]["client_id"] == "cid"


# ---------------------------------------------------------------------------
# get_user_info
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_user_info(monkeypatch):
    gi = GitHubIntegration(encryption_key=Fernet.generate_key())

    class FakeResp:
        def __init__(self, data, status=200):
            self._data = data
            self.status_code = status
        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception("bad")
        def json(self):
            return self._data
        @property
        def is_success(self):
            return 200 <= self.status_code < 300

    calls = []
    async def fake_get(url, *, headers, timeout):
        calls.append(url)
        if url.endswith("/user"):
            return FakeResp({"login": "alice", "id": 7, "name": "Alice"})
        if url.endswith("/user/emails"):
            return FakeResp([{"primary": True, "verified": True, "email": "alice@x.dev"}])
        return FakeResp({})

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = fake_get
        info = await gi.get_user_info("gho_secret")
    assert info.login == "alice"
    assert info.id == 7
    assert info.email == "alice@x.dev"
    assert info.name == "Alice"


# ---------------------------------------------------------------------------
# ensure_repository
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_repository_creates_when_missing(monkeypatch):
    gi = GitHubIntegration(encryption_key=Fernet.generate_key())
    gi.store_token("user-1", GitHubTokenData(access_token="tok"))

    seq = []
    class FakeResp:
        def __init__(self, data, status=200):
            self._data = data
            self.status_code = status
        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception("bad")
        def json(self):
            return self._data

    async def fake_get(url, headers, timeout):
        seq.append(("GET", url))
        if "/repos/alice/repo" in url:
            return FakeResp({}, status=404)
        if "/user/repos" in url:
            return FakeResp({}, status=201)
        return FakeResp({"default_branch": "main"})

    async def fake_post(url, headers, json, timeout):
        seq.append(("POST", url, json))
        return FakeResp({}, status=201)

    with patch("httpx.AsyncClient") as mock_client:
        client = mock_client.return_value.__aenter__.return_value
        client.get = fake_get
        client.post = fake_post
        await gi.ensure_repository("user-1", "alice", "repo", private=False)
    assert any("user/repos" in u for entry in seq if entry[0] == "POST" for u in [entry[1]])


# ---------------------------------------------------------------------------
# export_checkpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_checkpoint_pushes_files_and_plan(monkeypatch):
    gi = GitHubIntegration(encryption_key=Fernet.generate_key())
    gi.store_token("user-1", GitHubTokenData(access_token="tok"))

    calls = []
    class FakeResp:
        def __init__(self, data, status=200):
            self._data = data
            self.status_code = status
        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception("bad")
        def json(self):
            return self._data

    async def fake_get(url, *, headers, timeout):
        calls.append(("GET", url))
        if "/git/ref/heads/main" in url:
            return FakeResp({"object": {"sha": "base-sha"}}, status=200)
        if "/git/commits/base-sha" in url:
            return FakeResp({"tree": {"sha": "base-tree"}})
        if "/repos/alice/repo" in url and "/git" not in url:
            return FakeResp({"default_branch": "main"})
        return FakeResp({}, status=404)

    async def fake_post(url, *, headers, json, timeout):
        calls.append(("POST", url, json.get("message")))
        if "/git/blobs" in url:
            return FakeResp({"sha": "blob-sha"})
        if "/git/trees" in url:
            return FakeResp({"sha": "tree-sha"})
        if "/git/commits" in url:
            return FakeResp({"sha": "commit-sha"})
        return FakeResp({}, status=201)

    async def fake_patch(url, *, headers, json, timeout):
        calls.append(("PATCH", url))
        return FakeResp({}, status=200)

    with patch("httpx.AsyncClient") as mock_client:
        client = mock_client.return_value.__aenter__.return_value
        client.get = fake_get
        client.post = fake_post
        client.patch = fake_patch
        result = await gi.export_checkpoint(
            "user-1",
            {"files": {"src/App.tsx": "export const A = 1"}, "plan": [{"id": "t1", "title": "Build"}]},
            "alice", "repo",
        )
    assert isinstance(result, GitHubExportResult)
    assert result.commit_sha == "commit-sha"
    assert result.files_pushed == 2  # src/App.tsx + PLAN.json
    assert "github.com/alice/repo/commit/commit-sha" in result.html_url
    # both files were turned into blobs
    blob_posts = [c for c in calls if c[0] == "POST" and "/git/blobs" in c[1]]
    assert len(blob_posts) == 2


# ---------------------------------------------------------------------------
# Global instance
# ---------------------------------------------------------------------------

def test_get_github_integration_singleton(monkeypatch):
    import mux.integrations.github as gh
    monkeypatch.setattr(gh, "_github_integration", None)
    a = get_github_integration()
    b = get_github_integration()
    assert a is b
