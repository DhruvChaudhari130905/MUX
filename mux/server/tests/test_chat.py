"""Chat extras: emoji reactions, photo uploads and photo messages."""

import pytest

import mux.rooms.registry as room_registry
from tests.test_api import auth, client, create_room, registry_call, token  # noqa: F401  (client is a fixture)


def add_editor(c, rid: str, user: str = "bob") -> None:
    assert c.post(f"/rooms/{rid}/members", json={"user_id": user}, headers=auth("alice")).status_code in (200, 201)


def add_viewer(c, rid: str, user: str = "carol") -> None:
    c.patch(f"/rooms/{rid}/sharing", json={"link_access": "anyone", "link_permission": "viewer"}, headers=auth("alice"))
    assert c.post(f"/rooms/{rid}/join", json={}, headers=auth(user)).status_code == 200


def dump(c, rid: str, user: str = "alice") -> list[dict]:
    with c.websocket_connect(f"/rooms/{rid}/ws?since=0") as ws:
        ws.send_json({"type": "auth", "payload": {"token": token(user)}})
        return ws.receive_json()


def post_team(c, rid: str, text: str = "hi", user: str = "alice", **extra) -> None:
    r = c.post(f"/rooms/{rid}/messages", json={"text": text, "to": "team", **extra}, headers=auth(user))
    assert r.status_code == 200, r.text


def message_id(c, rid: str) -> str:
    return next(e for e in dump(c, rid) if e["type"] == "message.posted")["payload"]["id"]


def react(c, rid: str, mid: str, emoji: str, user: str = "alice"):
    return c.post(f"/rooms/{rid}/messages/{mid}/reactions", json={"emoji": emoji}, headers=auth(user))


# --- reactions -------------------------------------------------------------

def test_reaction_toggles_on_and_off(client):
    rid = create_room(client)
    post_team(client, rid)
    mid = message_id(client, rid)

    assert react(client, rid, mid, "👍").status_code == 200
    assert react(client, rid, mid, "👍").status_code == 200

    reacted = [e["payload"] for e in dump(client, rid) if e["type"] == "message.reacted"]
    assert reacted == [
        {"message_id": mid, "user_id": "alice", "emoji": "👍", "added": True},
        {"message_id": mid, "user_id": "alice", "emoji": "👍", "added": False},
    ]


def test_reactions_survive_rehydration(client):
    rid = create_room(client)
    add_editor(client, rid)
    post_team(client, rid)
    mid = message_id(client, rid)
    react(client, rid, mid, "🎉")
    react(client, rid, mid, "🎉", user="bob")

    registry_call(client, room_registry.get_registry().stop_room, rid)

    # After rehydrating, bob's toggle removes his reaction (it was on), alice's stays
    assert react(client, rid, mid, "🎉", user="bob").status_code == 200
    last = [e["payload"] for e in dump(client, rid) if e["type"] == "message.reacted"][-1]
    assert last == {"message_id": mid, "user_id": "bob", "emoji": "🎉", "added": False}


def test_reaction_on_unknown_message_is_404(client):
    rid = create_room(client)
    assert react(client, rid, "nope", "👍").status_code == 404


@pytest.mark.parametrize("emoji", ["", "a", "ok", "1", " 👍", "👍" * 17, "<b>"])
def test_reaction_rejects_non_emoji(client, emoji):
    rid = create_room(client)
    post_team(client, rid)
    assert react(client, rid, message_id(client, rid), emoji).status_code == 422


def test_viewer_cannot_react(client):
    rid = create_room(client)
    add_viewer(client, rid)
    post_team(client, rid)
    assert react(client, rid, message_id(client, rid), "👍", user="carol").status_code == 403


# --- photos ----------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 32
GIF = b"GIF89a" + b"\0" * 32
WEBP = b"RIFF\0\0\0\0WEBP" + b"\0" * 32


def upload(c, rid: str, data: bytes, name: str = "a.png", user: str = "alice"):
    return c.post(f"/rooms/{rid}/uploads", files={"file": (name, data, "image/png")}, headers=auth(user))


@pytest.mark.parametrize("data,content_type", [(PNG, "image/png"), (JPEG, "image/jpeg"),
                                               (GIF, "image/gif"), (WEBP, "image/webp")])
def test_upload_and_fetch_image(client, data, content_type):
    rid = create_room(client)
    r = upload(client, rid, data)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content_type"] == content_type and len(body["id"]) == 32

    got = client.get(f"/rooms/{rid}/uploads/{body['id']}", headers=auth("alice"))
    assert got.status_code == 200
    assert got.content == data
    assert got.headers["content-type"] == content_type
    assert got.headers["x-content-type-options"] == "nosniff"


def test_upload_rejects_svg_and_fake_png(client):
    rid = create_room(client)
    assert upload(client, rid, b"<svg xmlns='http://www.w3.org/2000/svg'/>", "a.svg").status_code == 415
    assert upload(client, rid, b"<html><script>alert(1)</script>", "a.png").status_code == 415


def test_upload_rejects_oversize(client):
    from mux.rooms.uploads import MAX_UPLOAD_BYTES
    rid = create_room(client)
    assert upload(client, rid, PNG + b"\0" * MAX_UPLOAD_BYTES).status_code == 413


def test_viewer_cannot_upload_but_can_view(client):
    rid = create_room(client)
    add_viewer(client, rid)
    assert upload(client, rid, PNG, user="carol").status_code == 403
    uid = upload(client, rid, PNG).json()["id"]
    assert client.get(f"/rooms/{rid}/uploads/{uid}", headers=auth("carol")).status_code == 200


def test_non_member_cannot_fetch(client):
    rid = create_room(client)
    uid = upload(client, rid, PNG).json()["id"]
    assert client.get(f"/rooms/{rid}/uploads/{uid}", headers=auth("mallory")).status_code in (403, 404)


@pytest.mark.parametrize("bad", ["..%2F..%2Fsecret", "ABCDEF" * 5 + "AB", "abc", "0" * 31, "0" * 33])
def test_get_upload_rejects_bad_ids(client, bad):
    rid = create_room(client)
    assert client.get(f"/rooms/{rid}/uploads/{bad}", headers=auth("alice")).status_code == 404


def test_photo_only_team_message(client):
    rid = create_room(client)
    uid = upload(client, rid, PNG).json()["id"]
    r = client.post(f"/rooms/{rid}/messages", json={"text": "", "to": "team", "attachments": [uid]}, headers=auth("alice"))
    assert r.status_code == 200, r.text
    posted = next(e for e in dump(client, rid) if e["type"] == "message.posted")["payload"]
    assert posted["attachments"] == [uid] and posted["text"] == ""


def test_old_and_text_messages_have_no_attachments(client):
    rid = create_room(client)
    post_team(client, rid, "hello")
    posted = next(e for e in dump(client, rid) if e["type"] == "message.posted")["payload"]
    assert posted["attachments"] == []


@pytest.mark.parametrize("body", [
    {"text": "", "to": "team"},                                  # nothing to send
    {"text": "look", "to": "agent", "attachments": ["0" * 32]},  # photos are team-only
    {"text": "", "to": "team", "attachments": ["0" * 32]},       # no such upload
    {"text": "", "to": "team", "attachments": ["0" * 32] * 5},   # more than 4
])
def test_photo_message_validation(client, body):
    rid = create_room(client)
    assert client.post(f"/rooms/{rid}/messages", json=body, headers=auth("alice")).status_code == 422


def test_message_rejects_upload_from_another_room(client):
    rid1, rid2 = create_room(client), create_room(client)
    uid = upload(client, rid1, PNG).json()["id"]
    r = client.post(f"/rooms/{rid2}/messages", json={"text": "", "to": "team", "attachments": [uid]}, headers=auth("alice"))
    assert r.status_code == 422
