# Coder Roles and Folder Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Room owners give coders a role with folders (plus per-person folders); changes outside a coder's folders — by hand or by the agent on their behalf — become change requests that the owner or a coder from that area approves.

**Architecture:** A pure rules module (`mux/rooms/file_access.py`) decides who may change a path and who may approve. The room actor keeps roles, per-member access and change requests as replayable events, and enforces the rule inside `save_checked`, the single version-checked write path that both people and the agent use. The runtime records who a task came from so agent writes are checked against that person. The web app adds an access section to the share dialog and approve/reject cards in notifications.

**Tech Stack:** Python 3 / FastAPI / pydantic / pytest (server); Next.js / React / TypeScript (web).

**Spec:** `docs/superpowers/specs/2026-10-18-file-access-roles-design.md`

## Global Constraints

- Every coder can read every file; only changes (save, create, delete) are restricted.
- First approval from the owner **or** a coder whose area contains the file applies the change; one rejection discards it.
- An approval covers that one change only; it never grants ongoing access.
- Files outside every assigned folder: only the owner can approve.
- Owner never restricted. An editor with no coder role **and** no personal folders is unrestricted (rooms that don't use this behave exactly as today).
- Viewers unchanged (still read-only, cannot send requests).
- Agent: task from one person → checked against that person; task with no single requester (kickoff plan, owner-written plan, several people merged in, vote/conflict/question resolution) → unrestricted.
- An approved request applies only if the file is still at the request's `base_version`; otherwise status `stale`, nothing written.
- Coder role names are free text typed by the owner.
- A folder covers itself and everything under it: `server` and `server/` both cover `server/mux/x.py`, not `serverless/a.py`.

## Review Focus

1. **Folder prefix matching** — `web` must not cover `webhooks/x.ts`; trailing slashes and leading `./` in owner input are normalised. Test in Task 1 (`test_folder_prefix_is_segment_aware`).
2. **Approving after the file moved on** — a teammate saved the file after the request was made; approve must not overwrite. Test in Task 3 (`test_approving_a_request_for_a_file_that_moved_on_marks_it_stale`).
3. **Rename in the web app** — rename is "save new path, then delete old"; if the save is held the old file must not be deleted. Test in Task 5 step (manual check) and server side the delete is checked on its own in Task 3.
4. **Restart / replay** — roles, member access and pending requests survive a room reload. Test in Task 2 (`test_access_survives_a_reload`) and Task 3 (`test_pending_requests_survive_a_reload`).
5. **Removing a role still in use** — deleting a role clears it from members (they become unrestricted unless they have personal folders). Test in Task 2 (`test_removing_a_role_clears_it_from_members`).

---

## File Structure

| File | Responsibility |
|---|---|
| `server/mux/rooms/file_access.py` (create) | Pure rules: areas, `can_change`, `approvers`, `can_approve`, folder normalising, `ChangeRequest` dataclass, `ChangeHeld` exception |
| `server/mux/events/models.py` (modify) | 4 new event types + models |
| `server/mux/rooms/actor.py` (modify) | State, replay, `set_coder_roles`, `set_member_access`, enforcement in `save_checked`, `decide_change_request`, `withdraw_change_request` |
| `server/mux/events/wire.py` (modify) | Wire names `access.changed`, `change.requested`, `change.decided`; membership view gains `coder_role`, `folders` |
| `server/mux/api/rooms.py` (modify) | Access + change-request routes, held save (202), checked delete, rewind guard, room view fields |
| `server/mux/api/files.py` (modify) | Legacy write routes refuse restricted changes (403) |
| `server/mux/rooms/plan.py`, `runtime.py` (modify) | `requested_by` on plan items; task requester tracking |
| `server/mux/agents/coder/tools/files.py` (modify) | `ActorFileTools(on_behalf_of=...)`; held result |
| `web/src/types/*.ts`, `lib/api.ts`, `lib/reducer.ts` (modify) | Types, calls, events |
| `web/src/components/room/ShareDialog.tsx` (modify) | Owner manages roles, folders, per-member access |
| `web/src/components/room/Notifications.tsx` (modify) | Change-request cards with diff, Approve / Reject / Withdraw |
| `web/src/components/center/FileTree.tsx`, `app/room/[roomId]/page.tsx` (modify) | Mark folders outside your area; handle held saves |
| Tests: `server/tests/test_file_access.py` (create), `server/tests/test_file_access_api.py` (create), `server/tests/test_runtime.py` (modify) | |

---

### Task 1: Access rules module

**Files:**
- Create: `server/mux/rooms/file_access.py`
- Test: `server/tests/test_file_access.py`

**Interfaces:**
- Produces:
  - `normalize_folder(folder: str) -> str` — strips whitespace, leading `./` and `/`, trailing `/`; raises `ValueError` if empty or contains `..`/empty segments (reuse `check_path`).
  - `@dataclass class AccessRules: roles: dict[str, list[str]]; member_role: dict[str, str]; member_folders: dict[str, list[str]]`
    - `area(user_id: str) -> list[str]`
    - `is_restricted(user_id: str, owner_id: str) -> bool`
    - `can_change(user_id: str, path: str, owner_id: str) -> bool`
    - `approvers(path: str, owner_id: str, editors: Iterable[str]) -> set[str]` — owner ∪ restricted editors whose area covers `path`
    - `can_approve(user_id: str, path: str, owner_id: str) -> bool`
  - `@dataclass class ChangeRequest: id: str; path: str; content: str | None; base_version: int | None; requested_by: str; via: Literal["person", "agent"]; task_id: str | None; status: Literal["pending", "approved", "rejected", "withdrawn", "stale"] = "pending"; decided_by: str | None = None; created_at: str = ""`
  - `class ChangeHeld(Exception)` with attribute `request: ChangeRequest`.

- [ ] **Step 1: Write the failing tests**

```python
from mux.rooms.file_access import AccessRules, normalize_folder

OWNER = "olivia"

def rules():
    return AccessRules(
        roles={"Backend engineer": ["server"], "Frontend developer": ["web/"]},
        member_role={"ravi": "Backend engineer", "fatima": "Frontend developer"},
        member_folders={"ravi": ["docs/api"], "sam": ["infra"]},
    )

def test_folder_prefix_is_segment_aware():
    r = rules()
    assert r.can_change("fatima", "web/src/App.tsx", OWNER)
    assert not r.can_change("fatima", "webhooks/x.ts", OWNER)
    assert normalize_folder(" ./web/ ") == "web"

def test_role_and_personal_folders_add_up():
    r = rules()
    assert r.can_change("ravi", "server/mux/main.py", OWNER)
    assert r.can_change("ravi", "docs/api/auth.md", OWNER)
    assert not r.can_change("ravi", "web/src/App.tsx", OWNER)
    assert r.can_change("sam", "infra/Dockerfile", OWNER)  # personal folders only, no role

def test_owner_and_unconfigured_editors_are_unrestricted():
    r = rules()
    assert r.can_change(OWNER, "web/x.ts", OWNER) and not r.is_restricted(OWNER, OWNER)
    assert r.can_change("newbie", "web/x.ts", OWNER) and not r.is_restricted("newbie", OWNER)

def test_unassigned_files_need_the_owner():
    r = rules()
    assert not r.can_change("ravi", "README.md", OWNER)
    assert r.approvers("README.md", OWNER, ["ravi", "fatima", "sam"]) == {OWNER}

def test_approvers_are_owner_plus_coders_of_that_area():
    r = rules()
    assert r.approvers("web/src/App.tsx", OWNER, ["ravi", "fatima", "sam"]) == {OWNER, "fatima"}
    assert r.can_approve("fatima", "web/src/App.tsx", OWNER)
    assert not r.can_approve("ravi", "web/src/App.tsx", OWNER)

def test_bad_folders_are_refused():
    import pytest
    for bad in ["", "/", "../etc", "a//b"]:
        with pytest.raises(ValueError):
            normalize_folder(bad)
```

- [ ] **Step 2: Run to verify failure** — `cd server && pytest tests/test_file_access.py -v` → FAIL (`ModuleNotFoundError: mux.rooms.file_access`).
- [ ] **Step 3: Implement** `server/mux/rooms/file_access.py` with the interfaces above. A folder `f` covers `path` when `path == f or path.startswith(f + "/")`.
- [ ] **Step 4: Run** `pytest tests/test_file_access.py -v` → all PASS.
- [ ] **Step 5: Commit** `git add server/mux/rooms/file_access.py server/tests/test_file_access.py && git commit -m "Add folder access rules for coder roles"`

---

### Task 2: Roles and member access in the room (events, actor, API, room view)

**Files:**
- Modify: `server/mux/events/models.py` (EventType enum near line 11; models near `RoomSharingUpdatedEvent` line 202; add to the `Event` union line 686)
- Modify: `server/mux/rooms/actor.py` (state near line 210; methods after `set_sharing` line 396; replay near line 1290)
- Modify: `server/mux/events/wire.py` (`membership_view` line 74; event mapping near line 117)
- Modify: `server/mux/api/rooms.py` (`room_view` line 278; new routes after `add_member` line 622)
- Test: `server/tests/test_file_access_api.py` (create; import `client`, `create_room`, `auth`, `registry_call` from `tests.test_api`)

**Interfaces:**
- Consumes: `AccessRules`, `normalize_folder` (Task 1).
- Produces:
  - Events: `CODER_ROLES_UPDATED = "coder_roles_updated"` → `CoderRolesUpdatedEvent(roles: dict[str, list[str]], updated_by: str)`; `MEMBER_ACCESS_UPDATED = "member_access_updated"` → `MemberAccessUpdatedEvent(member_id: str, coder_role: str | None, folders: list[str], updated_by: str)`.
  - `RoomActor.access: AccessRules` (replayed state).
  - `RoomActor.set_coder_roles(roles: dict[str, list[str]], user_id: str) -> None` — replaces all roles; folders normalised; members whose role disappeared get `coder_role=None` (emit one `MemberAccessUpdatedEvent` each); role names stripped, non-empty, ≤ 40 chars, else `ValueError`.
  - `RoomActor.set_member_access(member_id: str, coder_role: str | None, folders: list[str], user_id: str) -> None` — `ValueError` if `member_id` is not an editor, or `coder_role` unknown.
  - Routes (owner only, `require_owner`): `PUT /rooms/{room_id}/access/roles` body `{"roles": {name: [folders]}}`; `PUT /rooms/{room_id}/members/{member_id}/access` body `{"coder_role": str | null, "folders": [str]}`. Both return `room_view`. `ValueError` → 400.
  - Room view gains `"coder_roles": {name: [folders]}`; each membership gains `"coder_role": str | None`, `"folders": [str]`.
  - Wire: both events → `"access.changed"` with payload `{"coder_roles": ..., "members": [{"user_id", "coder_role", "folders"}]}` (full snapshot, so the web reducer just replaces).

- [ ] **Step 1: Write the failing tests**

```python
def setup_room(client):
    rid = create_room(client)  # owner alice
    for uid in ("ravi", "fatima"):
        client.post(f"/rooms/{rid}/members", json={"user_id": uid}, headers=auth("alice"))
    r = client.put(f"/rooms/{rid}/access/roles",
                   json={"roles": {"Backend engineer": ["server/"], "Frontend developer": ["web"]}}, headers=auth("alice"))
    assert r.status_code == 200, r.text
    client.put(f"/rooms/{rid}/members/ravi/access", json={"coder_role": "Backend engineer", "folders": []}, headers=auth("alice"))
    client.put(f"/rooms/{rid}/members/fatima/access", json={"coder_role": "Frontend developer", "folders": []}, headers=auth("alice"))
    return rid

def test_owner_sets_roles_and_member_access(client):
    rid = setup_room(client)
    room = client.get(f"/rooms/{rid}", headers=auth("alice")).json()
    assert room["coder_roles"] == {"Backend engineer": ["server"], "Frontend developer": ["web"]}
    ravi = next(m for m in room["members"] if m["user_id"] == "ravi")
    assert ravi["coder_role"] == "Backend engineer" and ravi["folders"] == []

def test_only_the_owner_manages_access(client):
    rid = setup_room(client)
    assert client.put(f"/rooms/{rid}/access/roles", json={"roles": {}}, headers=auth("ravi")).status_code == 403
    assert client.put(f"/rooms/{rid}/members/ravi/access", json={"coder_role": None, "folders": ["web"]},
                      headers=auth("ravi")).status_code == 403

def test_unknown_role_or_bad_folder_is_refused(client):
    rid = setup_room(client)
    assert client.put(f"/rooms/{rid}/members/ravi/access", json={"coder_role": "Nope", "folders": []},
                      headers=auth("alice")).status_code == 400
    assert client.put(f"/rooms/{rid}/access/roles", json={"roles": {"X": ["../etc"]}}, headers=auth("alice")).status_code == 400

def test_removing_a_role_clears_it_from_members(client):
    rid = setup_room(client)
    client.put(f"/rooms/{rid}/access/roles", json={"roles": {"Frontend developer": ["web"]}}, headers=auth("alice"))
    ravi = next(m for m in client.get(f"/rooms/{rid}", headers=auth("alice")).json()["members"] if m["user_id"] == "ravi")
    assert ravi["coder_role"] is None

def test_access_survives_a_reload(client):
    # Evict the actor from the registry and load it again from the event log (follow the reload pattern used
    # elsewhere in tests/test_api.py / tests/test_rewind.py), then assert the same room view fields as above.
    ...
```

The last test's body: the implementer copies the existing reload pattern from `tests/test_rewind.py` or `tests/test_api.py` (search for a test that restarts the registry) and asserts `coder_roles` and ravi's `coder_role` unchanged.

- [ ] **Step 2: Run** `pytest tests/test_file_access_api.py -v` → FAIL (404 on the new routes).
- [ ] **Step 3: Implement** events, actor state + replay branches (mirror `ROOM_SHARING_UPDATED`), actor methods (under `self._lock`, emit via `self._emit(... **self._event_fields(user_id) ...)`), wire mapping, `membership_view(..., coder_role=None, folders=())` extra keyword params, routes, room view fields.
- [ ] **Step 4: Run** `pytest tests/test_file_access_api.py tests/test_api.py -v` → all PASS.
- [ ] **Step 5: Commit** `git commit -m "Let room owners set coder roles and folder access"`

---

### Task 3: Enforce on manual changes and handle change requests

**Files:**
- Modify: `server/mux/events/models.py`, `server/mux/rooms/actor.py` (`save_checked` line 985; replay), `server/mux/events/wire.py`
- Modify: `server/mux/api/rooms.py` (`save_file` line 802, `_delete` line 827, `rewind` line 859, `room_view`; new routes)
- Modify: `server/mux/api/files.py` (create line 115, update line 238, delete line 274)
- Test: `server/tests/test_file_access_api.py`

**Interfaces:**
- Consumes: Task 1 `ChangeRequest`, `ChangeHeld`; Task 2 `RoomActor.access`, `setup_room` test helper.
- Produces:
  - Events: `CHANGE_REQUESTED = "change_requested"` → `ChangeRequestedEvent(request_id, path, content: str | None, base_version: int | None, requested_by, via, task_id: str | None)`; `CHANGE_REQUEST_DECIDED = "change_request_decided"` → `ChangeRequestDecidedEvent(request_id, status, decided_by)`.
  - `RoomActor.change_requests: dict[str, ChangeRequest]` (replayed).
  - `RoomActor.save_checked(path, content, base_version, user_id, on_behalf_of: str | None = None, task_id: str | None = None)` — after the existing clash / lock / stale checks pass, compute `subject = on_behalf_of if user_id == CODER else user_id`; if `subject` is set and `not self.access.can_change(subject, path, self.owner_id)`, record a `ChangeRequestedEvent` (`via="agent"` when `user_id == CODER`) and raise `ChangeHeld(request)`. `user_id == CODER` with `on_behalf_of=None` is unrestricted. Return type unchanged.
  - `RoomActor.can_change(user_id: str, path: str) -> bool` (thin wrapper used by API guards).
  - `RoomActor.decide_change_request(request_id: str, user_id: str, approve: bool) -> ChangeRequest` — `KeyError` if unknown; `PermissionError` if not pending-and-approvable by `user_id` (`access.can_approve`); on approve, if the live version ≠ `base_version` → status `stale`, else apply via the existing unlocked create/update/delete helpers with `user_id=requested_by` and status `approved`; on reject → `rejected`. Emits `ChangeRequestDecidedEvent`.
  - `RoomActor.withdraw_change_request(request_id: str, user_id: str) -> ChangeRequest` — requester only, pending only, else `PermissionError`.
  - Routes: `POST /rooms/{room_id}/change-requests/{request_id}/approve`, `/reject` (require_editor; `PermissionError` → 403, `KeyError` → 404), `/withdraw` (require_editor). Each returns the request view.
  - `PUT /rooms/{room_id}/files` on `ChangeHeld` → **202** `{"accepted": true, "held": true, "request_id": str, "seq": int}` (extend `FileSaved` with `held: bool = False`, `request_id: str | None = None`, `version: int | None`).
  - `DELETE /rooms/{room_id}/files...` goes through `actor.save_checked(path, None, current_version, user_id)` so deletes are held too (202 same shape).
  - `/api/files` create/update/delete: `if not await actor.can_change(user, path)` → 403 `"Outside your folders: save it from the room's editor to send it for approval"`.
  - `POST /rooms/{room_id}/rewind`: 403 for a restricted editor (owner and unrestricted editors unchanged).
  - Room view gains `"change_requests": [view of each pending request]`; view = `{"id","path","content","base_version","requested_by","via","task_id","status","decided_by","approvers": sorted(list)}`.
  - Wire: `CHANGE_REQUESTED` → `"change.requested"` (request view), `CHANGE_REQUEST_DECIDED` → `"change.decided"` `{"id","status","decided_by"}`.

- [ ] **Step 1: Write the failing tests** (append to `test_file_access_api.py`)

```python
def save(client, rid, who, path, content, base):
    return client.put(f"/rooms/{rid}/files", json={"path": path, "content": content, "base_version": base}, headers=auth(who))

def test_changes_inside_your_folders_save_normally(client):
    rid = setup_room(client)
    assert save(client, rid, "ravi", "server/app.py", "x", 0).status_code == 200

def test_changes_outside_your_folders_become_requests(client):
    rid = setup_room(client)
    r = save(client, rid, "ravi", "web/App.tsx", "hi", 0)
    assert r.status_code == 202 and r.json()["held"] is True
    assert client.get(f"/api/files/{rid}/files/web/App.tsx", headers=auth("alice")).status_code == 404
    reqs = client.get(f"/rooms/{rid}", headers=auth("alice")).json()["change_requests"]
    assert [q["path"] for q in reqs] == ["web/App.tsx"] and reqs[0]["approvers"] == ["alice", "fatima"]

def test_first_approval_from_owner_or_area_coder_applies_it(client):
    rid = setup_room(client)
    req = save(client, rid, "ravi", "web/App.tsx", "hi", 0).json()["request_id"]
    assert client.post(f"/rooms/{rid}/change-requests/{req}/approve", headers=auth("ravi")).status_code == 403
    assert client.post(f"/rooms/{rid}/change-requests/{req}/approve", headers=auth("fatima")).status_code == 200
    assert client.get(f"/api/files/{rid}/files/web/App.tsx", headers=auth("alice")).json()["content"] == "hi"
    assert client.post(f"/rooms/{rid}/change-requests/{req}/approve", headers=auth("alice")).status_code == 403  # no longer pending

def test_approval_is_for_that_change_only(client):
    rid = setup_room(client)
    req = save(client, rid, "ravi", "web/App.tsx", "hi", 0).json()["request_id"]
    client.post(f"/rooms/{rid}/change-requests/{req}/approve", headers=auth("alice"))
    assert save(client, rid, "ravi", "web/App.tsx", "again", 1).status_code == 202

def test_rejecting_or_withdrawing_discards_it(client):
    rid = setup_room(client)
    a = save(client, rid, "ravi", "web/a.ts", "a", 0).json()["request_id"]
    b = save(client, rid, "ravi", "web/b.ts", "b", 0).json()["request_id"]
    assert client.post(f"/rooms/{rid}/change-requests/{a}/reject", headers=auth("fatima")).status_code == 200
    assert client.post(f"/rooms/{rid}/change-requests/{b}/withdraw", headers=auth("fatima")).status_code == 403
    assert client.post(f"/rooms/{rid}/change-requests/{b}/withdraw", headers=auth("ravi")).status_code == 200
    assert client.get(f"/rooms/{rid}", headers=auth("alice")).json()["change_requests"] == []

def test_unassigned_files_need_the_owner(client):
    rid = setup_room(client)
    req = save(client, rid, "ravi", "README.md", "x", 0).json()["request_id"]
    assert client.post(f"/rooms/{rid}/change-requests/{req}/approve", headers=auth("fatima")).status_code == 403
    assert client.post(f"/rooms/{rid}/change-requests/{req}/approve", headers=auth("alice")).status_code == 200

def test_approving_a_request_for_a_file_that_moved_on_marks_it_stale(client):
    rid = setup_room(client)
    save(client, rid, "fatima", "web/App.tsx", "v1", 0)
    req = save(client, rid, "ravi", "web/App.tsx", "ravi's", 1).json()["request_id"]
    save(client, rid, "fatima", "web/App.tsx", "v2", 1)
    r = client.post(f"/rooms/{rid}/change-requests/{req}/approve", headers=auth("alice"))
    assert r.status_code == 200 and r.json()["status"] == "stale"
    assert client.get(f"/api/files/{rid}/files/web/App.tsx", headers=auth("alice")).json()["content"] == "v2"

def test_deletes_and_side_doors_are_guarded(client):
    rid = setup_room(client)
    save(client, rid, "fatima", "web/App.tsx", "v1", 0)
    assert client.delete(f"/rooms/{rid}/files", params={"path": "web/App.tsx"}, headers=auth("ravi")).status_code == 202
    assert client.put(f"/api/files/{rid}/files/web/App.tsx", json={"content": "x"}, headers=auth("ravi")).status_code == 403
    assert client.post(f"/rooms/{rid}/rewind", json={"checkpoint_id": "any"}, headers=auth("ravi")).status_code == 403

def test_viewers_still_cannot_save(client):
    rid = setup_room(client)
    client.post(f"/rooms/{rid}/members", json={"user_id": "vic", "role": "viewer"}, headers=auth("alice"))
    assert save(client, rid, "vic", "web/x.ts", "x", 0).status_code == 403

def test_pending_requests_survive_a_reload(client):
    # Same reload pattern as test_access_survives_a_reload; assert the pending request is still listed.
    ...
```

- [ ] **Step 2: Run** `pytest tests/test_file_access_api.py -v` → new tests FAIL.
- [ ] **Step 3: Implement** the interfaces above. The rewind guard runs before the checkpoint lookup so restricted editors get 403, not 404.
- [ ] **Step 4: Run** `pytest tests/test_file_access_api.py tests/test_api.py tests/test_rewind.py tests/test_runtime.py -v` → all PASS.
- [ ] **Step 5: Commit** `git commit -m "Send changes outside a coder's folders for approval"`

---

### Task 4: The agent works within the requester's folders

**Files:**
- Modify: `server/mux/rooms/plan.py` (`_validate_item`: optional `requested_by` must be `str`), `server/mux/rooms/plan.py` `PlanItem` TypedDict (add `requested_by: Optional[str]`)
- Modify: `server/mux/rooms/runtime.py` (`_apply` line 340, `_add_task` line 370, `_run` line 509)
- Modify: `server/mux/agents/coder/tools/files.py` (`ActorFileTools` line 185)
- Test: `server/tests/test_runtime.py`

**Interfaces:**
- Consumes: Task 3 `save_checked(..., on_behalf_of=, task_id=)`, `ChangeHeld`.
- Produces:
  - `RoomRuntime._add_task(title, plan, requested_by: str | None = None)` — stores `requested_by` on the item. Pass `message.author` from the merge-with-nothing-running branch and the `queue` branch of `_apply`. Kickoff/plan drafts, reviews and owner plan edits leave it `None`.
  - `RoomRuntime._task_requester: str | None` — set from `item.get("requested_by")` at the start of `_run`; set to `None` when a message from a **different** author is merged into the running task (`_apply` merge/interrupt branch) or when a vote/conflict/question answer is enqueued (`enqueue_message(... user_id=COORDINATOR)` paths).
  - `ActorFileTools(actor, user_id=CODER, on_behalf_of: Callable[[], str | None] | None = None, task_id: str | None = None)` — a callable so a mid-task merge that clears the requester takes effect on the next write. `_save` passes `on_behalf_of()` and `task_id`; catches `ChangeHeld` and returns `("held", request.id)`.
  - `write_file` / `edit_file` / `delete_file` on outcome `"held"` return `{"ok": True, "held": True, "path": path, "request_id": id, "message": "This file is outside <requester>'s folders, so the change was sent for approval. Do not retry it or work around it; carry on with the rest of the task."}`.
  - `_run` builds `ActorFileTools(self.actor, on_behalf_of=lambda: self._task_requester, task_id=task_id)`.

- [ ] **Step 1: Write the failing tests** (in `test_runtime.py`, using `setup` / `new_room`; set access with `actor.add_member`, `actor.set_coder_roles`, `actor.set_member_access`)

```python
@pytest.mark.asyncio
async def test_agent_edits_outside_the_requesters_folders_are_held(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)
    await actor.add_member("ravi", "editor", granted_by=actor.owner_id)
    await actor.set_coder_roles({"Backend": ["server"]}, actor.owner_id)
    await actor.set_member_access("ravi", "Backend", [], actor.owner_id)
    tools = ActorFileTools(actor, on_behalf_of=lambda: "ravi", task_id="t1")
    assert (await tools.write_file("server/a.py", "ok"))["version"] == 1
    held = await tools.write_file("web/App.tsx", "x")
    assert held["held"] is True and await actor.file_version("web/App.tsx") is None
    assert actor.change_requests[held["request_id"]].via == "agent"
    await registry.shutdown_all()

@pytest.mark.asyncio
async def test_agent_without_a_single_requester_is_unrestricted(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)
    tools = ActorFileTools(actor, on_behalf_of=lambda: None)
    assert (await tools.write_file("web/App.tsx", "x"))["ok"] is True
    await registry.shutdown_all()

@pytest.mark.asyncio
async def test_task_from_one_message_records_its_requester(setup):
    # Drive a message through the coordinator with the FakeLLM labeling it "queue" (follow
    # test_messages_are_labeled_and_applied), then assert the new plan item's requested_by == the sender.
    ...

@pytest.mark.asyncio
async def test_a_second_person_merging_in_clears_the_requester(setup):
    # Start a task requested by ravi, merge a message from fatima into it (label "merge"), then assert
    # runtime._task_requester is None. Follow test_a_change_asked_for_after_the_plan_is_done_gets_built.
    ...
```

The last two bodies follow the FakeLLM scripting in the named existing tests; the assertions are given.

- [ ] **Step 2: Run** `pytest tests/test_runtime.py -k "requester or held or unrestricted" -v` → FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** `pytest tests -q` → all PASS.
- [ ] **Step 5: Commit** `git commit -m "Hold agent edits outside the requester's folders for approval"`

---

### Task 5: Web app — manage access, approve requests, held saves

**Files:**
- Modify: `web/src/types/*.ts` (`Membership`, `Room` at lines 21–43; new `ChangeRequest`)
- Modify: `web/src/lib/api.ts` (near `saveFile` line 232)
- Modify: `web/src/lib/reducer.ts` (cases near `sharing.changed` line 51)
- Modify: `web/src/components/room/ShareDialog.tsx`, `web/src/components/room/Notifications.tsx`, `web/src/components/center/FileTree.tsx`, `web/src/app/room/[roomId]/page.tsx` (save at line 319, rename at line 364)

**Interfaces:**
- Consumes: Task 2/3 room view fields, routes and wire events.
- Produces:
  - Types: `Membership.coder_role: string | null`, `Membership.folders: string[]`; `Room.coder_roles: Record<string, string[]>`; `Room.change_requests: ChangeRequest[]`; `interface ChangeRequest { id: string; path: string; content: string | null; base_version: number | null; requested_by: string; via: 'person' | 'agent'; task_id: string | null; status: 'pending' | 'approved' | 'rejected' | 'withdrawn' | 'stale'; decided_by: string | null; approvers: string[] }`.
  - `api.setCoderRoles(roomId, roles)`, `api.setMemberAccess(roomId, userId, coderRole, folders)`, `api.approveChange(roomId, id)`, `api.rejectChange(roomId, id)`, `api.withdrawChange(roomId, id)`. `api.saveFile` return type gains `held?: boolean; request_id?: string; version: number | null`.
  - Reducer: `access.changed` replaces `coder_roles` and each member's `coder_role`/`folders`; `change.requested` adds to `change_requests`; `change.decided` removes it from `change_requests`.
  - Helper `canChange(room, userId, path): boolean` in `web/src/lib/fileAccess.ts` (create) mirroring Task 1's rule — UI hints only; the server decides.

- [ ] **Step 1: ShareDialog** — owner-only "Coder roles" section: list roles, add/rename/delete, folder chips picked from the room's folder paths (derive from file paths); per editor row a role select and "Extra folders" chips. Saves via the two API calls. Non-owners see each person's role read-only.
- [ ] **Step 2: Notifications** — a card per pending request the current user can approve (`approvers.includes(me)`): requester name, "via agent" badge, path, a line diff of the current file vs `content` (fetch the current file with `api.readFile`; reuse an existing diff view if one exists in `components/`, else a simple added/removed line list), Approve / Reject. The requester sees their own pending requests with Withdraw. A `stale` decision tells the requester "The file changed before this was approved — redo your change."
- [ ] **Step 3: Held saves** — in `page.tsx` save handler: if the response has `held`, show "Sent to <approvers> for approval", and reload that file from the server (`pullServerFiles`) so the editor shows the real content. In the rename flow (line 364), delete the old path only when the save was not held.
- [ ] **Step 4: FileTree** — folders/files where `!canChange(room, me, path)` get a small lock icon and tooltip "Changes here need approval". The editor shows the same note above the file.
- [ ] **Step 5: Verify** — `cd web && npx tsc --noEmit -p .` → no output; `npm run lint` → no errors. Run the app (server + web) and, with two browser profiles (owner + coder), check: owner sets "Backend engineer" → `server`; coder saves in `web/` → request card appears for the owner; approve → file updates for both; rename a file outside your folders → old file still there.
- [ ] **Step 6: Commit** `git commit -m "Manage coder roles and approve change requests in the room"`
