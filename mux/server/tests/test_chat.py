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
