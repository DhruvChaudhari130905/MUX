"""End-to-end tests for the HTTP + WebSocket API (regression tests for the review findings)."""

import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
import jwt

import mux.rooms.registry as room_registry
from mux.config import settings
from mux.events.models import UserMessageSentEvent
from mux.main import create_app

SECRET = "test-secret-that-is-at-least-32-bytes"


def token(sub: str, aud: str | None = "authenticated") -> str:
    """A Supabase-shaped access token (Supabase sets aud='authenticated')."""
    payload = {"sub": sub, "role": "authenticated", "exp": int(time.time()) + 3600}
    if aud:
        payload["aud"] = aud
    return jwt.encode(payload, SECRET, algorithm="HS256")


def auth(sub: str) -> dict:
    return {"Authorization": f"Bearer {token(sub)}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # FileStore and sqlite live under cwd
    monkeypatch.setattr(settings, "supabase_jwt_secret", SECRET)
    monkeypatch.setattr(settings, "supabase_jwt_audience", "")
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{tmp_path}/test.db")
    # Keep a developer's real .env out of these tests: no live agents, no GitHub OAuth app
    monkeypatch.setattr(settings, "token_factory_api_key", "")
    monkeypatch.setattr(settings, "github_client_id", "")
    monkeypatch.setattr(settings, "github_client_secret", "")
    monkeypatch.setattr("mux.integrations.github._github_integration", None)
    with TestClient(create_app()) as c:
        yield c


def create_room(c, owner="alice", **body) -> str:
    r = c.post("/rooms", json={"description": "demo", **body}, headers=auth(owner))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def registry_call(c, fn, *args):
    return c.portal.call(fn, *args)


# --- startup / auth -------------------------------------------------------

def test_app_starts(client):
    assert client.get("/health").json() == {"status": "healthy"}


def test_supabase_token_with_aud_is_accepted(client):
    create_room(client)  # token carries aud="authenticated"


def test_configured_audience_is_enforced(client, monkeypatch):
    monkeypatch.setattr(settings, "supabase_jwt_audience", "authenticated")
    create_room(client)
    bad = {"Authorization": f"Bearer {token('alice', aud='other')}"}
    assert client.post("/rooms", json={"name": "x"}, headers=bad).status_code == 401


def test_messages_carry_the_senders_display_name(client):
    rid = create_room(client)
    named = jwt.encode({"sub": "alice", "aud": "authenticated", "exp": int(time.time()) + 3600, "email": "alice@x.dev",
                        "user_metadata": {"full_name": "Alice Smith"}}, SECRET, algorithm="HS256")
    r = client.post(f"/rooms/{rid}/messages", json={"text": "hi"}, headers={"Authorization": f"Bearer {named}"})
    assert r.status_code == 200, r.text
    with client.websocket_connect(f"/rooms/{rid}/ws?since=0") as ws:
        ws.send_json({"type": "auth", "payload": {"token": named}})
        dump = ws.receive_json()
    posted = next(e for e in dump if e["type"] == "message.posted")
    assert posted["payload"]["user"]["name"] == "Alice Smith"


def test_display_name_falls_back_to_email():
    from mux.auth.supabase import display_name
    assert display_name({"email": "bob@x.dev", "user_metadata": {}}) == "bob"
    assert display_name({"user_metadata": {"name": "  Bob B "}}) == "Bob B"
    assert display_name({}) is None


def test_asymmetric_supabase_token_is_verified_against_jwks(client, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from jwt.algorithms import ECAlgorithm

    import mux.auth.supabase as supa

    def es256_key():
        private = ec.generate_private_key(ec.SECP256R1())
        pem = private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        return pem, ECAlgorithm.to_jwk(private.public_key(), as_dict=True)

    signing_pem, public = es256_key()
    other_pem, _ = es256_key()
    public.update(kid="k1", alg="ES256")
    monkeypatch.setattr(settings, "supabase_url", "https://example.supabase.co")
    monkeypatch.setattr(supa, "_jwks_cache", {"url": None, "fetched_at": 0.0, "keys": []})
    monkeypatch.setattr(supa, "_fetch_jwks", lambda url: [public])

    def es_token(pem, sub="alice"):
        claims = {"sub": sub, "aud": "authenticated", "exp": int(time.time()) + 3600}
        return jwt.encode(claims, pem.decode(), algorithm="ES256", headers={"kid": "k1"})

    good = {"Authorization": f"Bearer {es_token(signing_pem)}"}
    assert client.post("/rooms", json={"description": "x"}, headers=good).status_code == 201
    forged = {"Authorization": f"Bearer {es_token(other_pem)}"}
    assert client.post("/rooms", json={"description": "x"}, headers=forged).status_code == 401


# --- rooms / permissions --------------------------------------------------

def test_room_status_and_list(client):
    rid = create_room(client)
    r = client.get(f"/rooms/{rid}", headers=auth("alice"))
    assert r.status_code == 200, r.text
    room = r.json()
    assert room["title"] == "demo" and room["owner_id"] == "alice" and room["link_access"] == "restricted"
    assert room["members"][0]["permission"] == "owner"
    assert [x["id"] for x in client.get("/rooms", headers=auth("alice")).json()] == [rid]
    assert client.get("/rooms", headers=auth("mallory")).json() == []
    assert client.get(f"/rooms/{rid}/status", headers=auth("alice")).json()["name"] == "demo"


def test_unknown_room_is_404_not_claimed(client):
    r = client.post("/api/files/room_unclaimed/files", json={"path": "a.txt", "content": "hi"}, headers=auth("mallory"))
    assert r.status_code == 404
    assert client.get("/rooms/room_unclaimed", headers=auth("mallory")).status_code == 404


def test_private_room_requires_membership(client):
    rid = create_room(client)
    assert client.post(f"/rooms/{rid}/join", json={}, headers=auth("mallory")).status_code == 403
    assert client.post(f"/api/commands/{rid}/steer", json={"instructions": "x"}, headers=auth("mallory")).status_code == 403

    r = client.post(f"/rooms/{rid}/members", json={"user_id": "bob", "role": "editor"}, headers=auth("alice"))
    assert r.status_code == 200, r.text
    r = client.post(f"/rooms/{rid}/join", json={"user_name": "Bob"}, headers=auth("bob"))
    assert r.status_code == 200, r.text
    assert client.post(f"/api/commands/{rid}/steer", json={"instructions": "x"}, headers=auth("bob")).status_code == 200
    # Leaving does not revoke membership
    client.post(f"/rooms/{rid}/leave", headers=auth("bob"))
    assert client.post(f"/api/commands/{rid}/steer", json={"instructions": "y"}, headers=auth("bob")).status_code == 200
    # Only the owner can add members
    assert client.post(f"/rooms/{rid}/members", json={"user_id": "eve"}, headers=auth("bob")).status_code == 403


def test_public_room_join_grants_editor(client):
    rid = create_room(client)
    assert client.patch(f"/rooms/{rid}/sharing", json={"link_access": "anyone"}, headers=auth("alice")).status_code == 200
    assert client.get(f"/rooms/{rid}", headers=auth("carol")).status_code == 200  # viewer
    assert client.post(f"/api/commands/{rid}/steer", json={"instructions": "x"}, headers=auth("carol")).status_code == 403
    assert client.post(f"/rooms/{rid}/join", json={}, headers=auth("carol")).status_code == 200
    assert client.post(f"/api/commands/{rid}/steer", json={"instructions": "x"}, headers=auth("carol")).status_code == 200


def test_closed_room_cannot_be_reclaimed(client):
    rid = create_room(client)
    assert client.post(f"/rooms/{rid}/close", headers=auth("alice")).status_code == 200
    assert client.get(f"/rooms/{rid}", headers=auth("mallory")).status_code == 404
    assert client.get(f"/rooms/{rid}", headers=auth("alice")).status_code == 404


def test_invalid_plan_edit_is_400_and_atomic(client):
    rid = create_room(client, initial_plan=[{"id": "p1", "title": "T", "status": "draft"}])
    edits = [{"type": "update", "item_id": "p1", "changes": {"status": "doing"}},
             {"type": "update", "item_id": "nope", "changes": {"status": "done"}}]
    r = client.post(f"/api/commands/{rid}/edit-plan", json={"edits": edits}, headers=auth("alice"))
    assert r.status_code == 400
    room = registry_call(client, room_registry.get_registry().get_room, rid)
    assert registry_call(client, room.get_plan)[0]["status"] == "draft"


def test_raise_budget_without_increase_is_400(client):
    rid = create_room(client)
    r = client.post(f"/api/commands/{rid}/raise-budget", json={"token_cap": 1}, headers=auth("alice"))
    assert r.status_code == 400


# --- files ----------------------------------------------------------------

def test_files_are_isolated_per_room(client):
    a = create_room(client, owner="alice")
    b = create_room(client, owner="bob")
    client.post(f"/api/files/{a}/files", json={"path": "src/app.py", "content": "alice"}, headers=auth("alice"))
    client.post(f"/api/files/{b}/files", json={"path": "src/app.py", "content": "bob"}, headers=auth("bob"))
    assert client.get(f"/api/files/{a}/files/src/app.py", headers=auth("alice")).json()["content"] == "alice"


def test_file_metadata_lock_status_and_duplicates(client):
    rid = create_room(client)
    r = client.post(f"/api/files/{rid}/files", json={"path": "/app.py", "content": "x"}, headers=auth("alice"))
    assert r.json()["path"] == "app.py"
    assert client.post(f"/api/files/{rid}/files", json={"path": "app.py", "content": "y"}, headers=auth("alice")).status_code == 409

    listing = client.get(f"/api/files/{rid}/files", headers=auth("alice")).json()["files"]
    assert listing[0]["file_type"] == "text/x-python"

    client.post(f"/api/files/{rid}/files/app.py/lock", headers=auth("alice"))
    r = client.get(f"/api/files/{rid}/files/app.py/lock", headers=auth("alice"))
    assert r.status_code == 200, r.text
    assert r.json()["locked_by"] == "alice"


def test_deleted_file_is_not_readable(client):
    rid = create_room(client)
    client.post(f"/api/files/{rid}/files", json={"path": "a.txt", "content": "x"}, headers=auth("alice"))
    assert client.delete(f"/api/files/{rid}/files/a.txt", headers=auth("alice")).status_code == 200
    assert client.get(f"/api/files/{rid}/files/a.txt", headers=auth("alice")).status_code == 404


# --- websocket ------------------------------------------------------------

def read_until(ws, predicate, max_rounds=20):
    """Read messages until one matches.

    Each round sends a ping; reaching its pong without a match gives the server's
    broadcast tasks another chance to run. After max_rounds the message is treated
    as never sent (fails fast instead of blocking forever on receive)."""
    seen = []
    for _ in range(max_rounds):
        ws.send_text(json.dumps({"type": "ping"}))
        while True:
            msg = json.loads(ws.receive_text())
            seen.append(msg)
            if predicate(msg):
                return msg, seen
            if msg["type"] == "pong":
                break
    raise AssertionError(f"not found in {[m['type'] for m in seen]}")


def connect_ws(client, rid, user="alice", since=0):
    """Open the room socket and return it with the initial dump (a JSON array of envelopes)."""
    ws = client.websocket_connect(f"/rooms/{rid}/ws?since={since}&token={token(user)}").__enter__()
    dump = json.loads(ws.receive_text())
    assert isinstance(dump, list)
    return ws, dump


def test_websocket_streams_catalog_envelopes(client):
    rid = create_room(client)
    ws, dump = connect_ws(client, rid)
    try:
        assert [e["type"] for e in dump] == ["room.created"]
        assert set(dump[0]) == {"seq", "room_id", "type", "actor", "actor_id", "ts", "payload"}

        join, _ = read_until(ws, lambda m: m["type"] == "presence.join")
        assert join["payload"]["user_id"] == "alice"

        client.post(f"/rooms/{rid}/messages", json={"text": "hello", "to": "team"}, headers=auth("alice"))
        msg, _ = read_until(ws, lambda m: m["type"] == "message.posted")
        assert msg["payload"]["text"] == "hello" and msg["payload"]["to"] == "team"
        assert msg["seq"] > join["seq"]

        ws.send_text(json.dumps({"type": "presence.tab", "payload": {"tab": "nope"}}))
        err, _ = read_until(ws, lambda m: m["type"] == "error")
        assert "Invalid tab" in err["message"]

        ws.send_text(json.dumps({"type": "presence.tab", "payload": {"tab": "code"}}))
        tab, _ = read_until(ws, lambda m: m["type"] == "presence.tab")
        assert tab["payload"] == {"user_id": "alice", "tab": "code"}
        assert tab["seq"] == msg["seq"]  # ephemeral: keeps the client's resume point

        ws.send_text(json.dumps({"type": "presence.typing", "payload": {"typing": True}}))
        typing, _ = read_until(ws, lambda m: m["type"] == "presence.typing")
        assert typing["payload"] == {"user_id": "alice", "typing": True}
    finally:
        ws.__exit__(None, None, None)


def test_websocket_first_message_auth_and_resume(client):
    rid = create_room(client)
    client.post(f"/rooms/{rid}/messages", json={"text": "one"}, headers=auth("alice"))
    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_text(json.dumps({"type": "auth", "payload": {"token": token("alice")}}))
        dump = json.loads(ws.receive_text())
    assert [e["type"] for e in dump] == ["room.created", "message.posted"]
    seqs = [e["seq"] for e in dump]
    assert seqs == sorted(set(seqs))

    # Resuming from the last seq replays only what came after it, with no duplicates
    client.post(f"/rooms/{rid}/messages", json={"text": "two"}, headers=auth("alice"))
    ws, dump = connect_ws(client, rid, since=dump[-1]["seq"])
    try:
        texts = [e["payload"]["text"] for e in dump if e["type"] == "message.posted"]
        assert texts == ["two"]
        _, seen = read_until(ws, lambda m: m["type"] == "presence.join")
        stored = [m["seq"] for m in seen if not m["type"].startswith("presence.") and m["type"] not in ("pong", "error")]
        assert all(s > dump[-1]["seq"] for s in stored)
    finally:
        ws.__exit__(None, None, None)


def test_websocket_rejects_non_members_and_missing_tokens(client):
    from starlette.websockets import WebSocketDisconnect
    rid = create_room(client)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/rooms/{rid}/ws?token={token('mallory')}") as ws:
            ws.receive_text()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
            ws.send_text(json.dumps({"type": "auth", "payload": {"token": "garbage"}}))
            ws.receive_text()


# --- the web app's REST contract ------------------------------------------

def test_file_saves_are_version_checked(client):
    rid = create_room(client)
    r = client.put(f"/rooms/{rid}/files", json={"path": "a.py", "content": "v1", "base_version": 0}, headers=auth("alice"))
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] is True and r.json()["version"] == 1
    r = client.put(f"/rooms/{rid}/files", json={"path": "a.py", "content": "v2", "base_version": 1}, headers=auth("alice"))
    assert r.json()["version"] == 2
    # An edit based on a stale version is refused instead of silently overwriting
    stale = client.put(f"/rooms/{rid}/files", json={"path": "a.py", "content": "v2b", "base_version": 1}, headers=auth("alice"))
    assert stale.status_code == 409
    assert client.get(f"/api/files/{rid}/files/a.py", headers=auth("alice")).json()["content"] == "v2"

    # Someone else's lock blocks a save
    client.post(f"/rooms/{rid}/members", json={"user_id": "bob"}, headers=auth("alice"))
    assert client.post(f"/rooms/{rid}/files/lock", json={"path": "a.py"}, headers=auth("bob")).status_code == 200
    assert client.post(f"/rooms/{rid}/files/lock", json={"path": "a.py"}, headers=auth("alice")).status_code == 409
    blocked = client.put(f"/rooms/{rid}/files", json={"path": "a.py", "content": "x", "base_version": 2}, headers=auth("alice"))
    assert blocked.status_code == 409
    assert client.post(f"/rooms/{rid}/files/unlock", json={"path": "a.py"}, headers=auth("bob")).status_code == 200

    assert client.delete(f"/rooms/{rid}/files", params={"path": "a.py"}, headers=auth("alice")).status_code == 200
    assert client.delete(f"/rooms/{rid}/files", params={"path": "a.py"}, headers=auth("alice")).status_code == 404


def test_plan_conflict_question_session_commands(client):
    rid = create_room(client, domain_role="design")
    assert client.get(f"/rooms/{rid}", headers=auth("alice")).json()["members"][0]["domain_role"] == "design"

    items = [{"id": "t1", "title": "Pricing page", "status": "draft"}, {"id": "t2", "title": "Auth", "status": "draft"}]
    r = client.patch(f"/rooms/{rid}/plan", json={"items": items}, headers=auth("alice"))
    assert r.status_code == 200 and r.json()["accepted"] is True
    # Regression: removing a task from the plan UI must not survive on the server
    kept = [{"id": "t1", "title": "Pricing page", "status": "draft"}]
    r = client.patch(f"/rooms/{rid}/plan", json={"items": kept}, headers=auth("alice"))
    assert r.status_code == 200
    room = registry_call(client, room_registry.get_registry().get_room, rid)
    plan = registry_call(client, room.get_plan)
    assert [i["id"] for i in plan] == ["t1"], plan
    assert client.post(f"/rooms/{rid}/plan/approve", headers=auth("alice")).status_code == 200
    room = registry_call(client, room_registry.get_registry().get_room, rid)
    assert {i["status"] for i in registry_call(client, room.get_plan)} == {"todo"}

    assert client.post(f"/rooms/{rid}/conflicts/c1/vote", json={"option": "SQLite"}, headers=auth("alice")).status_code == 200
    assert client.post(f"/rooms/{rid}/conflicts/c1/override", json={"option": "SQLite"}, headers=auth("alice")).status_code == 200
    assert client.post(f"/rooms/{rid}/questions/q1/answer", json={"answer": "Yes"}, headers=auth("alice")).status_code == 200
    assert client.post(f"/rooms/{rid}/end-session", headers=auth("alice")).status_code == 200

    b = client.patch(f"/rooms/{rid}/budget", json={"tokens_cap": 5_000_000}, headers=auth("alice")).json()
    assert b["tokens_cap"] == 5_000_000 and set(b) == {"tokens_used", "runs_used", "tokens_cap", "runs_cap"}

    ws, dump = connect_ws(client, rid)
    ws.__exit__(None, None, None)
    types = [e["type"] for e in dump]
    for expected in ("plan.edited", "plan.approved", "conflict.vote", "conflict.closed", "question.answered"):
        assert expected in types, types
    vote = next(e for e in dump if e["type"] == "conflict.vote")["payload"]
    assert vote == {"conflict_id": "c1", "user_id": "alice", "option": "SQLite", "weight": 1}


def test_export_needs_github(client):
    rid = create_room(client)
    r = client.post(f"/rooms/{rid}/export", json={"repo_name": "demo", "private": True}, headers=auth("alice"))
    assert r.status_code == 400 and "Connect GitHub" in r.json()["detail"]
    assert client.get("/github/connect", headers=auth("alice")).status_code == 503  # OAuth app not configured


# --- event log, rewind, rehydration ---------------------------------------

def test_rewind_restores_files_and_keeps_sequences_unique(client):
    rid = create_room(client)
    client.post(f"/api/files/{rid}/files", json={"path": "a.py", "content": "v1"}, headers=auth("alice"))
    cp = client.post(f"/api/export/{rid}/checkpoints", json={"description": "cp"}, headers=auth("alice")).json()["checkpoint_id"]
    client.put(f"/api/files/{rid}/files/a.py", json={"path": "a.py", "content": "v2"}, headers=auth("alice"))
    client.post(f"/api/files/{rid}/files", json={"path": "b.py", "content": "new"}, headers=auth("alice"))
    future = client.post(f"/api/export/{rid}/checkpoints", json={"description": "future"}, headers=auth("alice")).json()["checkpoint_id"]

    r = client.post(f"/rooms/{rid}/rewind", json={"checkpoint_id": cp}, headers=auth("alice"))
    assert r.status_code == 200, r.text
    present = r.json()["present_checkpoint_id"]
    assert client.get(f"/api/files/{rid}/files/a.py", headers=auth("alice")).json()["content"] == "v1"
    assert client.get(f"/api/files/{rid}/files/b.py", headers=auth("alice")).status_code == 404

    # Both the old future and the saved present remain available; new work branches from cp.
    saved = client.get(f"/api/export/{rid}/checkpoints", headers=auth("alice")).json()["checkpoints"]
    assert {item["checkpoint_id"] for item in saved} >= {cp, future, present}
    client.post(f"/api/files/{rid}/files", json={"path": "c.py", "content": "new branch"}, headers=auth("alice"))
    branch = client.post(f"/api/export/{rid}/checkpoints", json={"description": "new branch"}, headers=auth("alice")).json()["checkpoint_id"]
    all_checkpoints = client.get(f"/api/export/{rid}/checkpoints", headers=auth("alice")).json()["checkpoints"]
    assert client.get(f"/api/export/{rid}/checkpoints/{branch}", headers=auth("alice")).json()["parent_id"] == cp

    restored = client.post(f"/rooms/{rid}/restore-checkpoint", json={"checkpoint_id": present}, headers=auth("alice"))
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/files/{rid}/files/a.py", headers=auth("alice")).json()["content"] == "v2"
    assert client.get(f"/api/files/{rid}/files/b.py", headers=auth("alice")).json()["content"] == "new"
    assert client.get(f"/api/files/{rid}/files/c.py", headers=auth("alice")).status_code == 404

    room = registry_call(client, room_registry.get_registry().get_room, rid)
    seqs = [e.sequence for e in room.event_log._events]
    assert seqs == list(range(1, len(seqs) + 1))
    assert len(all_checkpoints) == 4
    rewind_event = next(e for e in room.event_log._events if e.type.value == "command_rewind")
    restore_event = next(e for e in room.event_log._events if e.type.value == "checkpoint_restored")
    assert rewind_event.target_checkpoint_id == cp and rewind_event.return_checkpoint_id == present
    assert set(rewind_event.files) == {"a.py"} and rewind_event.plan == []
    assert restore_event.checkpoint_id == present and set(restore_event.files) == {"a.py", "b.py"}

    # Replaying the event log after a restart restores the saved present and every branch.
    registry = room_registry.get_registry()
    registry_call(client, registry.stop_room, rid)
    rehydrated = registry_call(client, registry.get_room_or_rehydrate, rid)
    assert rehydrated is not None
    assert registry_call(client, rehydrated.get_file, "a.py") == "v2"
    assert registry_call(client, rehydrated.get_file, "b.py") == "new"
    assert rehydrated.manifest.active_checkpoint_id == present
    assert len(rehydrated.manifest.list_checkpoints()) == 4


def test_rehydration_restores_state(client):
    rid = create_room(client, initial_plan=[{"id": "p1", "title": "T", "status": "draft"}])
    client.post(f"/rooms/{rid}/members", json={"user_id": "bob"}, headers=auth("alice"))
    client.post(f"/api/files/{rid}/files", json={"path": "f.py", "content": "x=1"}, headers=auth("alice"))
    client.post(f"/api/export/{rid}/checkpoints", json={}, headers=auth("alice"))
    # PATCH /plan sends the whole plan (architecture.md, Q45)
    client.patch(f"/rooms/{rid}/plan", json={"items": [{"id": "p1", "title": "T", "status": "draft"},
                                                       {"id": "p2", "title": "U", "status": "doing"}]}, headers=auth("alice"))
    client.post(f"/api/commands/{rid}/approve-plan", json={"plan_item_ids": ["p1"]}, headers=auth("alice"))

    reg = room_registry.get_registry()
    registry_call(client, reg.stop_room, rid)

    # Next request rehydrates from the log; ownership and membership come from events
    r = client.get(f"/rooms/{rid}", headers=auth("bob"))
    assert r.status_code == 200, r.text
    assert r.json()["owner_id"] == "alice"
    room = registry_call(client, reg.get_room, rid)
    plan = {i["id"]: i["status"] for i in registry_call(client, room.get_plan)}
    # Whole-plan updates cannot start work: newly added tasks are normalized to drafts until the owner approves.
    assert plan == {"p1": "todo", "p2": "draft"}
    assert client.get(f"/api/files/{rid}/files/f.py", headers=auth("bob")).json()["content"] == "x=1"
    assert len(room.manifest.list_checkpoints()) == 1


def test_saving_a_folder_path_as_a_file_is_a_409(client):
    rid = create_room(client)
    h = auth("alice")
    assert client.put(f"/rooms/{rid}/files", json={"path": "src/App.tsx", "content": "x", "base_version": 0}, headers=h).status_code == 200
    r = client.put(f"/rooms/{rid}/files", json={"path": "src", "content": "", "base_version": 0}, headers=h)
    assert r.status_code == 409 and "folder" in r.json()["detail"]
    r = client.put(f"/rooms/{rid}/files", json={"path": "src/App.tsx/x", "content": "", "base_version": 0}, headers=h)
    assert r.status_code == 409 and "is a file" in r.json()["detail"]


def test_rooms_survive_a_server_restart(client):
    """Events are kept on disk: after a restart the room is listed again and rebuilt with its state."""
    import mux.main as main_module

    rid = create_room(client, initial_plan=[{"id": "p1", "title": "Home page", "status": "draft"}])
    client.post(f"/rooms/{rid}/messages", json={"text": "make it blue"}, headers=auth("alice"))
    client.put(f"/rooms/{rid}/files", json={"path": "index.html", "content": "<h1>hi</h1>", "base_version": 0}, headers=auth("alice"))
    client.post(f"/rooms/{rid}/plan/approve", headers=auth("alice"))

    # A restart: no running actors and no cached logs, only what's on disk
    registry_call(client, room_registry.get_registry().stop_room, rid)
    main_module._room_event_logs.clear()

    assert [r["id"] for r in client.get("/rooms", headers=auth("alice")).json()] == [rid]
    assert client.get("/rooms", headers=auth("mallory")).json() == []
    room = registry_call(client, room_registry.get_registry().get_room, rid)
    assert [(p["title"], p["status"]) for p in registry_call(client, room.get_plan)] == [("Home page", "todo")]
    assert client.get(f"/api/files/{rid}/files/index.html", headers=auth("alice")).json()["content"] == "<h1>hi</h1>"
    with client.websocket_connect(f"/rooms/{rid}/ws?since=0") as ws:
        ws.send_json({"type": "auth", "payload": {"token": token("alice")}})
        dump = ws.receive_json()
    assert any(e["type"] == "message.posted" and e["payload"]["text"] == "make it blue" for e in dump)


def test_rehydration_restores_budget(client):
    rid = create_room(client)
    reg = room_registry.get_registry()
    room = registry_call(client, reg.get_room, rid)
    registry_call(client, room.record_tokens, 2_000_000, "alice")
    assert client.get(f"/api/commands/{rid}/budget", headers=auth("alice")).json()["paused"] is True
    client.post(f"/api/commands/{rid}/raise-budget", json={"token_cap": 3_000_000}, headers=auth("alice"))

    registry_call(client, reg.stop_room, rid)
    status = client.get(f"/api/commands/{rid}/budget", headers=auth("alice")).json()
    assert status["paused"] is False
    assert status["tokens_used"] == 2_000_000


# --- GitHub ---------------------------------------------------------------

def test_github_callback_rejects_forged_state(client):
    import base64
    forged = base64.urlsafe_b64encode(json.dumps({"user_id": "victim"}).encode()).decode()
    r = client.get("/api/export/github/callback", params={"code": "c", "state": forged}, follow_redirects=False)
    assert r.status_code == 307 and "github=error" in r.headers["location"]


def test_connect_github_then_export_creates_the_repo(client, monkeypatch):
    """The callback records the GitHub login, and export creates a missing repo before pushing."""
    from mux.integrations.github import get_github_integration

    monkeypatch.setattr(settings, "web_app_url", "http://app.test")
    gh = get_github_integration()
    gh.config.client_id, gh.config.client_secret = "id", "secret"
    calls = []

    def handler(req):
        calls.append((req.method, req.url.path))
        path = req.url.path
        if path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "tok", "scope": "repo"})
        if path == "/user":
            return httpx.Response(200, json={"login": "alice-gh", "id": 7, "email": "a@x.dev"})
        if path == "/repos/alice-gh/shop" and req.method == "GET":
            return httpx.Response(404 if ("POST", "/user/repos") not in calls else 200, json={"default_branch": "main"})
        if path == "/user/repos":
            assert json.loads(req.content)["private"] is False
            return httpx.Response(201, json={})
        if path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base"}})
        if path.endswith("/git/commits/base"):
            return httpx.Response(200, json={"tree": {"sha": "tree0"}})
        if path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "blob"})
        if path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "tree1"})
        if path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "c1"})
        if path.endswith("/git/refs/heads/main"):
            return httpx.Response(200, json={})
        return httpx.Response(500, json={"unexpected": path})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))

    rid = create_room(client)
    client.put(f"/rooms/{rid}/files", json={"path": "index.html", "content": "<h1>hi</h1>", "base_version": 0}, headers=auth("alice"))
    _, state = gh.get_authorization_url("alice")
    r = client.get("/api/export/github/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert r.headers["location"] == "http://app.test/profile?github=connected&username=alice-gh"

    r = client.post(f"/rooms/{rid}/export", json={"repo_name": "shop", "private": False}, headers=auth("alice"))
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "https://github.com/alice-gh/shop/commit/c1"
    assert ("POST", "/user/repos") in calls


def test_github_connect_returns_to_the_page_that_started_it(client, monkeypatch):
    from urllib.parse import parse_qs, urlparse

    from mux.integrations.github import get_github_integration

    monkeypatch.setattr(settings, "web_app_url", "http://app.test")
    gh = get_github_integration()
    gh.config.client_id = "id"

    def state_for(next_path):
        r = client.get("/github/connect", params={"next": next_path}, headers=auth("alice"))
        return parse_qs(urlparse(r.json()["url"]).query)["state"][0]

    # A bad state still goes back to where the user came from, with the error
    state = state_for("/room/r1?export=1")
    monkeypatch.setattr(gh, "exchange_code_for_token", lambda code, state: (_ for _ in ()).throw(ValueError("bad code")))
    r = client.get("/api/export/github/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert r.headers["location"].startswith("http://app.test/room/r1?export=1&github=error")

    # Paths that would leave the app fall back to the profile page
    for unsafe in ("https://evil.test/x", "//evil.test/x", "/\\evil.test"):
        state = state_for(unsafe)
        r = client.get("/api/export/github/callback", params={"code": "c", "state": state}, follow_redirects=False)
        assert r.headers["location"].startswith("http://app.test/profile?github=error"), unsafe


def test_github_state_is_single_use():
    from mux.integrations.github import GitHubIntegration
    gh = GitHubIntegration()
    gh.config.client_id = "id"
    _, state = gh.get_authorization_url("alice")
    assert gh.consume_state(state) == "alice"
    assert gh.consume_state(state) is None


@pytest.mark.asyncio
async def test_github_user_info_with_private_email(monkeypatch):
    from mux.integrations.github import GitHubIntegration

    def handler(req):
        if req.url.path == "/user":
            return httpx.Response(200, json={"login": "a", "id": 1, "email": None})
        return httpx.Response(200, json=[{"email": "a@x.dev", "primary": True, "verified": True}])

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    info = await GitHubIntegration().get_user_info("t")
    assert info.email == "a@x.dev"


# --- units ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_emit_event_sends_envelope_once_per_connection(monkeypatch):
    from mux.api import ws as ws_module
    from mux.events.bus import EventBus

    class FakeSocket:
        def __init__(self):
            self.sent = []

        async def send_text(self, data: str) -> None:
            self.sent.append(json.loads(data))

    bus = EventBus()
    monkeypatch.setattr(ws_module, "event_bus", bus)
    fresh, caught_up = FakeSocket(), FakeSocket()
    bus.connect("r", fresh, last_seq=0)
    bus.connect("r", caught_up, last_seq=1)  # already had seq 1 in its initial dump

    event = UserMessageSentEvent(room_id="r", sequence=1, message_id="m1", content="hi", user_id="u")
    await ws_module.emit_event("r", event)
    await ws_module.emit_event("r", event)  # a late duplicate broadcast is dropped
    assert [m["type"] for m in fresh.sent] == ["message.posted"]
    assert fresh.sent[0]["payload"]["text"] == "hi" and fresh.sent[0]["seq"] == 1
    assert caught_up.sent == []


def test_every_mapped_event_has_a_catalog_type():
    """Every envelope type the server can send is one the web app knows (web/src/types/index.ts)."""
    import inspect
    import re
    from pathlib import Path
    import mux.events.models as models
    from mux.events.wire import to_envelope

    ts_types = Path(__file__).resolve().parents[2] / "web" / "src" / "types" / "index.ts"
    known = set(re.findall(r"\| '([a-z_.]+)'", ts_types.read_text()))
    wire_src = (Path(models.__file__).parent / "wire.py").read_text()
    sent = set(re.findall(r'return "([a-z_.]+)", ', wire_src))
    assert sent and sent <= known, sent - known
    assert inspect.isfunction(to_envelope)


def test_only_the_owner_can_delete_a_room_and_it_leaves_the_list(client):
    rid = create_room(client)
    assert client.post(f"/rooms/{rid}/close", headers=auth("bob")).status_code in (403, 404)
    assert rid in [r["id"] for r in client.get("/rooms", headers=auth("alice")).json()]

    r = client.post(f"/rooms/{rid}/close", params={"reason": "deleted_by_owner"}, headers=auth("alice"))
    assert r.status_code == 200 and r.json()["closed"] is True
    assert rid not in [r["id"] for r in client.get("/rooms", headers=auth("alice")).json()]


# --- joining: links, invites, passwords -----------------------------------

def auth_email(sub: str, email: str) -> dict:
    payload = {"sub": sub, "aud": "authenticated", "exp": int(time.time()) + 3600, "email": email}
    return {"Authorization": f"Bearer {jwt.encode(payload, SECRET, algorithm='HS256')}"}


def role_in(c, rid: str, user: str) -> str | None:
    room = c.get(f"/rooms/{rid}", headers=auth("alice")).json()
    return next((m["permission"] for m in room["members"] if m["user_id"] == user), None)


def test_viewer_link_grants_viewer(client):
    rid = create_room(client)
    r = client.patch(f"/rooms/{rid}/sharing", json={"link_access": "anyone", "link_permission": "viewer"}, headers=auth("alice"))
    assert r.json()["link_permission"] == "viewer"
    assert client.post(f"/rooms/{rid}/join", json={}, headers=auth("carol")).status_code == 200
    assert role_in(client, rid, "carol") == "viewer"
    assert client.post(f"/api/commands/{rid}/steer", json={"instructions": "x"}, headers=auth("carol")).status_code == 403


def test_invite_lets_that_email_into_a_private_room(client, monkeypatch):
    sent = []

    async def fake_send(email, room_id, title, inviter):
        sent.append(email)
        from mux.integrations.invite_email import EmailResult
        return EmailResult(True)

    monkeypatch.setattr("mux.api.rooms.send_invite_email", fake_send)
    rid = create_room(client)
    r = client.post(f"/rooms/{rid}/invites", json={"email": " Bob@X.dev ", "role": "viewer"}, headers=auth("alice"))
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "bob@x.dev" and r.json()["email_sent"] is True and r.json()["link"].endswith(f"/room/{rid}")
    assert sent == ["bob@x.dev"]
    assert client.get(f"/rooms/{rid}/invites", headers=auth("alice")).json() == [{"email": "bob@x.dev", "role": "viewer"}]
    # Only the owner sees or makes invites
    assert client.get(f"/rooms/{rid}/invites", headers=auth("bob")).status_code == 403
    # Someone else with a different email is still refused
    assert client.post(f"/rooms/{rid}/join", json={}, headers=auth_email("mallory", "m@x.dev")).status_code == 403
    assert client.post(f"/rooms/{rid}/join", json={}, headers=auth_email("bob", "bob@x.dev")).status_code == 200
    assert role_in(client, rid, "bob") == "viewer"
    # Used up once accepted
    assert client.get(f"/rooms/{rid}/invites", headers=auth("alice")).json() == []


def test_invite_is_kept_when_email_fails(client, monkeypatch):
    monkeypatch.setattr(settings, "supabase_service_role_key", "")
    rid = create_room(client)
    r = client.post(f"/rooms/{rid}/invites", json={"email": "bob@x.dev"}, headers=auth("alice"))
    assert r.status_code == 200
    assert r.json()["email_sent"] is False and "SUPABASE_SERVICE_ROLE_KEY" in r.json()["email_error"]
    assert client.post(f"/rooms/{rid}/join", json={}, headers=auth_email("bob", "bob@x.dev")).status_code == 200
    assert role_in(client, rid, "bob") == "editor"


def test_revoked_invite_no_longer_admits(client, monkeypatch):
    monkeypatch.setattr(settings, "supabase_service_role_key", "")
    rid = create_room(client)
    client.post(f"/rooms/{rid}/invites", json={"email": "bob@x.dev"}, headers=auth("alice"))
    assert client.delete(f"/rooms/{rid}/invites/bob@x.dev", headers=auth("alice")).status_code == 204
    assert client.delete(f"/rooms/{rid}/invites/bob@x.dev", headers=auth("alice")).status_code == 404
    assert client.post(f"/rooms/{rid}/join", json={}, headers=auth_email("bob", "bob@x.dev")).status_code == 403
    assert client.post(f"/rooms/{rid}/invites", json={"email": "not-an-email"}, headers=auth("alice")).status_code == 400


def test_room_password_join(client):
    rid = create_room(client, password="hunter22")
    assert client.get(f"/rooms/{rid}", headers=auth("alice")).json()["has_password"] is True
    assert client.post(f"/rooms/{rid}/join", json={}, headers=auth("dave")).status_code == 403
    assert client.post(f"/rooms/{rid}/join", json={"password": "nope"}, headers=auth("dave")).status_code == 403
    assert client.post(f"/rooms/{rid}/join", json={"password": "hunter22"}, headers=auth("dave")).status_code == 200
    assert role_in(client, rid, "dave") == "editor"

    # Owner changes and then removes it
    assert client.put(f"/rooms/{rid}/password", json={"password": "newpass"}, headers=auth("dave")).status_code == 403
    assert client.put(f"/rooms/{rid}/password", json={"password": "newpass"}, headers=auth("alice")).status_code == 200
    assert client.post(f"/rooms/{rid}/join", json={"password": "hunter22"}, headers=auth("erin")).status_code == 403
    assert client.post(f"/rooms/{rid}/join", json={"password": "newpass"}, headers=auth("erin")).status_code == 200
    r = client.put(f"/rooms/{rid}/password", json={"password": None}, headers=auth("alice"))
    assert r.json()["has_password"] is False
    assert client.post(f"/rooms/{rid}/join", json={"password": "newpass"}, headers=auth("frank")).status_code == 403


def test_password_guessing_is_locked_out(client):
    from mux.rooms.access import password_attempts
    rid = create_room(client, password="hunter22")
    for _ in range(5):
        assert client.post(f"/rooms/{rid}/join", json={"password": "wrong"}, headers=auth("mallory")).status_code == 403
    r = client.post(f"/rooms/{rid}/join", json={"password": "hunter22"}, headers=auth("mallory"))
    assert r.status_code == 429 and "Retry-After" in r.headers
    # Another user isn't affected
    assert client.post(f"/rooms/{rid}/join", json={"password": "hunter22"}, headers=auth("dave")).status_code == 200
    password_attempts.reset(rid, "mallory")


def test_password_hash_and_invites_never_reach_clients(client, monkeypatch):
    monkeypatch.setattr(settings, "supabase_service_role_key", "")
    rid = create_room(client, password="hunter22")
    client.post(f"/rooms/{rid}/invites", json={"email": "secret@x.dev"}, headers=auth("alice"))
    room_text = client.get(f"/rooms/{rid}", headers=auth("alice")).text
    with client.websocket_connect(f"/rooms/{rid}/ws?since=0&token={token('alice')}") as ws:
        dump = ws.receive_text()
    for text in (room_text, dump):
        assert "scrypt" not in text and "secret@x.dev" not in text
    assert '"has_password": true' in dump or '"has_password":true' in dump


def test_old_sharing_events_replay_as_editor_links():
    from mux.events.models import RoomSharingUpdatedEvent
    e = RoomSharingUpdatedEvent.model_validate({"room_id": "r", "sequence": 1, "public": True, "updated_by": "a"})
    assert e.link_permission == "editor"


def test_password_hashes_verify():
    from mux.rooms.access import hash_password, verify_password
    h = hash_password("hunter22")
    assert h != hash_password("hunter22")  # salted
    assert verify_password("hunter22", h) and not verify_password("hunter23", h)
    assert not verify_password("hunter22", "garbage")


def test_rehydration_restores_access_settings(client, monkeypatch):
    monkeypatch.setattr(settings, "supabase_service_role_key", "")
    rid = create_room(client, password="hunter22")
    client.patch(f"/rooms/{rid}/sharing", json={"link_access": "anyone", "link_permission": "viewer"}, headers=auth("alice"))
    client.post(f"/rooms/{rid}/invites", json={"email": "bob@x.dev"}, headers=auth("alice"))
    registry_call(client, room_registry.get_registry().stop_room, rid)

    room = client.get(f"/rooms/{rid}", headers=auth("alice")).json()
    assert room["link_permission"] == "viewer" and room["has_password"] is True
    assert client.get(f"/rooms/{rid}/invites", headers=auth("alice")).json() == [{"email": "bob@x.dev", "role": "editor"}]
    assert client.post(f"/rooms/{rid}/join", json={"password": "hunter22"}, headers=auth("dave")).status_code == 200


def test_room_list_shows_only_your_rooms(client):
    rid = create_room(client)
    client.patch(f"/rooms/{rid}/sharing", json={"link_access": "anyone"}, headers=auth("alice"))
    assert [r["id"] for r in client.get("/rooms", headers=auth("carol")).json()] == []
    client.post(f"/rooms/{rid}/join", json={}, headers=auth("carol"))
    assert [r["id"] for r in client.get("/rooms", headers=auth("carol")).json()] == [rid]


def test_uploading_over_an_existing_file_overwrites_without_a_base_version(client):
    # The web app's upload/import sends no base_version: those files replace what's there
    rid = create_room(client)
    put = lambda body: client.put(f"/rooms/{rid}/files", json={"path": "src/App.jsx", **body}, headers=auth("alice"))
    assert put({"content": "v1", "base_version": 0}).status_code == 200
    assert put({"content": "v2", "base_version": 0}).status_code == 409  # "new file" over an existing one
    r = put({"content": "v2"})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert client.get(f"/api/files/{rid}/files/src/App.jsx", headers=auth("alice")).json()["content"] == "v2"


# --- MCP servers ------------------------------------------------------------

PUBLIC_URL = "https://93.184.216.34/mcp"


@pytest.fixture
def mcp_servers(monkeypatch):
    """In-process MCP servers by name; anything else refuses to connect. One mcp.json server, "github"."""
    from cryptography.fernet import Fernet
    from mcp.server.mcpserver import MCPServer

    import mux.mcp.client as mcp_client
    import mux.mcp.config as mcp_config
    from mux.mcp.config import ServerSpec

    def server(*tool_names: str) -> MCPServer:
        s = MCPServer("t")
        for tool_name in tool_names:
            s.add_tool(lambda: "ok", name=tool_name, description=f"{tool_name} tool")
        return s

    available = {"docs": server("search", "fetch"), "github": server("create_issue")}

    def connect(spec):
        if spec.name not in available:
            raise ConnectionError("connection refused")
        return mcp_client.open_client(spec, target=available[spec.name])

    monkeypatch.setattr(mcp_client, "default_connect", connect)
    monkeypatch.setattr(mcp_config, "admin_servers", lambda: {"github": ServerSpec("github", command="gh-mcp")})
    monkeypatch.setattr(settings, "allow_private_urls", False)
    monkeypatch.setattr(settings, "room_secrets_key", Fernet.generate_key().decode())
    monkeypatch.setattr("mux.mcp.catalog.ADMIN_TOOLS", {})
    return available, server


def add_mcp(c, rid, name="docs", url=PUBLIC_URL, headers=None, user="alice"):
    return c.post(f"/rooms/{rid}/mcp/servers", json={"name": name, "url": url, "headers": headers or {}}, headers=auth(user))


def test_owner_adds_a_room_mcp_server(client, mcp_servers):
    rid = create_room(client)
    r = add_mcp(client, rid, headers={"Authorization": "Bearer top-secret"})
    assert r.status_code == 200, r.text
    docs = r.json()["servers"][0]
    assert docs["name"] == "docs" and docs["header_names"] == ["Authorization"]
    assert {t["name"] for t in docs["tools"]} == {"search", "fetch"}
    # Viewers can see it; only the owner can change it
    client.post(f"/rooms/{rid}/members", json={"user_id": "bob", "role": "viewer"}, headers=auth("alice"))
    assert client.get(f"/rooms/{rid}/mcp", headers=auth("bob")).status_code == 200
    assert add_mcp(client, rid, name="other", user="bob").status_code == 403
    # The token never comes back: not from the API, not over the socket
    assert "top-secret" not in client.get(f"/rooms/{rid}/mcp", headers=auth("alice")).text
    with client.websocket_connect(f"/rooms/{rid}/ws?since=0&token={token('alice')}") as ws:
        assert "top-secret" not in ws.receive_text()


def test_bad_room_mcp_servers_are_refused(client, mcp_servers, monkeypatch):
    rid = create_room(client)
    assert add_mcp(client, rid, url="http://93.184.216.34/mcp").status_code == 400
    assert add_mcp(client, rid, url="https://10.0.0.5/mcp").status_code == 400
    assert add_mcp(client, rid, name="Bad Name").status_code == 422
    assert add_mcp(client, rid, name="github").status_code == 409  # taken by mcp.json
    r = add_mcp(client, rid, name="down")
    # Room servers get one message for every network failure, so the errors can't map a private network
    assert r.status_code == 502 and r.json()["detail"] == "Could not connect to down: could not reach the server"
    assert add_mcp(client, rid, headers={"Authorization": "Bearer x\n"}).status_code == 422
    assert add_mcp(client, rid, headers={"Bad Name": "x"}).status_code == 422
    monkeypatch.setattr(settings, "room_secrets_key", "")
    assert add_mcp(client, rid, headers={"Authorization": "x"}).status_code == 400
    assert client.get(f"/rooms/{rid}/mcp", headers=auth("alice")).json()["servers"] == []


def test_room_mcp_server_limit(client, mcp_servers):
    available, server = mcp_servers
    rid = create_room(client)
    for i in range(10):
        available[f"s{i}"] = server("t")
        assert add_mcp(client, rid, name=f"s{i}").status_code == 200
    available["s10"] = server("t")
    assert add_mcp(client, rid, name="s10").status_code == 400


def test_mcp_settings_refresh_and_remove(client, mcp_servers):
    available, server = mcp_servers
    rid = create_room(client)
    add_mcp(client, rid)
    r = client.patch(f"/rooms/{rid}/mcp/servers/docs", json={"settings": {
        "search": {"enabled": True, "mode": "ask"}, "fetch": {"enabled": False, "mode": "auto"}, "nope": {"enabled": False}}},
        headers=auth("alice"))
    assert r.status_code == 200
    assert r.json()["servers"][0]["settings"] == {"search": {"enabled": True, "mode": "ask"}, "fetch": {"enabled": False, "mode": "auto"}}
    # The server drops "fetch" and adds "summarize": refresh keeps the setting of the tool that's still there
    available["docs"] = server("search", "summarize")
    r = client.post(f"/rooms/{rid}/mcp/servers/docs/refresh", headers=auth("alice"))
    assert {t["name"] for t in r.json()["servers"][0]["tools"]} == {"search", "summarize"}
    assert r.json()["servers"][0]["settings"] == {"search": {"enabled": True, "mode": "ask"}}
    assert client.delete(f"/rooms/{rid}/mcp/servers/docs", headers=auth("alice")).json()["servers"] == []
    assert client.delete(f"/rooms/{rid}/mcp/servers/docs", headers=auth("alice")).status_code == 404


def test_admin_mcp_servers_are_off_until_the_owner_turns_them_on(client, mcp_servers):
    rid = create_room(client)
    github = client.get(f"/rooms/{rid}/mcp", headers=auth("alice")).json()["admin"][0]
    assert github["name"] == "github" and github["kind"] == "stdio" and github["enabled"] is False
    assert client.patch(f"/rooms/{rid}/mcp/admin/nope", json={"enabled": True}, headers=auth("alice")).status_code == 404
    r = client.post(f"/rooms/{rid}/mcp/admin/github/refresh", headers=auth("alice"))
    assert r.json()["admin"][0]["tools_loaded"] and r.json()["admin"][0]["tools"][0]["name"] == "create_issue"
    r = client.patch(f"/rooms/{rid}/mcp/admin/github", json={"enabled": True, "settings": {"create_issue": {"mode": "ask"}}},
                     headers=auth("alice"))
    assert r.json()["admin"][0]["enabled"] is True
    assert r.json()["admin"][0]["settings"] == {"create_issue": {"enabled": True, "mode": "ask"}}


def test_mcp_settings_survive_a_restart_and_a_lost_key(client, mcp_servers, monkeypatch):
    from mux.mcp.catalog import enabled_servers
    rid = create_room(client)
    add_mcp(client, rid, headers={"Authorization": "Bearer x"})
    registry_call(client, room_registry.get_registry().stop_room, rid)
    assert client.get(f"/rooms/{rid}/mcp", headers=auth("alice")).json()["servers"][0]["name"] == "docs"
    room = registry_call(client, room_registry.get_registry().get_room, rid)
    assert [s.spec.headers for s in enabled_servers(room)] == [{"Authorization": "Bearer x"}]
    # A changed key skips the server instead of breaking the coder
    monkeypatch.setattr(settings, "room_secrets_key", "")
    assert enabled_servers(room) == []


# --- Room AI provider ----------------------------------------------------------

AI_BODY = {"provider": "openai", "base_url": "https://93.184.216.34/v1", "api_key": "sk-room-secret",
           "models": {"lightning": "fast-1", "super": "smart-1", "ultra": "smart-2"}}


@pytest.fixture
def ai_checks(monkeypatch):
    """check_models stand-in: records the key it was given, fails roles listed in `failing`."""
    from cryptography.fernet import Fernet

    import mux.agents.room_llm as room_llm
    seen: dict = {"keys": [], "failing": {}}

    async def fake_check(llm, models, secret):
        seen["keys"].append(secret)
        return dict(seen["failing"])

    monkeypatch.setattr(room_llm, "check_models", fake_check)
    monkeypatch.setattr(settings, "room_secrets_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "allow_private_urls", False)
    return seen


def test_owner_sets_the_room_ai_provider(client, ai_checks):
    rid = create_room(client)
    assert client.get(f"/rooms/{rid}/ai", headers=auth("alice")).json()["source"] == "none"
    r = client.put(f"/rooms/{rid}/ai", json=AI_BODY, headers=auth("alice"))
    assert r.status_code == 200, r.text
    view = r.json()
    assert view == {"source": "room", "provider": "openai", "base_url": AI_BODY["base_url"],
                    "models": AI_BODY["models"], "has_key": True}
    assert ai_checks["keys"] == ["sk-room-secret"]
    # Others can read it, never the key; only the owner changes it
    client.post(f"/rooms/{rid}/members", json={"user_id": "bob", "role": "editor"}, headers=auth("alice"))
    assert client.get(f"/rooms/{rid}/ai", headers=auth("bob")).json()["provider"] == "openai"
    assert client.put(f"/rooms/{rid}/ai", json=AI_BODY, headers=auth("bob")).status_code == 403
    assert "sk-room-secret" not in client.get(f"/rooms/{rid}/ai", headers=auth("alice")).text
    with client.websocket_connect(f"/rooms/{rid}/ws?since=0&token={token('alice')}") as ws:
        assert "sk-room-secret" not in ws.receive_text()


def test_empty_key_keeps_the_saved_one(client, ai_checks):
    rid = create_room(client)
    assert client.put(f"/rooms/{rid}/ai", json={**AI_BODY, "api_key": ""}, headers=auth("alice")).status_code == 400
    client.put(f"/rooms/{rid}/ai", json=AI_BODY, headers=auth("alice"))
    r = client.put(f"/rooms/{rid}/ai", json={**AI_BODY, "api_key": "", "models": {**AI_BODY["models"], "super": "smart-9"}},
                   headers=auth("alice"))
    assert r.status_code == 200 and r.json()["models"]["super"] == "smart-9"
    assert ai_checks["keys"] == ["sk-room-secret", "sk-room-secret"]


def test_bad_ai_settings_save_nothing(client, ai_checks, monkeypatch):
    rid = create_room(client)
    assert client.put(f"/rooms/{rid}/ai", json={**AI_BODY, "base_url": "https://10.0.0.5/v1"}, headers=auth("alice")).status_code == 400
    assert client.put(f"/rooms/{rid}/ai", json={**AI_BODY, "provider": "nope"}, headers=auth("alice")).status_code == 422
    assert client.put(f"/rooms/{rid}/ai", json={**AI_BODY, "models": {**AI_BODY["models"], "ultra": "has space"}},
                      headers=auth("alice")).status_code == 422
    ai_checks["failing"] = {"ultra": "NotFoundError: model smart-2 not found"}
    r = client.put(f"/rooms/{rid}/ai", json=AI_BODY, headers=auth("alice"))
    assert r.status_code == 400 and r.json()["detail"] == {"errors": {"ultra": "NotFoundError: model smart-2 not found"}}
    ai_checks["failing"] = {}
    monkeypatch.setattr(settings, "room_secrets_key", "")
    assert client.put(f"/rooms/{rid}/ai", json=AI_BODY, headers=auth("alice")).status_code == 400
    assert client.get(f"/rooms/{rid}/ai", headers=auth("alice")).json()["source"] == "none"


def test_clearing_and_restart(client, ai_checks, monkeypatch):
    rid = create_room(client)
    client.put(f"/rooms/{rid}/ai", json=AI_BODY, headers=auth("alice"))
    registry_call(client, room_registry.get_registry().stop_room, rid)
    assert client.get(f"/rooms/{rid}/ai", headers=auth("alice")).json()["source"] == "room"
    monkeypatch.setattr(settings, "token_factory_api_key", "tf-key")
    monkeypatch.setattr(settings, "token_factory_base_url", "https://tf.example/v1")
    monkeypatch.setattr(settings, "model_super", "nemotron")
    r = client.delete(f"/rooms/{rid}/ai", headers=auth("alice"))
    assert r.status_code == 200 and r.json() == {"source": "server", "provider": None, "base_url": None,
                                                 "models": None, "has_key": False}


def test_saving_checks_models_quickly(client, ai_checks, monkeypatch):
    import mux.api.ai as ai_api
    built: list[dict] = []

    class Recording:
        def __init__(self, *args, **kwargs):
            built.append(kwargs)

    monkeypatch.setattr(ai_api, "OpenAILLM", Recording)
    rid = create_room(client)
    assert client.put(f"/rooms/{rid}/ai", json=AI_BODY, headers=auth("alice")).status_code == 200
    assert built[0]["max_retries"] == 0 and built[0]["timeout"] <= 30


# --- kickoff -------------------------------------------------------------------

def test_kickoff_needs_the_owner_and_a_model(client):
    rid = create_room(client)
    client.post(f"/rooms/{rid}/members", json={"user_id": "bob", "role": "editor"}, headers=auth("alice"))
    assert client.post(f"/rooms/{rid}/kickoff", headers=auth("bob")).status_code == 403
    r = client.post(f"/rooms/{rid}/kickoff", headers=auth("alice"))
    assert r.status_code == 409 and "no AI model" in r.json()["detail"]


def test_run_plan_needs_an_editor_and_a_model(client, monkeypatch):
    rid = create_room(client)
    client.post(f"/rooms/{rid}/members", json={"user_id": "bob", "role": "viewer"}, headers=auth("alice"))
    assert client.post(f"/rooms/{rid}/plan/run", headers=auth("bob")).status_code == 403
    r = client.post(f"/rooms/{rid}/plan/run", headers=auth("alice"))
    assert r.status_code == 409 and "no AI model" in r.json()["detail"]
    runtime = room_registry.get_registry().runtime(rid)
    assert runtime is not None
    monkeypatch.setattr(runtime, "_model_available", lambda: True)
    assert client.post(f"/rooms/{rid}/plan/run", headers=auth("alice")).json() == {"accepted": True, "retried": 0}


def test_coder_stop_and_start_needs_an_editor_and_a_model(client, monkeypatch):
    rid = create_room(client)
    client.post(f"/rooms/{rid}/members", json={"user_id": "bob", "role": "viewer"}, headers=auth("alice"))
    assert client.post(f"/rooms/{rid}/coder/stop", headers=auth("bob")).status_code == 403
    assert client.post(f"/rooms/{rid}/coder/start", headers=auth("bob")).status_code == 403
    r = client.post(f"/rooms/{rid}/coder/start", headers=auth("alice"))
    assert r.status_code == 409 and "no AI model" in r.json()["detail"]
    runtime = room_registry.get_registry().runtime(rid)
    monkeypatch.setattr(runtime, "_model_available", lambda: True)
    # Start works whenever (it just sets the coder on the queued tasks), Stop is idempotent
    assert client.post(f"/rooms/{rid}/coder/start", headers=auth("alice")).json() == {"accepted": True}
    assert client.post(f"/rooms/{rid}/coder/stop", headers=auth("alice")).json() == {"accepted": True}
    assert client.post(f"/rooms/{rid}/coder/stop", headers=auth("alice")).json() == {"accepted": True}


def test_preview_errors_become_a_fix_task(client, monkeypatch):
    rid = create_room(client)
    client.post(f"/rooms/{rid}/members", json={"user_id": "bob", "role": "viewer"}, headers=auth("alice"))
    body = {"errors": ["TypeError: x is undefined (app.js:3)"], "source": "preview"}
    assert client.post(f"/rooms/{rid}/errors", json=body, headers=auth("bob")).status_code == 403
    r = client.post(f"/rooms/{rid}/errors", json=body, headers=auth("alice"))
    assert r.status_code == 409 and "no AI model" in r.json()["detail"]
    runtime = room_registry.get_registry().runtime(rid)
    assert runtime is not None
    monkeypatch.setattr(runtime, "_model_available", lambda: True)
    assert client.post(f"/rooms/{rid}/errors", json={"errors": []}, headers=auth("alice")).status_code == 422
    r = client.post(f"/rooms/{rid}/errors", json=body, headers=auth("alice"))
    assert r.status_code == 200 and r.json()["accepted"] is True and r.json()["task_id"]


def test_kickoff_is_recorded_and_not_run_twice(client, monkeypatch):
    import asyncio
    rid = create_room(client)
    runtime = room_registry.get_registry().runtime(rid)
    assert runtime is not None
    monkeypatch.setattr(runtime, "_model_available", lambda: True)

    async def no_kickoff() -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(runtime, "_kickoff", no_kickoff)
    assert client.post(f"/rooms/{rid}/kickoff", headers=auth("alice")).json() == {"accepted": True}
    for _ in range(50):
        if runtime.kickoff_running:
            break
        time.sleep(0.02)
    r = client.post(f"/rooms/{rid}/kickoff", headers=auth("alice"))
    assert r.status_code == 409 and r.json()["detail"] == "Planning is already running"
    with client.websocket_connect(f"/rooms/{rid}/ws?since=0&token={token('alice')}") as ws:
        assert '"kickoff.requested"' in ws.receive_text()


# --- Skills ------------------------------------------------------------------

@pytest.fixture
def skills_dir(tmp_path, monkeypatch):
    import mux.skills.library as skills_library
    root = tmp_path / "skills"

    def add(folder: str, description: str, body: str = "Do it well.") -> None:
        (root / folder).mkdir(parents=True)
        (root / folder / "SKILL.md").write_text(f"---\nname: {folder}\ndescription: {description}\n---\n{body}")

    add("frontend-design", "Distinctive UIs")
    add("subagent-dev", "Delegate", "Dispatch a subagent with the Task tool.")
    lib = skills_library.SkillLibrary([(root, "server")])
    monkeypatch.setattr(skills_library, "library", lambda: lib)
    return add


def test_owner_switches_skills_on(client, skills_dir):
    rid = create_room(client)
    listed = client.get(f"/rooms/{rid}/skills", headers=auth("alice")).json()
    assert [s["name"] for s in listed] == ["frontend-design", "subagent-dev"] and not any(s["enabled"] for s in listed)
    assert listed[1]["compatible"] is False and listed[1]["issues"] == ["uses Claude Code sub-agents"]
    r = client.put(f"/rooms/{rid}/skills", json={"enabled": ["frontend-design"]}, headers=auth("alice"))
    assert r.status_code == 200 and [s["name"] for s in r.json() if s["enabled"]] == ["frontend-design"]
    client.post(f"/rooms/{rid}/members", json={"user_id": "bob", "role": "viewer"}, headers=auth("alice"))
    assert client.get(f"/rooms/{rid}/skills", headers=auth("bob")).status_code == 200
    assert client.put(f"/rooms/{rid}/skills", json={"enabled": []}, headers=auth("bob")).status_code == 403
    assert client.put(f"/rooms/{rid}/skills", json={"enabled": ["nope"]}, headers=auth("alice")).status_code == 400


def test_skills_reload_and_removed_skills_show_as_missing(client, skills_dir, tmp_path):
    import shutil
    rid = create_room(client)
    client.put(f"/rooms/{rid}/skills", json={"enabled": ["frontend-design"]}, headers=auth("alice"))
    skills_dir("tdd", "Tests first")
    shutil.rmtree(tmp_path / "skills" / "frontend-design")
    listed = client.post(f"/rooms/{rid}/skills/reload", headers=auth("alice")).json()
    by_name = {s["name"]: s for s in listed}
    assert "tdd" in by_name and by_name["frontend-design"]["missing"] is True and by_name["frontend-design"]["enabled"] is True
    registry_call(client, room_registry.get_registry().stop_room, rid)
    assert any(s["enabled"] for s in client.get(f"/rooms/{rid}/skills", headers=auth("alice")).json())


def test_skill_limit(client, skills_dir):
    for i in range(30):
        skills_dir(f"s{i}", "x")
    rid = create_room(client)
    client.post(f"/rooms/{rid}/skills/reload", headers=auth("alice"))
    names = [f"s{i}" for i in range(30)] + ["frontend-design"]
    assert client.put(f"/rooms/{rid}/skills", json={"enabled": names}, headers=auth("alice")).status_code == 400


def test_owner_lists_the_models_their_key_can_use(client, ai_checks, monkeypatch):
    import mux.api.ai as ai_api
    keys: list[str] = []
    answer: dict = {"error": None}

    class Listing:
        def __init__(self, base_url, api_key, models, **kwargs):
            keys.append(api_key)

        async def list_models(self):
            if answer["error"]:
                raise RuntimeError(answer["error"])
            return ["fast-1", "smart-1"]

        async def chat(self, *args, **kwargs):
            raise AssertionError("listing never chats")

    monkeypatch.setattr(ai_api, "OpenAILLM", Listing)
    rid = create_room(client)
    body = {"base_url": AI_BODY["base_url"], "api_key": "sk-room-secret"}
    r = client.post(f"/rooms/{rid}/ai/models", json=body, headers=auth("alice"))
    assert r.status_code == 200 and r.json() == {"models": ["fast-1", "smart-1"]}
    # No key typed and none saved; then the saved key is used
    assert client.post(f"/rooms/{rid}/ai/models", json={**body, "api_key": ""}, headers=auth("alice")).status_code == 400
    client.put(f"/rooms/{rid}/ai", json=AI_BODY, headers=auth("alice"))
    assert client.post(f"/rooms/{rid}/ai/models", json={**body, "api_key": ""}, headers=auth("alice")).status_code == 200
    assert keys[-1] == "sk-room-secret"
    # Only the owner; refused addresses; provider errors come back without the key
    client.post(f"/rooms/{rid}/members", json={"user_id": "bob", "role": "editor"}, headers=auth("alice"))
    assert client.post(f"/rooms/{rid}/ai/models", json=body, headers=auth("bob")).status_code == 403
    assert client.post(f"/rooms/{rid}/ai/models", json={**body, "base_url": "https://10.0.0.5/v1"},
                       headers=auth("alice")).status_code == 400
    answer["error"] = "401 bad key sk-room-secret"
    r = client.post(f"/rooms/{rid}/ai/models", json=body, headers=auth("alice"))
    assert r.status_code == 400 and "sk-room-secret" not in r.text and "[hidden]" in r.text


async def test_list_models_reads_the_models_endpoint():
    import httpx2

    from mux.agents.llm import OpenAILLM

    def handler(request):
        assert request.url.path == "/v1/models" and request.headers["authorization"] == "Bearer k"
        return httpx2.Response(200, json={"object": "list", "data": [
            {"id": "b-model", "object": "model", "created": 0, "owned_by": "x"},
            {"id": "a-model:free", "object": "model", "created": 0, "owned_by": "x"}]})

    llm = OpenAILLM("https://api.example/v1", "k", {}, transport=httpx2.MockTransport(handler))
    assert await llm.list_models() == ["a-model:free", "b-model"]


def test_a_missing_secrets_key_is_made_and_saved(tmp_path, monkeypatch):
    import mux.secrets as secrets
    env = tmp_path / ".env"
    monkeypatch.setattr(secrets, "ENV_FILE", env)
    monkeypatch.setattr(settings, "room_secrets_key", "")
    env.write_text("WEB_APP_URL=http://localhost:3000\nROOM_SECRETS_KEY=\nALLOW_PRIVATE_URLS=false")
    secrets.ensure_secrets_key()
    key = settings.room_secrets_key
    assert key and env.read_text() == f"WEB_APP_URL=http://localhost:3000\nROOM_SECRETS_KEY={key}\nALLOW_PRIVATE_URLS=false"
    assert secrets.decrypt_value(secrets.encrypt_value("sk-x")) == "sk-x"
    # A key already set is kept, and a file without the line gets one appended
    secrets.ensure_secrets_key()
    assert settings.room_secrets_key == key
    monkeypatch.setattr(settings, "room_secrets_key", "")
    env.write_text("A=1")
    secrets.ensure_secrets_key()
    assert env.read_text() == f"A=1\nROOM_SECRETS_KEY={settings.room_secrets_key}\n"
