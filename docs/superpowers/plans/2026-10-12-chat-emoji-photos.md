# Chat Emojis, Reactions and Photos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An emoji picker in the chat box, emoji reactions on people's messages, and photo sharing in the Team window.

**Architecture:** Reactions and photo attachments are new room events (`message_reaction_toggled`, an `attachments` field on `user_message_sent`) so they replay like everything else. Photos are stored per room under `.mux/uploads/{room_id}/` by a small `UploadStore`, uploaded and served by a new `mux/api/uploads.py` router; the browser fetches them with the bearer token and shows object URLs. The web side extends the reducer, the API client, `Composer` and `FeedItem`.

**Tech Stack:** FastAPI + Pydantic v2 + pytest (`mux/server`), Next.js 15 / React 18 / TypeScript (`mux/web`), `emoji-picker-react@4.12.0`, `python-multipart`.

**Spec:** `docs/superpowers/specs/2026-10-12-chat-emoji-photos-design.md`

## Global Constraints

- Photos: PNG, JPEG, GIF, WebP only, detected from the bytes; SVG rejected. Max 5 MB each (`5 * 1024 * 1024`), max 4 per message.
- Photos only in Team messages (`to == "team"`); a photo message to the agent is a 422.
- Upload ids match `^[0-9a-f]{32}$`.
- Reactions and uploads need `require_editor`; reading an image needs `require_viewer`.
- Reactions only on people's messages (ids from `user_message_sent`); unknown id → 404.
- An emoji is 1–16 characters with no ASCII letters, digits or whitespace and at least one non-ASCII character.
- Old events (no `attachments`, no reactions) must replay unchanged.
- Server tests run from `mux/server` with `.venv/bin/pytest`; web checks run from `mux/web`.
- Match the surrounding code: short comments only where the reason isn't obvious, no new abstractions beyond those named here.

## Review Focus

1. **Rewind and rehydrate:** a room with reactions is stopped and reopened; the reaction set must be the same, not doubled or lost (Task 1, `test_reactions_survive_rehydration`).
2. **A file named `.png` that's really HTML or SVG:** must be refused with 415, never stored or served (Task 2, `test_upload_rejects_svg_and_fake_png`).
3. **Path tricks in the image URL** (`../`, uppercase or short ids): 404, never a file outside the room's folder (Task 2, `test_get_upload_rejects_bad_ids`).
4. **Someone else's room's upload id attached to a message:** 422, since each room only sees its own folder (Task 2, `test_message_rejects_upload_from_another_room`).
5. **Reactions from two users on one emoji, one un-reacts:** the chip keeps the other user (Task 3, reducer replay check).

---

## File map

| File | Change |
|---|---|
| `mux/server/mux/events/models.py` | `MESSAGE_REACTION_TOGGLED`, `MessageReactionToggledEvent`, `UserMessageSentEvent.attachments` |
| `mux/server/mux/rooms/actor.py` | `message_ids`, `reactions`, `toggle_reaction`, `add_message(attachments=)`, replay branches |
| `mux/server/mux/events/wire.py` | `message.reacted`; `attachments` on `message.posted` |
| `mux/server/mux/api/rooms.py` | reaction endpoint; `MessageCreateRequest` text optional + attachments |
| `mux/server/mux/rooms/uploads.py` (new) | `UploadStore`, `sniff_image`, `MAX_UPLOAD_BYTES` |
| `mux/server/mux/api/uploads.py` (new) | `POST/GET /rooms/{id}/uploads` |
| `mux/server/mux/main.py` | register the uploads router |
| `mux/server/pyproject.toml` | `python-multipart` |
| `mux/server/tests/test_chat.py` (new) | reaction, upload and attachment tests |
| `mux/web/src/types/index.ts` | `Message.attachments`, `Message.reactions`, `message.reacted` event |
| `mux/web/src/lib/reducer.ts` | `message.reacted` |
| `mux/web/src/lib/api.ts` | `sendMessage(…, attachments)`, `toggleReaction`, `uploadImage`, `fetchUpload` |
| `mux/web/src/components/feed/EmojiPicker.tsx` (new) | lazy picker popover |
| `mux/web/src/components/feed/Composer.tsx` | emoji button, photo button, paste, previews |
| `mux/web/src/components/feed/Reactions.tsx` (new) | hover bar + chips |
| `mux/web/src/components/feed/MessageImage.tsx` (new) | authenticated image + lightbox |
| `mux/web/src/components/feed/FeedItem.tsx`, `Feed.tsx` | render reactions and images; pass room id and permissions |
| `mux/web/src/app/room/[roomId]/page.tsx` | `handleSendMessage` attachments, pass `roomId`/`canReact` |
| `mux/web/src/app/globals.css` | styles for the above |

---

### Task 1: Reactions on the server

**Files:**
- Modify: `mux/server/mux/events/models.py` (EventType enum near line 56; after `UserMessageSentEvent` ~line 369)
- Modify: `mux/server/mux/rooms/actor.py` (`__init__` near line 209, `add_message` ~791, `_apply_event` ~1257)
- Modify: `mux/server/mux/events/wire.py` (imports at top; `_payload` near line 158)
- Modify: `mux/server/mux/api/rooms.py` (request models ~159; endpoint after `send_message` ~617)
- Test: `mux/server/tests/test_chat.py` (new)

**Interfaces:**
- Produces: `RoomActor.toggle_reaction(message_id: str, user_id: str, emoji: str) -> bool` (raises `KeyError` for an unknown message); `RoomActor.message_ids: set[str]`; `RoomActor.reactions: dict[str, dict[str, set[str]]]`; envelope `message.reacted` with payload `{"message_id", "user_id", "emoji", "added"}`; `POST /rooms/{room_id}/messages/{message_id}/reactions` body `{"emoji": str}` → `Accepted`.

- [ ] **Step 1: Write the failing tests**

Create `mux/server/tests/test_chat.py`:

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run (from `mux/server`): `.venv/bin/pytest tests/test_chat.py -q`
Expected: FAIL — the reaction endpoint returns 404/405 for every call (`test_reaction_toggles_on_and_off` asserts 200).

- [ ] **Step 3: Add the event**

In `mux/server/mux/events/models.py`, add to `EventType` right after `USER_MESSAGE_SENT = "user_message_sent"`:

```python
    MESSAGE_REACTION_TOGGLED = "message_reaction_toggled"
```

After the `UserMessageSentEvent` class:

```python
class MessageReactionToggledEvent(BaseEvent):
    """A person added or removed an emoji reaction on a person's message."""
    type: EventType = EventType.MESSAGE_REACTION_TOGGLED
    message_id: str = Field(..., description="The reacted-to message (a user_message_sent message_id)")
    emoji: str = Field(..., description="The emoji")
    added: bool = Field(..., description="True when added, False when removed")
```

- [ ] **Step 4: Actor state, replay and toggle**

In `mux/server/mux/rooms/actor.py`:

1. Add `MessageReactionToggledEvent` to the existing `from mux.events.models import (...)` list.
2. In `__init__`, next to `self.members: dict[str, str] = {}`:

```python
        self.message_ids: set[str] = set()  # people's messages, the ones that can get reactions
        self.reactions: dict[str, dict[str, set[str]]] = {}  # message_id -> emoji -> user ids
```

3. In `add_message`, after `await self._emit(event)`:

```python
            self.message_ids.add(event.message_id)
```

4. In `_apply_event`, extend the `USER_MESSAGE_SENT` branch and add a branch after it:

```python
            elif t == EventType.USER_MESSAGE_SENT:
                e = cast(UserMessageSentEvent, event)
                if e.user_name:
                    self.member_names[e.user_id] = e.user_name
                self.message_ids.add(e.message_id)
            elif t == EventType.MESSAGE_REACTION_TOGGLED:
                # Chat state, not document state: a rewind re-applies earlier events with this False
                if include_session_state:
                    e = cast(MessageReactionToggledEvent, event)
                    users = self.reactions.setdefault(e.message_id, {}).setdefault(e.emoji, set())
                    if e.added:
                        users.add(e.user_id)
                    else:
                        users.discard(e.user_id)
```

5. After `add_message`, add:

```python
    async def toggle_reaction(self, message_id: str, user_id: str, emoji: str) -> bool:
        """Add or remove `user_id`'s `emoji` on a person's message; returns whether it is now on.

        Raises KeyError for a message id that isn't a person's message in this room.
        """
        async with self._lock:
            if message_id not in self.message_ids:
                raise KeyError(message_id)
            users = self.reactions.setdefault(message_id, {}).setdefault(emoji, set())
            added = user_id not in users
            if added:
                users.add(user_id)
            else:
                users.discard(user_id)
            await self._emit(MessageReactionToggledEvent(
                **self._event_fields(user_id), message_id=message_id, emoji=emoji, added=added,
            ))
            return added
```

- [ ] **Step 5: Envelope**

In `mux/server/mux/events/wire.py`, add `MessageReactionToggledEvent` to the `from mux.events.models import (...)` list and, right after the `USER_MESSAGE_SENT` branch in `_payload`:

```python
    if t == EventType.MESSAGE_REACTION_TOGGLED:
        e = cast(MessageReactionToggledEvent, event)
        return "message.reacted", {"message_id": e.message_id, "user_id": e.user_id, "emoji": e.emoji, "added": e.added}
```

- [ ] **Step 6: Endpoint**

In `mux/server/mux/api/rooms.py`, change the pydantic import to `from pydantic import BaseModel, Field, field_validator, model_validator`, and after `MessageCreateRequest`:

```python
def is_emoji(value: str) -> bool:
    """One emoji (or a short ZWJ/skin-tone sequence): no ASCII letters, digits or spaces, not plain ASCII."""
    if not 1 <= len(value) <= 16 or value.isascii():
        return False
    return not any(c.isspace() or (c.isascii() and c.isalnum()) for c in value)


class ReactionRequest(BaseModel):
    """`POST /rooms/{id}/messages/{message_id}/reactions`: toggles the caller's reaction."""
    emoji: str

    @field_validator("emoji")
    @classmethod
    def _one_emoji(cls, value: str) -> str:
        if not is_emoji(value):
            raise ValueError("emoji must be a single emoji")
        return value
```

After `send_message`:

```python
@router.post("/{room_id}/messages/{message_id}/reactions", response_model=Accepted)
async def toggle_reaction(
    room_id: str,
    message_id: str,
    request: ReactionRequest,
    current_user: User = Depends(require_editor),
    actor: RoomActor = Depends(get_room_actor),
) -> Accepted:
    """Add the caller's emoji to a person's message, or remove it if it's already there."""
    try:
        await actor.toggle_reaction(message_id, current_user.id, request.emoji)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    return accepted(actor)
```

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/pytest tests/test_chat.py -q`
Expected: all reaction tests PASS.

- [ ] **Step 8: Full suite and types**

Run: `.venv/bin/pytest -q && .venv/bin/pyright mux tests scripts`
Expected: no failures (DB tests skip or pass depending on Postgres), pyright 0 errors.

- [ ] **Step 9: Commit**

```bash
git add mux/server/mux/events/models.py mux/server/mux/rooms/actor.py mux/server/mux/events/wire.py mux/server/mux/api/rooms.py mux/server/tests/test_chat.py
git commit -m "Add emoji reactions on chat messages"
```

---

### Task 2: Photo uploads and photo messages on the server

**Files:**
- Create: `mux/server/mux/rooms/uploads.py`
- Create: `mux/server/mux/api/uploads.py`
- Modify: `mux/server/mux/main.py:10,106-116` (import and register router)
- Modify: `mux/server/mux/events/models.py` (`UserMessageSentEvent`)
- Modify: `mux/server/mux/rooms/actor.py` (`add_message`)
- Modify: `mux/server/mux/events/wire.py` (`message.posted`)
- Modify: `mux/server/mux/api/rooms.py` (`MessageCreateRequest`, `send_message`)
- Modify: `mux/server/pyproject.toml` (dependencies)
- Test: `mux/server/tests/test_chat.py`

**Interfaces:**
- Consumes: the helpers in `tests/test_chat.py` from Task 1.
- Produces: `UploadStore(root: Path)` with `.save(data: bytes) -> tuple[str, str]` (id, content type; `ValueError` for unsupported bytes) and `.find(upload_id: str) -> Optional[tuple[Path, str]]`; `room_uploads(room_id: str) -> UploadStore`; `MAX_UPLOAD_BYTES`; `POST /rooms/{id}/uploads` (multipart field `file`) → `{"id": str, "content_type": str}`; `GET /rooms/{id}/uploads/{upload_id}` → image bytes; `MessageCreateRequest.attachments: list[str]`; `message.posted` payload gains `"attachments": list[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `mux/server/tests/test_chat.py`:

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_chat.py -q -k "upload or photo or attachments"`
Expected: FAIL — `/rooms/{id}/uploads` returns 404/405.

- [ ] **Step 3: The store**

Create `mux/server/mux/rooms/uploads.py`:

```python
"""Photos people share in the Team chat: <cwd>/.mux/uploads/<room_id>/<id>.<ext>.

The type comes from the file's first bytes, never its name or the browser's claim, and only raster images
are kept: an SVG or HTML file could run script when opened from the API's address.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from uuid import uuid4

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_ATTACHMENTS = 4

_ID = re.compile(r"^[0-9a-f]{32}$")
_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


def sniff_image(data: bytes) -> Optional[str]:
    """The file extension for PNG, JPEG, GIF or WebP bytes, else None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


class UploadStore:
    """One room's uploaded images."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, data: bytes) -> tuple[str, str]:
        """Store the image; returns (upload id, content type). ValueError if it isn't a supported image."""
        ext = sniff_image(data)
        if ext is None:
            raise ValueError("Only PNG, JPEG, GIF and WebP images can be uploaded")
        upload_id = uuid4().hex
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{upload_id}.{ext}").write_bytes(data)
        return upload_id, _TYPES[ext]

    def find(self, upload_id: str) -> Optional[tuple[Path, str]]:
        """(path, content type) for a stored id, else None. Ids are checked first, so no path escapes."""
        if not _ID.match(upload_id):
            return None
        for ext, content_type in _TYPES.items():
            path = self.root / f"{upload_id}.{ext}"
            if path.is_file():
                return path, content_type
        return None


def room_uploads(room_id: str) -> UploadStore:
    """The store for a room (room ids are checked against ROOM_ID_PATTERN before they get here)."""
    return UploadStore(Path.cwd() / ".mux" / "uploads" / room_id)
```

- [ ] **Step 4: The router**

Create `mux/server/mux/api/uploads.py`:

```python
"""Photos for the Team chat (mux/rooms/uploads.py): editors upload, anyone in the room views."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from mux.api.deps import User, get_room_actor_dep, require_editor, require_viewer
from mux.rooms.actor import RoomActor
from mux.rooms.uploads import MAX_UPLOAD_BYTES, room_uploads

router = APIRouter()


class UploadResult(BaseModel):
    id: str
    content_type: str


@router.post("/{room_id}/uploads", response_model=UploadResult)
async def upload_image(
    room_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(require_editor),
    actor: RoomActor = Depends(get_room_actor_dep),
) -> UploadResult:
    """Store a PNG, JPEG, GIF or WebP image of at most 5 MB; send its id with a Team message."""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Images can be at most 5 MB")
    try:
        upload_id, content_type = room_uploads(actor.room_id).save(data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(e))
    return UploadResult(id=upload_id, content_type=content_type)


@router.get("/{room_id}/uploads/{upload_id}")
async def get_image(
    room_id: str,
    upload_id: str,
    current_user: User = Depends(require_viewer),
    actor: RoomActor = Depends(get_room_actor_dep),
) -> FileResponse:
    """The image, for people in the room only."""
    found = room_uploads(actor.room_id).find(upload_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    path, content_type = found
    return FileResponse(path, media_type=content_type, headers={
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=86400",
    })
```

In `mux/server/mux/main.py`: add `uploads` to `from mux.api import rooms, files, commands, export, ws, mcp, ai, skills, account` and, after the `skills.router` line:

```python
    app.include_router(uploads.router, prefix="/rooms", tags=["uploads"])
```

In `mux/server/pyproject.toml`, add to `dependencies = [...]`:

```toml
    "python-multipart>=0.0.9",
```

- [ ] **Step 5: Attachments on messages**

`mux/server/mux/events/models.py`, in `UserMessageSentEvent` after `to`:

```python
    attachments: list[str] = Field(default_factory=list, description="Upload ids of photos (Team notes only)")
```

`mux/server/mux/rooms/actor.py`, `add_message`: add a keyword parameter `attachments: Optional[list[str]] = None` after `user_name`, and pass `attachments=attachments or []` to `UserMessageSentEvent(...)`.

`mux/server/mux/events/wire.py`, in the `message.posted` payload dict, add after `"to": e.to or "agent",`:

```python
            "attachments": e.attachments,
```

`mux/server/mux/api/rooms.py`: add `from mux.rooms.uploads import MAX_ATTACHMENTS, room_uploads` to the imports, and replace `MessageCreateRequest`:

```python
class MessageCreateRequest(BaseModel):
    """`POST /rooms/{id}/messages`. Text, photos (Team notes only), or both."""
    text: str = Field("", max_length=10000)
    to: Literal["agent", "team"] = "agent"
    attachments: list[str] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)

    @model_validator(mode="after")
    def _has_content(self) -> "MessageCreateRequest":
        if not self.text and not self.attachments:
            raise ValueError("A message needs text or a photo")
        if self.attachments and self.to != "team":
            raise ValueError("Photos can only be sent to the team")
        return self
```

In `send_message`, before `await actor.add_message(...)`:

```python
    uploads = room_uploads(actor.room_id)
    missing = [a for a in request.attachments if uploads.find(a) is None]
    if missing:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown photo: {missing[0]}")
```

and add `attachments=request.attachments,` to the `actor.add_message(...)` call.

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/pytest tests/test_chat.py -q`
Expected: all PASS. If `test_get_upload_rejects_bad_ids["..%2F..%2Fsecret"]` returns 404 from routing rather than from `find`, that still satisfies the test.

- [ ] **Step 7: Full suite and types**

Run: `.venv/bin/pytest -q && .venv/bin/pyright mux tests scripts`
Expected: no failures; pyright 0 errors. Existing message tests that post `{"text": "hi"}` still pass (text is still accepted alone).

- [ ] **Step 8: Commit**

```bash
git add mux/server/mux/rooms/uploads.py mux/server/mux/api/uploads.py mux/server/mux/main.py mux/server/mux/events/models.py mux/server/mux/rooms/actor.py mux/server/mux/events/wire.py mux/server/mux/api/rooms.py mux/server/pyproject.toml mux/server/tests/test_chat.py
git commit -m "Add photo uploads and photo messages to the Team chat"
```

---

### Task 3: Web types, reducer and API client

**Files:**
- Modify: `mux/web/src/types/index.ts` (`Message` ~138; `EventType` list ~275; event interfaces ~330; `TypedEventType` ~446; `AppEvent` ~478)
- Modify: `mux/web/src/lib/reducer.ts` (after `message.labeled`)
- Modify: `mux/web/src/lib/api.ts` (`sendMessage` ~133; demo branch for `/messages` ~30)
- Test: replay script in the scratchpad (no web test runner in the repo)

**Interfaces:**
- Consumes: envelopes from Tasks 1–2.
- Produces: `Message.attachments?: string[]`, `Message.reactions?: Record<string, string[]>`; `MessageReactedEvent`; `api.sendMessage(roomId, text, to, attachments?: string[])`, `api.toggleReaction(roomId, messageId, emoji)`, `api.uploadImage(roomId, file: File) => Promise<{ id: string; content_type: string }>`, `api.fetchUpload(roomId, uploadId) => Promise<Blob>`.

- [ ] **Step 1: Write the failing check**

Create `<scratchpad>/reactions-check.ts` (scratchpad = this session's scratchpad directory):

```ts
import { applyEvent, createEmptyState } from './reducer.ts';

const ev = (seq: number, type: string, payload: unknown) => ({ seq, room_id: 'r', type, actor_id: 'x', ts: '2026-01-01T00:00:00Z', payload }) as any;
const user = { id: 'alice', email: '', name: 'Alice', initials: 'AL', color: '' };
let s = createEmptyState();
s = applyEvent(s, ev(1, 'message.posted', { id: 'm1', room_id: 'r', user_id: 'alice', text: '', to: 'team', attachments: ['a'.repeat(32)], created_at: '2026-01-01T00:00:00Z', user }));
s = applyEvent(s, ev(2, 'message.reacted', { message_id: 'm1', user_id: 'alice', emoji: '👍', added: true }));
s = applyEvent(s, ev(3, 'message.reacted', { message_id: 'm1', user_id: 'bob', emoji: '👍', added: true }));
s = applyEvent(s, ev(4, 'message.reacted', { message_id: 'm1', user_id: 'alice', emoji: '👍', added: false }));
s = applyEvent(s, ev(5, 'message.reacted', { message_id: 'm1', user_id: 'bob', emoji: '🎉', added: true }));
s = applyEvent(s, ev(6, 'message.reacted', { message_id: 'm1', user_id: 'bob', emoji: '🎉', added: false }));
s = applyEvent(s, ev(7, 'message.reacted', { message_id: 'm1', user_id: 'bob', emoji: '👍', added: true })); // repeat: no duplicate
const got = JSON.stringify(s.messages[0].reactions);
const want = JSON.stringify({ '👍': ['bob'] });
if (got !== want) { console.error('FAIL', got, '!=', want); process.exit(1); }
if (s.messages[0].attachments?.length !== 1) { console.error('FAIL attachments'); process.exit(1); }
console.log('PASS');
```

Run (from `mux/web`): `cp src/lib/reducer.ts <scratchpad>/reducer.ts && node <scratchpad>/reactions-check.ts`
Expected: `FAIL undefined != {"👍":["bob"]}`.

- [ ] **Step 2: Types**

In `mux/web/src/types/index.ts`:

`Message`, after `to?: MessageTo;`:

```ts
  // Upload ids of photos in a Team note (GET /rooms/{id}/uploads/{upload_id})
  attachments?: string[];
  // emoji -> ids of the people who reacted with it
  reactions?: Record<string, string[]>;
```

In the `EventType` union after `| 'message.labeled'`: add `| 'message.reacted'`.

After `MessageLabeledEvent`:

```ts
export interface MessageReactedEvent extends BaseEvent {
  type: 'message.reacted';
  payload: { message_id: string; user_id: string; emoji: string; added: boolean };
}
```

Add `| 'message.reacted'` after `| 'message.labeled'` in `TypedEventType`, and `| MessageReactedEvent` after `| MessageLabeledEvent` in `AppEvent`.

- [ ] **Step 3: Reducer**

In `mux/web/src/lib/reducer.ts`, after the `message.labeled` case:

```ts
    case 'message.reacted': {
      const { message_id, user_id, emoji, added } = event.payload;
      newState.messages = newState.messages.map(m => {
        if (m.id !== message_id) return m;
        const users = (m.reactions?.[emoji] ?? []).filter(u => u !== user_id);
        const reactions = { ...m.reactions, [emoji]: added ? [...users, user_id] : users };
        if (!reactions[emoji].length) delete reactions[emoji];
        return { ...m, reactions };
      });
      break;
    }
```

- [ ] **Step 4: Run the check**

Run: `cp src/lib/reducer.ts <scratchpad>/reducer.ts && node <scratchpad>/reactions-check.ts`
Expected: `PASS`.

- [ ] **Step 5: API client**

In `mux/web/src/lib/api.ts`, replace `sendMessage` and add the three new calls after it:

```ts
  sendMessage: (roomId: string, text: string, to: MessageTo = 'agent', attachments: string[] = []) =>
    fetchWithAuth<{ accepted: true; seq: number }>(`/rooms/${roomId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ text, to, ...(attachments.length ? { attachments } : {}) }),
    }),
  // Toggles the caller's reaction (editors only; only people's messages)
  toggleReaction: (roomId: string, messageId: string, emoji: string) =>
    fetchWithAuth<{ accepted: true; seq: number }>(`/rooms/${roomId}/messages/${messageId}/reactions`, {
      method: 'POST',
      body: JSON.stringify({ emoji }),
    }),
  // PNG, JPEG, GIF or WebP up to 5 MB; send the id with a Team message
  uploadImage: async (roomId: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    const token = await getAccessToken();
    const res = await fetch(`${API_BASE}/rooms/${roomId}/uploads`, {
      method: 'POST',
      body: form, // the browser sets the multipart Content-Type with its boundary
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) {
      const error = await res.json().catch(() => ({}));
      throw new ApiError(typeof error.detail === 'string' ? error.detail : `HTTP ${res.status}`, res.status);
    }
    return res.json() as Promise<{ id: string; content_type: string }>;
  },
  // <img> can't send the token, so images are fetched here and shown from an object URL
  fetchUpload: async (roomId: string, uploadId: string) => {
    const token = await getAccessToken();
    const res = await fetch(`${API_BASE}/rooms/${roomId}/uploads/${uploadId}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) throw new ApiError(`HTTP ${res.status}`, res.status);
    return res.blob();
  },
```

- [ ] **Step 6: Type-check and lint**

Run (from `mux/web`): `npx tsc --noEmit && npm run lint -- --max-warnings=0`
Expected: 0 errors, 0 warnings.

- [ ] **Step 7: Commit**

```bash
git add mux/web/src/types/index.ts mux/web/src/lib/reducer.ts mux/web/src/lib/api.ts
git commit -m "Handle reactions and photo attachments in the web client"
```

---

### Task 4: Emoji picker in the composer

**Files:**
- Modify: `mux/web/package.json`, `mux/web/package-lock.json` (via npm)
- Create: `mux/web/src/components/feed/EmojiPicker.tsx`
- Modify: `mux/web/src/components/feed/Composer.tsx`
- Modify: `mux/web/src/app/globals.css` (after `.composer .to-toggle`, ~line 441)

**Interfaces:**
- Produces: `<EmojiButton onPick={(emoji: string) => void} label?: string className?: string />` — a button that opens the picker popover and calls `onPick` with the native emoji.

- [ ] **Step 1: Install**

Run (from `mux/web`): `npm install emoji-picker-react@4.12.0`
Expected: added to `dependencies`; `npm audit --omit=dev` shows no new high/critical.

- [ ] **Step 2: The picker**

Create `mux/web/src/components/feed/EmojiPicker.tsx`:

```tsx
'use client';

import React from 'react';
import dynamic from 'next/dynamic';
import { Smile } from 'lucide-react';
import { Theme, EmojiStyle } from 'emoji-picker-react';

// Loaded on first open, so the room's bundle doesn't carry the emoji data
const Picker = dynamic(() => import('emoji-picker-react'), { ssr: false, loading: () => <div className="emoji-loading">Loading…</div> });

interface EmojiButtonProps {
  onPick: (emoji: string) => void;
  label?: string;
  className?: string;
  children?: React.ReactNode;
}

export function EmojiButton({ onPick, label = 'Add emoji', className = 'btn icon', children }: EmojiButtonProps) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="emoji-anchor" ref={ref}>
      <button type="button" className={className} aria-label={label} title={label} aria-expanded={open} onClick={() => setOpen(o => !o)}>
        {children ?? <Smile className="h-4 w-4" aria-hidden="true" />}
      </button>
      {open && (
        <div className="emoji-pop" role="dialog" aria-label="Emoji picker">
          <Picker
            theme={Theme.AUTO}
            emojiStyle={EmojiStyle.NATIVE}
            lazyLoadEmojis
            previewConfig={{ showPreview: false }}
            width={300}
            height={360}
            onEmojiClick={(data) => { onPick(data.emoji); setOpen(false); }}
          />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Use it in the composer**

In `mux/web/src/components/feed/Composer.tsx`:

1. `import { EmojiButton } from './EmojiPicker';`
2. Change the React import to `import React, { useId, useRef, useState, FormEvent } from 'react';` and add `const inputRef = useRef<HTMLInputElement>(null);` after the existing `useState` calls.
3. Add:

```tsx
  // Insert at the cursor (or the end), then put the cursor after the emoji
  const insertEmoji = (emoji: string) => {
    const input = inputRef.current;
    const start = input?.selectionStart ?? text.length;
    const end = input?.selectionEnd ?? text.length;
    handleChange(text.slice(0, start) + emoji + text.slice(end));
    requestAnimationFrame(() => {
      input?.focus();
      input?.setSelectionRange(start + emoji.length, start + emoji.length);
    });
  };
```

4. Give the `<input>` `ref={inputRef}`, and put `<EmojiButton onPick={insertEmoji} />` between the `<input>` and the Send button.

- [ ] **Step 4: Styles**

In `mux/web/src/app/globals.css`, after `.composer .to-toggle { … }`:

```css
.emoji-anchor { position: relative; display: flex; }
.emoji-pop { position: absolute; bottom: calc(100% + 6px); right: 0; z-index: 50; box-shadow: 0 8px 24px rgb(0 0 0 / .35); border-radius: 8px; }
.emoji-loading { width: 300px; height: 120px; display: grid; place-items: center; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; color: var(--muted); font-size: 12px; }
.btn.icon { padding: 6px 8px; display: inline-flex; align-items: center; }
```

If `.btn.icon` already exists in `globals.css` (`grep -n "\.btn\.icon" src/app/globals.css`), skip that last line.

- [ ] **Step 5: Check**

Run: `npx tsc --noEmit && npm run lint -- --max-warnings=0`
Expected: 0 errors. In the running room (dev server reloads), the 😊 button opens the picker and inserts at the cursor in both windows; Escape and an outside click close it.

- [ ] **Step 6: Commit**

```bash
git add mux/web/package.json mux/web/package-lock.json mux/web/src/components/feed/EmojiPicker.tsx mux/web/src/components/feed/Composer.tsx mux/web/src/app/globals.css
git commit -m "Add an emoji picker to the chat box"
```

---

### Task 5: Reaction bar and chips

**Files:**
- Create: `mux/web/src/components/feed/Reactions.tsx`
- Modify: `mux/web/src/components/feed/FeedItem.tsx`
- Modify: `mux/web/src/components/feed/Feed.tsx` (props ~15, destructuring ~105, `FeedItem` uses ~175 and ~184)
- Modify: `mux/web/src/app/room/[roomId]/page.tsx` (`<Feed …>` ~512)
- Modify: `mux/web/src/app/globals.css`

**Interfaces:**
- Consumes: `api.toggleReaction`, `Message.reactions`, `EmojiButton` (Tasks 3–4).
- Produces: `FeedItem` props `roomId: string`, `canReact: boolean`, `memberNames?: Record<string, string>`; `Feed` props `roomId: string`, `canReact: boolean`, `memberNames?: Record<string, string>`.

- [ ] **Step 1: The component**

Create `mux/web/src/components/feed/Reactions.tsx`:

```tsx
'use client';

import React from 'react';
import { SmilePlus } from 'lucide-react';
import { api } from '@/lib/api';
import { EmojiButton } from './EmojiPicker';

const QUICK = ['👍', '❤️', '😂', '🎉', '👀'];

interface ReactionsProps {
  roomId: string;
  messageId: string;
  reactions?: Record<string, string[]>;
  currentUserId: string;
  canReact: boolean;
  names?: Record<string, string>;
}

// The new state arrives as message.reacted over the room socket, so nothing is updated here
export function ReactionChips({ roomId, messageId, reactions, currentUserId, canReact, names = {} }: ReactionsProps) {
  const entries = Object.entries(reactions ?? {});
  if (!entries.length) return null;
  return (
    <div className="reactions">
      {entries.map(([emoji, users]) => {
        const mine = users.includes(currentUserId);
        const who = users.map(u => (u === currentUserId ? 'You' : names[u] ?? 'Someone')).join(', ');
        return (
          <button
            key={emoji}
            type="button"
            className={`reaction${mine ? ' mine' : ''}`}
            title={who}
            aria-pressed={mine}
            disabled={!canReact}
            onClick={() => void toggle(roomId, messageId, emoji)}
          >
            <span>{emoji}</span> <span className="mono">{users.length}</span>
          </button>
        );
      })}
    </div>
  );
}

export function ReactionBar({ roomId, messageId }: { roomId: string; messageId: string }) {
  return (
    <div className="react-bar" role="toolbar" aria-label="React">
      {QUICK.map(emoji => (
        <button key={emoji} type="button" aria-label={`React ${emoji}`} onClick={() => void toggle(roomId, messageId, emoji)}>
          {emoji}
        </button>
      ))}
      <EmojiButton onPick={emoji => void toggle(roomId, messageId, emoji)} label="More reactions" className="">
        <SmilePlus className="h-3.5 w-3.5" aria-hidden="true" />
      </EmojiButton>
    </div>
  );
}

async function toggle(roomId: string, messageId: string, emoji: string) {
  try {
    await api.toggleReaction(roomId, messageId, emoji);
  } catch (error) {
    console.error('Failed to react:', error);
  }
}
```

- [ ] **Step 2: FeedItem**

Replace `mux/web/src/components/feed/FeedItem.tsx` with:

```tsx
'use client';

import React from 'react';
import type { Message, User } from '@/types';
import { LabelChip } from './LabelChip';
import { ReactionBar, ReactionChips } from './Reactions';
import { format } from 'date-fns';

interface FeedItemProps {
  message: Message;
  currentUser: User;
  roomId: string;
  canReact: boolean;
  memberNames?: Record<string, string>;
}

export function FeedItem({ message, currentUser, roomId, canReact, memberNames }: FeedItemProps) {
  const isAgent = message.user_id === 'agent' || message.user_id === 'mux';
  // The room's own agents post replies, reviews and task summaries; nothing labels those
  const isBot = isAgent || message.user_id === 'coordinator' || message.user_id === 'coder';
  const isNote = message.to === 'team';
  // Only people's messages have server ids that reactions can point at
  const reactable = !isBot;

  return (
    <div className={`msg ${isNote ? 'note' : ''}${reactable && canReact ? ' reactable' : ''}`}>
      <span
        className={`av ${isAgent ? 'agent-av' : ''}`}
        style={{ background: isAgent ? undefined : message.user.color }}
      >
        {isAgent ? 'M' : message.user.initials}
      </span>
      <div>
        <div className="who">
          {isAgent ? 'MUX' : message.user.name}{' '}
          <time className="mono">{format(new Date(message.created_at), 'HH:mm')}</time>
          {isNote ? (
            <span className="note-tag mono">team</span>
          ) : message.label ? (
            <LabelChip label={message.label} />
          ) : (
            !isBot && <span className="chip pending">waiting for coordinator</span>
          )}
        </div>
        <p className="whitespace-pre-wrap">{message.text}</p>
        {reactable && (
          <ReactionChips roomId={roomId} messageId={message.id} reactions={message.reactions}
            currentUserId={currentUser.id} canReact={canReact} names={memberNames} />
        )}
      </div>
      {reactable && canReact && <ReactionBar roomId={roomId} messageId={message.id} />}
    </div>
  );
}
```

- [ ] **Step 3: Feed and page**

`mux/web/src/components/feed/Feed.tsx`: add `roomId: string; canReact: boolean; memberNames?: Record<string, string>;` to `FeedProps`, destructure them in `Feed`, and pass `roomId={roomId} canReact={canReact} memberNames={memberNames}` to both `<FeedItem …/>` uses.

`mux/web/src/app/room/[roomId]/page.tsx`, on `<Feed …>`:

```tsx
            roomId={roomId}
            canReact={currentUserMembership.permission !== 'viewer'}
            memberNames={Object.fromEntries(room.members.map(m => [m.user_id, m.user.name]))}
```

- [ ] **Step 4: Styles**

In `globals.css`, after the `.note-tag` rule:

```css
.msg { position: relative; }
.reactions { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.reaction { display: inline-flex; align-items: center; gap: 4px; padding: 2px 7px; border-radius: 999px; border: 1px solid var(--line); background: var(--raised); font-size: 12px; color: var(--ink); }
.reaction .mono { font-size: 11px; color: var(--muted); }
.reaction.mine { border-color: var(--coord); background: color-mix(in srgb, var(--coord) 16%, transparent); }
.reaction:disabled { cursor: default; }
.react-bar { position: absolute; top: -12px; right: 4px; display: none; gap: 2px; padding: 2px 4px; border-radius: 8px; border: 1px solid var(--line); background: var(--panel); box-shadow: 0 4px 12px rgb(0 0 0 / .3); }
.react-bar button { padding: 2px 4px; border-radius: 4px; font-size: 14px; line-height: 1; color: var(--muted); }
.react-bar button:hover { background: var(--raised); }
.msg.reactable:hover .react-bar, .msg.reactable:focus-within .react-bar { display: flex; }
@media (hover: none) { .msg.reactable .react-bar { display: flex; position: static; margin-top: 4px; grid-column: 2; justify-self: start; } }
```

- [ ] **Step 5: Check**

Run: `npx tsc --noEmit && npm run lint -- --max-warnings=0`
Expected: 0 errors. In two browsers on the tunnel: hovering a person's message shows the bar; clicking 👍 shows a chip with 1 in both browsers; the second user's 👍 makes it 2; clicking your highlighted chip removes yours; reload keeps them; agent messages have no bar; a viewer sees chips but can't click.

- [ ] **Step 6: Commit**

```bash
git add mux/web/src/components/feed/Reactions.tsx mux/web/src/components/feed/FeedItem.tsx mux/web/src/components/feed/Feed.tsx mux/web/src/app/room/[roomId]/page.tsx mux/web/src/app/globals.css
git commit -m "Add emoji reactions to chat messages"
```

---

### Task 6: Photos in the Team chat

**Files:**
- Create: `mux/web/src/components/feed/MessageImage.tsx`
- Modify: `mux/web/src/components/feed/Composer.tsx`
- Modify: `mux/web/src/components/feed/FeedItem.tsx`
- Modify: `mux/web/src/components/feed/Feed.tsx`
- Modify: `mux/web/src/app/room/[roomId]/page.tsx` (`handleSendMessage` ~272)
- Modify: `mux/web/src/app/globals.css`

**Interfaces:**
- Consumes: `api.uploadImage`, `api.fetchUpload`, `api.sendMessage(…, attachments)`, `Message.attachments`, `FeedItem`'s `roomId` (Tasks 3, 5).
- Produces: `Composer` prop `onSend: (text: string, to: MessageTo, photos?: File[]) => Promise<void> | void` and `canAttach?: boolean`; `Feed`'s `onSendMessage` gets the same signature; `<MessageImage roomId uploadId />`.

- [ ] **Step 1: Sending with photos (page)**

In `mux/web/src/app/room/[roomId]/page.tsx`, replace `handleSendMessage`:

```tsx
  // Photos are uploaded first, then sent with the note; a failure is shown in the composer
  const handleSendMessage = useCallback(async (text: string, to: MessageTo, photos: File[] = []) => {
    if (!roomId || !currentUser) return;
    const ids = await Promise.all(photos.map(p => api.uploadImage(roomId, p).then(r => r.id)));
    await api.sendMessage(roomId, text, to, ids);
    // Message will appear via WebSocket
  }, [roomId, currentUser]);
```

In `Feed.tsx`, change `onSendMessage: (text: string, to: MessageTo) => void;` to `onSendMessage: (text: string, to: MessageTo, photos?: File[]) => Promise<void> | void;` and pass `canAttach` to the Team composer only: `<Composer onSend={onSendMessage} canPostTeam={canPostTeam} target="team" canAttach={canPostTeam} />`.

- [ ] **Step 2: Composer photos**

In `mux/web/src/components/feed/Composer.tsx`:

1. Imports: add `ImagePlus, X` from `lucide-react` and `useEffect` from React.
2. Props: `onSend: (text: string, to: MessageTo, photos?: File[]) => Promise<void> | void;` and `canAttach?: boolean;` (destructure `canAttach = false`).
3. State and helpers inside the component:

```tsx
  const fileRef = useRef<HTMLInputElement>(null);
  const [photos, setPhotos] = useState<{ file: File; url: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const photosRef = useRef(photos);
  photosRef.current = photos;
  useEffect(() => () => photosRef.current.forEach(p => URL.revokeObjectURL(p.url)), []);

  const addPhotos = (files: File[]) => {
    setError(null);
    const ok: File[] = [];
    for (const f of files) {
      if (!PHOTO_TYPES.includes(f.type)) { setError('Only PNG, JPEG, GIF and WebP images'); continue; }
      if (f.size > MAX_PHOTO_BYTES) { setError(`${f.name} is over 5 MB`); continue; }
      ok.push(f);
    }
    setPhotos(prev => {
      const room = MAX_PHOTOS - prev.length;
      if (ok.length > room) setError(`Up to ${MAX_PHOTOS} photos per message`);
      return [...prev, ...ok.slice(0, Math.max(room, 0)).map(file => ({ file, url: URL.createObjectURL(file) }))];
    });
  };

  const removePhoto = (url: string) => {
    URL.revokeObjectURL(url);
    setPhotos(prev => prev.filter(p => p.url !== url));
  };

  const onPaste = (e: React.ClipboardEvent) => {
    if (!canAttach) return;
    const files = Array.from(e.clipboardData.files).filter(f => f.type.startsWith('image/'));
    if (files.length) { e.preventDefault(); addPhotos(files); }
  };
```

with these constants above the component:

```tsx
const PHOTO_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];
const MAX_PHOTO_BYTES = 5 * 1024 * 1024;
const MAX_PHOTOS = 4;
```

4. Replace `handleSubmit`:

```tsx
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = text.trim();
    if ((!trimmed && !photos.length) || disabled || sending) return;
    setSending(true);
    setError(null);
    try {
      await onSend(trimmed, target, photos.map(p => p.file));
      photos.forEach(p => URL.revokeObjectURL(p.url));
      setPhotos([]);
      setText('');
      setTo('agent');
      setPicked(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not send');
    } finally {
      setSending(false);
    }
  };
```

5. JSX: add `onPaste={onPaste}` to the `<input>`; before the `<form>` render previews; inside the form, before the emoji button, the photo button and hidden input; after the form the error; and the Send button becomes `disabled={disabled || sending || (!text.trim() && !photos.length)}` with label `{sending ? 'Sending…' : 'Send'}`:

```tsx
      {photos.length > 0 && (
        <div className="photo-previews">
          {photos.map(p => (
            <div key={p.url} className="photo-preview">
              {/* eslint-disable-next-line @next/next/no-img-element -- a local object URL */}
              <img src={p.url} alt={p.file.name} />
              <button type="button" aria-label={`Remove ${p.file.name}`} onClick={() => removePhoto(p.url)}>
                <X className="h-3 w-3" aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>
      )}
```

```tsx
        {canAttach && (
          <>
            <button type="button" className="btn icon" aria-label="Add photo" title="Add photo" onClick={() => fileRef.current?.click()}>
              <ImagePlus className="h-4 w-4" aria-hidden="true" />
            </button>
            <input ref={fileRef} type="file" accept={PHOTO_TYPES.join(',')} multiple hidden
              onChange={(e) => { addPhotos(Array.from(e.target.files ?? [])); e.target.value = ''; }} />
          </>
        )}
```

```tsx
      {error && <div className="composer-error" role="alert">{error}</div>}
```

- [ ] **Step 3: Showing photos**

Create `mux/web/src/components/feed/MessageImage.tsx`:

```tsx
'use client';

import React from 'react';
import { api } from '@/lib/api';

// Fetched with the sign-in token (an <img src> to the API couldn't send it) and shown from an object URL
export function MessageImage({ roomId, uploadId }: { roomId: string; uploadId: string }) {
  const [url, setUrl] = React.useState<string | null>(null);
  const [failed, setFailed] = React.useState(false);
  const [open, setOpen] = React.useState(false);

  React.useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    api.fetchUpload(roomId, uploadId)
      .then(blob => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [roomId, uploadId]);

  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  if (failed) return <div className="msg-photo placeholder">Couldn&apos;t load image</div>;
  if (!url) return <div className="msg-photo placeholder" aria-busy="true" />;
  return (
    <>
      <button type="button" className="msg-photo" onClick={() => setOpen(true)} aria-label="Open photo">
        {/* eslint-disable-next-line @next/next/no-img-element -- an object URL from an authenticated fetch */}
        <img src={url} alt="Shared photo" />
      </button>
      {open && (
        <div className="lightbox" role="dialog" aria-label="Photo" onClick={() => setOpen(false)}>
          {/* eslint-disable-next-line @next/next/no-img-element -- same object URL */}
          <img src={url} alt="Shared photo, full size" />
        </div>
      )}
    </>
  );
}
```

In `FeedItem.tsx`: `import { MessageImage } from './MessageImage';` and replace `<p className="whitespace-pre-wrap">{message.text}</p>` with:

```tsx
        {message.text && <p className="whitespace-pre-wrap">{message.text}</p>}
        {message.attachments && message.attachments.length > 0 && (
          <div className="msg-photos">
            {message.attachments.map(id => <MessageImage key={id} roomId={roomId} uploadId={id} />)}
          </div>
        )}
```

- [ ] **Step 4: Styles**

In `globals.css`, after the reaction rules:

```css
.photo-previews { display: flex; gap: 6px; flex-wrap: wrap; }
.photo-preview { position: relative; width: 56px; height: 56px; border-radius: 6px; overflow: hidden; border: 1px solid var(--line); }
.photo-preview img { width: 100%; height: 100%; object-fit: cover; }
.photo-preview button { position: absolute; top: 2px; right: 2px; border-radius: 999px; background: rgb(0 0 0 / .6); color: #fff; padding: 2px; }
.composer-error { font-size: 11.5px; color: var(--conflict); }
.msg-photos { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
.msg-photo { display: block; max-width: 220px; border-radius: 6px; overflow: hidden; border: 1px solid var(--line); padding: 0; background: var(--raised); }
.msg-photo img { display: block; max-width: 220px; max-height: 220px; object-fit: contain; }
.msg-photo.placeholder { width: 160px; height: 100px; display: grid; place-items: center; font-size: 11px; color: var(--faint); }
.lightbox { position: fixed; inset: 0; z-index: 100; background: rgb(0 0 0 / .85); display: grid; place-items: center; padding: 16px; cursor: zoom-out; }
.lightbox img { max-width: 100%; max-height: 100%; object-fit: contain; }
```

- [ ] **Step 5: Check**

Run: `npx tsc --noEmit && npm run lint -- --max-warnings=0 && NEXT_DIST_DIR=.next-build npm run build`
Expected: 0 errors; build passes.

- [ ] **Step 6: Commit**

```bash
git add mux/web/src/components/feed/MessageImage.tsx mux/web/src/components/feed/Composer.tsx mux/web/src/components/feed/FeedItem.tsx mux/web/src/components/feed/Feed.tsx mux/web/src/app/room/[roomId]/page.tsx mux/web/src/app/globals.css
git commit -m "Share photos in the Team chat"
```

---

### Task 7: End-to-end check

- [ ] **Step 1: Everything automated**

Run (repo root): `make test-server && make typecheck-server && make check-web`
Expected: all pass.

- [ ] **Step 2: Restart for the new npm package if needed**

If the dev web app logs `Module not found: emoji-picker-react`, restart it with the same API address:
`NEXT_PUBLIC_API_URL=<tunnel API URL> make web`.

- [ ] **Step 3: Manual, two accounts over the tunnel**

1. Both users open the room. User A types `great 🎉` using the picker → both see it.
2. A reacts 👍 to B's note; B sees the chip; B adds 👍 → `2`; A un-reacts → `1`, highlighted only for B.
3. B pastes a screenshot in the Team window, adds a second photo with the button, removes one, sends → both see one thumbnail; clicking opens the lightbox; Escape closes it.
4. Try a 6 MB photo → "over 5 MB" error, nothing sent.
5. Both reload → messages, reactions and the photo are still there.
6. The Agent window has no photo button.
