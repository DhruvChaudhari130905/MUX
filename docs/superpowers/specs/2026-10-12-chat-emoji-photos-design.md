# Chat emojis, reactions and photos

**Date:** 2026-10-12 · **Status:** approved design, awaiting spec review

## Goal

Make the room's chat feel like a team chat while multi-user testing: people can type emojis, react to any
message, and share photos (screenshots, mockups) with each other in the Team window.

## Decisions

| Question | Decision |
|---|---|
| Who sees photos | People only (Team window). The agent never receives them, so any AI model works. |
| Emoji scope | A picker in the composer, plus reactions on messages. |
| Who can react / upload | Editors and owners (same rule as posting). Viewers see reactions and photos. |
| Reactable messages | People's messages (both windows). Agent messages get their ids in the browser (`ai-12`, `coder-40`), so the server can't check them; no reaction bar on those. |
| Out of scope | Photos to the agent, GIF search, deleting uploads, editing messages. |

## 1. Emoji picker (web only)

- `mux/web/src/components/feed/Composer.tsx`: a 😊 button between the input and **Send** in both windows.
- Opens `emoji-picker-react` in a popover (searchable, keyboard reachable, closes on Escape or outside click).
- The picker is loaded with `next/dynamic` on first open, so the room bundle doesn't grow.
- Picking inserts the emoji at the input's cursor position and returns focus to the input.
- A shared `EmojiPicker` wrapper (`mux/web/src/components/feed/EmojiPicker.tsx`) is reused by reactions.

## 2. Reactions

### Server

- New event `MessageReactionToggledEvent` in `mux/server/mux/events/models.py`
  (`EventType.MESSAGE_REACTION_TOGGLED`): `message_id`, `user_id`, `emoji`, `added: bool`.
- `RoomActor.toggle_reaction(message_id, user_id, emoji)`: under the actor lock, checks the id is a person's
  message in this room (`message_ids`, rebuilt on replay), computes `added` from the current reaction set (kept in memory, rebuilt on replay),
  appends the event. Never reaches the coordinator's inbox.
- `POST /rooms/{id}/messages/{message_id}/reactions` with `{"emoji": "👍"}`, `require_editor`.
  404 for an unknown message; 422 unless `emoji` is one emoji (1–16 characters, no ASCII letters, digits or
  whitespace).
- `mux/server/mux/events/wire.py`: maps it to `message.reacted` with
  `{message_id, user_id, emoji, added}`.

### Web

- `Message.reactions?: Record<string, string[]>` (emoji → user ids) in `mux/web/src/types/index.ts`.
- `mux/web/src/lib/reducer.ts`: `message.reacted` adds or removes the user id, dropping empty emojis.
  Immutable updates (no in-place mutation).
- `api.toggleReaction(roomId, messageId, emoji)` in `mux/web/src/lib/api.ts`.
- `FeedItem`: on hover/focus a bar with 👍 ❤️ 😂 🎉 👀 and **+** (opens the picker); below the message,
  one chip per emoji with its count, highlighted when you reacted, names in the `title`, click to toggle.
  Viewers see chips but no bar, and chips aren't clickable for them.

## 3. Photos (Team window only)

### Server

- `mux/server/mux/rooms/uploads.py`: `UploadStore(root=.mux/uploads/{room_id})`.
  - `save(data: bytes) -> upload_id`: detects the type from magic bytes (PNG, JPEG, GIF, WebP only;
    SVG and everything else rejected), stores `{uuid}.{ext}`.
  - `open(upload_id) -> (path, content_type) | None`: ids must match `^[0-9a-f]{32}$` (no path traversal).
- `POST /rooms/{id}/uploads` (multipart `file`, `require_editor`): 413 above 5 MB, 415 for an unsupported
  type, returns `{"id", "content_type"}`.
- `GET /rooms/{id}/uploads/{upload_id}` (room members only): `FileResponse` with the stored content type,
  `X-Content-Type-Options: nosniff`, `Cache-Control: private, max-age=86400`.
- `MessageCreateRequest`: `text` becomes optional (`max_length=10000`, default `""`), adds
  `attachments: list[str]` (max 4). 422 if both are empty, if attachments are sent with `to="agent"`, or if
  an id doesn't exist in the room's uploads.
- `UserMessageSentEvent.attachments: list[str] = []`; `actor.add_message(..., attachments=...)`;
  `wire.py` adds `attachments` to `message.posted`.
- `python-multipart` added to `mux/server/pyproject.toml` dependencies.

### Web

- `Message.attachments?: string[]`.
- `api.uploadImage(roomId, file)` (multipart, bearer token) and `sendMessage` gains `attachments`.
- `Composer` (Team target only): image button and clipboard paste. Client-side checks (type, 5 MB, max 4)
  with an inline error; thumbnails with a remove button before sending; uploads run on Send, Send is
  disabled while they run; the text box may be empty when a photo is attached.
- `mux/web/src/components/feed/MessageImage.tsx`: fetches `GET …/uploads/{id}` with the token, shows it from
  an object URL (revoked on unmount), a placeholder while loading and on error. Click opens a lightbox
  (Escape or click to close).

## Error handling

- Failed upload or reaction: the composer/feed shows the server's message; nothing is sent half-way.
- Images that fail to load show a "couldn't load image" placeholder instead of breaking the message.
- Older events (no `attachments`, no reactions) replay unchanged.

## Testing

- **Backend (pytest, fake LLM):** reaction toggle on/off, survives rehydrate, 404 unknown message, 422 bad
  emoji, viewer forbidden; upload accepts PNG/JPEG/GIF/WebP, rejects SVG/text/oversize, viewer forbidden,
  non-member can't GET, bad id 404; photo-only team message accepted, photo to agent rejected, unknown
  attachment rejected; `message.posted` carries attachments.
- **Web:** `npx tsc --noEmit`, lint, build; reducer replay script covering `message.reacted` and attachments.
- **Manual:** two accounts over the tunnel: pick emoji, react and un-react live, paste and upload a photo,
  reload and check everything is still there.
