"""Coder roles and folder access through the HTTP API (docs/superpowers/specs/2026-10-18-file-access-roles-design.md)."""

import mux.rooms.registry as room_registry
from tests.test_api import auth, client, create_room, registry_call  # noqa: F401 (client is a fixture)


def setup_room(client):
    rid = create_room(client)  # owner alice
    for uid in ("ravi", "fatima"):
        client.post(f"/rooms/{rid}/members", json={"user_id": uid}, headers=auth("alice"))
    r = client.put(f"/rooms/{rid}/access/roles",
                   json={"roles": {"Backend engineer": ["server/"], "Frontend developer": ["web"]}}, headers=auth("alice"))
    assert r.status_code == 200, r.text
    for uid, role in (("ravi", "Backend engineer"), ("fatima", "Frontend developer")):
        r = client.put(f"/rooms/{rid}/members/{uid}/access", json={"coder_role": role, "folders": []}, headers=auth("alice"))
        assert r.status_code == 200, r.text
    return rid


def member(client, rid, uid):
    return next(m for m in client.get(f"/rooms/{rid}", headers=auth("alice")).json()["members"] if m["user_id"] == uid)


def restart(client, rid):
    import mux.main as main_module
    registry_call(client, room_registry.get_registry().stop_room, rid)
    main_module._room_event_logs.clear()


# --- roles and member access ------------------------------------------------

def test_owner_sets_roles_and_member_access(client):
    rid = setup_room(client)
    room = client.get(f"/rooms/{rid}", headers=auth("alice")).json()
    assert room["coder_roles"] == {"Backend engineer": ["server"], "Frontend developer": ["web"]}
    ravi = member(client, rid, "ravi")
    assert ravi["coder_role"] == "Backend engineer" and ravi["folders"] == []
    r = client.put(f"/rooms/{rid}/members/ravi/access", json={"coder_role": "Backend engineer", "folders": ["./docs/api/"]},
                   headers=auth("alice"))
    assert r.status_code == 200
    assert member(client, rid, "ravi")["folders"] == ["docs/api"]


def test_only_the_owner_manages_access(client):
    rid = setup_room(client)
    assert client.put(f"/rooms/{rid}/access/roles", json={"roles": {}}, headers=auth("ravi")).status_code == 403
    assert client.put(f"/rooms/{rid}/members/ravi/access", json={"coder_role": None, "folders": ["web"]},
                      headers=auth("ravi")).status_code == 403


def test_unknown_role_or_bad_folder_is_refused(client):
    rid = setup_room(client)
    assert client.put(f"/rooms/{rid}/members/ravi/access", json={"coder_role": "Nope", "folders": []},
                      headers=auth("alice")).status_code == 400
    assert client.put(f"/rooms/{rid}/members/mallory/access", json={"coder_role": None, "folders": ["web"]},
                      headers=auth("alice")).status_code == 400
    assert client.put(f"/rooms/{rid}/access/roles", json={"roles": {"X": ["../etc"]}}, headers=auth("alice")).status_code == 400
    assert client.put(f"/rooms/{rid}/access/roles", json={"roles": {"  ": ["web"]}}, headers=auth("alice")).status_code == 400


def test_removing_a_role_clears_it_from_members(client):
    rid = setup_room(client)
    client.put(f"/rooms/{rid}/access/roles", json={"roles": {"Frontend developer": ["web"]}}, headers=auth("alice"))
    assert member(client, rid, "ravi")["coder_role"] is None
    assert member(client, rid, "fatima")["coder_role"] == "Frontend developer"


def test_access_survives_a_reload(client):
    rid = setup_room(client)
    restart(client, rid)
    room = client.get(f"/rooms/{rid}", headers=auth("alice")).json()
    assert room["coder_roles"] == {"Backend engineer": ["server"], "Frontend developer": ["web"]}
    assert member(client, rid, "ravi")["coder_role"] == "Backend engineer"


def test_access_changes_reach_the_web_app(client):
    rid = setup_room(client)
    from tests.test_api import token
    with client.websocket_connect(f"/rooms/{rid}/ws?since=0") as ws:
        ws.send_json({"type": "auth", "payload": {"token": token("alice")}})
        dump = ws.receive_json()
    roles = [e["payload"] for e in dump if e["type"] == "access.roles_changed"]
    assert roles and roles[-1]["coder_roles"]["Frontend developer"] == ["web"]
    members = [e["payload"] for e in dump if e["type"] == "access.member_changed"]
    assert {"user_id": "fatima", "coder_role": "Frontend developer", "folders": []} in members


# --- changes outside your folders become requests ---------------------------

def save(client, rid, who, path, content, base):
    return client.put(f"/rooms/{rid}/files", json={"path": path, "content": content, "base_version": base}, headers=auth(who))


def content_of(client, rid, path):
    r = client.get(f"/api/files/{rid}/files/{path}", headers=auth("alice"))
    return r.json()["content"] if r.status_code == 200 else None


def pending(client, rid):
    return [q for q in client.get(f"/rooms/{rid}", headers=auth("alice")).json()["change_requests"] if q["status"] == "pending"]


def decide(client, rid, req, action, who):
    return client.post(f"/rooms/{rid}/change-requests/{req}/{action}", headers=auth(who))


def test_changes_inside_your_folders_save_normally(client):
    rid = setup_room(client)
    r = save(client, rid, "ravi", "server/app.py", "x", 0)
    assert r.status_code == 200 and r.json()["version"] == 1 and r.json()["held"] is False


def test_changes_outside_your_folders_become_requests(client):
    rid = setup_room(client)
    r = save(client, rid, "ravi", "web/App.tsx", "hi", 0)
    assert r.status_code == 202 and r.json()["held"] is True and r.json()["request_id"]
    assert content_of(client, rid, "web/App.tsx") is None
    reqs = pending(client, rid)
    assert [q["path"] for q in reqs] == ["web/App.tsx"]
    assert reqs[0]["approvers"] == ["alice", "fatima"] and reqs[0]["requested_by"] == "ravi" and reqs[0]["via"] == "person"


def test_first_approval_from_owner_or_area_coder_applies_it(client):
    rid = setup_room(client)
    req = save(client, rid, "ravi", "web/App.tsx", "hi", 0).json()["request_id"]
    assert decide(client, rid, req, "approve", "ravi").status_code == 403
    r = decide(client, rid, req, "approve", "fatima")
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["decided_by"] == "fatima"
    assert content_of(client, rid, "web/App.tsx") == "hi"
    assert decide(client, rid, req, "approve", "alice").status_code == 403  # no longer pending
    assert pending(client, rid) == []


def test_approval_is_for_that_change_only(client):
    rid = setup_room(client)
    req = save(client, rid, "ravi", "web/App.tsx", "hi", 0).json()["request_id"]
    decide(client, rid, req, "approve", "alice")
    assert save(client, rid, "ravi", "web/App.tsx", "again", 1).status_code == 202


def test_rejecting_or_withdrawing_discards_it(client):
    rid = setup_room(client)
    a = save(client, rid, "ravi", "web/a.ts", "a", 0).json()["request_id"]
    b = save(client, rid, "ravi", "web/b.ts", "b", 0).json()["request_id"]
    assert decide(client, rid, a, "reject", "fatima").json()["status"] == "rejected"
    assert decide(client, rid, b, "withdraw", "fatima").status_code == 403
    assert decide(client, rid, b, "withdraw", "ravi").json()["status"] == "withdrawn"
    assert pending(client, rid) == []
    assert content_of(client, rid, "web/a.ts") is None and content_of(client, rid, "web/b.ts") is None
    assert decide(client, rid, "nope", "approve", "alice").status_code == 404


def test_unassigned_files_need_the_owner(client):
    rid = setup_room(client)
    req = save(client, rid, "ravi", "README.md", "x", 0).json()["request_id"]
    assert pending(client, rid)[0]["approvers"] == ["alice"]
    assert decide(client, rid, req, "approve", "fatima").status_code == 403
    assert decide(client, rid, req, "approve", "alice").status_code == 200


def test_approving_a_request_for_a_file_that_moved_on_marks_it_stale(client):
    rid = setup_room(client)
    save(client, rid, "fatima", "web/App.tsx", "v1", 0)
    req = save(client, rid, "ravi", "web/App.tsx", "ravi's", 1).json()["request_id"]
    save(client, rid, "fatima", "web/App.tsx", "v2", 1)
    r = decide(client, rid, req, "approve", "alice")
    assert r.status_code == 200 and r.json()["status"] == "stale"
    assert content_of(client, rid, "web/App.tsx") == "v2"


def test_stale_edits_are_refused_not_turned_into_requests(client):
    rid = setup_room(client)
    save(client, rid, "fatima", "web/App.tsx", "v1", 0)
    assert save(client, rid, "ravi", "web/App.tsx", "x", 0).status_code == 409
    assert pending(client, rid) == []


def test_deletes_and_side_doors_are_guarded(client):
    rid = setup_room(client)
    save(client, rid, "fatima", "web/App.tsx", "v1", 0)
    r = client.delete(f"/rooms/{rid}/files", params={"path": "web/App.tsx"}, headers=auth("ravi"))
    assert r.status_code == 202 and r.json()["held"] is True
    assert content_of(client, rid, "web/App.tsx") == "v1"
    req = r.json()["request_id"]
    assert decide(client, rid, req, "approve", "fatima").json()["status"] == "approved"
    assert content_of(client, rid, "web/App.tsx") is None

    assert client.put(f"/api/files/{rid}/files/web/x.ts", json={"path": "web/x.ts", "content": "x"}, headers=auth("ravi")).status_code == 403
    assert client.post(f"/api/files/{rid}/files", json={"path": "web/y.ts", "content": "y"}, headers=auth("ravi")).status_code == 403
    assert client.post(f"/rooms/{rid}/rewind", json={"checkpoint_id": "any"}, headers=auth("ravi")).status_code == 403


def test_unrestricted_editors_and_the_owner_save_anywhere(client):
    rid = setup_room(client)
    client.post(f"/rooms/{rid}/members", json={"user_id": "bob"}, headers=auth("alice"))
    assert save(client, rid, "bob", "web/App.tsx", "b", 0).status_code == 200
    assert save(client, rid, "alice", "README.md", "a", 0).status_code == 200


def test_viewers_still_cannot_save(client):
    rid = setup_room(client)
    client.post(f"/rooms/{rid}/members", json={"user_id": "vic", "role": "viewer"}, headers=auth("alice"))
    assert save(client, rid, "vic", "web/x.ts", "x", 0).status_code == 403
    assert pending(client, rid) == []


def test_pending_requests_survive_a_reload(client):
    rid = setup_room(client)
    req = save(client, rid, "ravi", "web/App.tsx", "hi", 0).json()["request_id"]
    restart(client, rid)
    assert [q["id"] for q in pending(client, rid)] == [req]
    assert decide(client, rid, req, "approve", "fatima").status_code == 200
    restart(client, rid)
    assert pending(client, rid) == [] and content_of(client, rid, "web/App.tsx") == "hi"


def test_requests_reach_the_web_app(client):
    rid = setup_room(client)
    req = save(client, rid, "ravi", "web/App.tsx", "hi", 0).json()["request_id"]
    decide(client, rid, req, "reject", "alice")
    from tests.test_api import token
    with client.websocket_connect(f"/rooms/{rid}/ws?since=0") as ws:
        ws.send_json({"type": "auth", "payload": {"token": token("alice")}})
        dump = ws.receive_json()
    asked = [e["payload"] for e in dump if e["type"] == "change.requested"]
    assert asked and asked[0]["id"] == req and asked[0]["content"] == "hi" and asked[0]["approvers"] == ["alice", "fatima"]
    assert {"id": req, "status": "rejected", "decided_by": "alice"} in [e["payload"] for e in dump if e["type"] == "change.decided"]


# --- review fixes: side doors ------------------------------------------------

def test_dot_dot_paths_are_refused(client):
    rid = setup_room(client)
    save(client, rid, "fatima", "web/app.ts", "v1", 0)
    assert save(client, rid, "ravi", "server/../web/app.ts", "PWNED", 1).status_code == 400
    assert client.delete(f"/rooms/{rid}/files", params={"path": "server/../web/app.ts"}, headers=auth("ravi")).status_code == 400
    assert client.put(f"/api/files/{rid}/files/server/../web/app.ts", json={"path": "x", "content": "x"},
                      headers=auth("ravi")).status_code in (400, 403)  # the client may normalise the URL
    assert client.post(f"/api/files/{rid}/files", json={"path": "server/../web/b.ts", "content": "x"},
                       headers=auth("ravi")).status_code == 400
    assert content_of(client, rid, "web/app.ts") == "v1" and pending(client, rid) == []


def plan_of(client, rid):
    room = registry_call(client, room_registry.get_registry().get_room, rid)
    return {i["id"]: i for i in registry_call(client, room.get_plan)}


def test_plan_edits_by_a_restricted_coder_bind_tasks_to_them(client):
    rid = setup_room(client)
    items = [{"id": "t9", "title": "edit web", "status": "draft", "requested_by": ["alice"]}]
    assert client.patch(f"/rooms/{rid}/plan", json={"items": items}, headers=auth("ravi")).status_code == 200
    assert plan_of(client, rid)["t9"]["requested_by"] == ["ravi"]
    # The owner renaming it keeps who it is for; a client can't rebind it
    items = [{"id": "t9", "title": "edit the web app", "status": "draft"}]
    client.patch(f"/rooms/{rid}/plan", json={"items": items}, headers=auth("alice"))
    assert plan_of(client, rid)["t9"]["requested_by"] == ["ravi"]
    # Commands: an add and an update by a restricted coder bind too, whatever requested_by they send
    edits = [{"type": "add", "item": {"id": "t10", "title": "more web", "status": "draft", "requested_by": []}},
             {"type": "update", "item_id": "t9", "changes": {"title": "x", "requested_by": ["alice"]}}]
    r = client.post(f"/api/commands/{rid}/edit-plan", json={"edits": edits}, headers=auth("fatima"))
    assert r.status_code == 200, r.text
    plan = plan_of(client, rid)
    assert plan["t10"]["requested_by"] == ["fatima"] and plan["t9"]["requested_by"] == ["fatima", "ravi"]


def test_plan_items_carry_requested_by_to_the_web_app():
    from mux.events.wire import plan_item_view
    assert plan_item_view({"id": "t1", "title": "x", "status": "todo", "requested_by": ["ravi"]})["requested_by"] == ["ravi"]


def test_renaming_a_role_keeps_its_members(client):
    rid = setup_room(client)
    r = client.put(f"/rooms/{rid}/access/roles", headers=auth("alice"), json={
        "roles": {"Backend dev": ["server"], "Frontend developer": ["web"]}, "renames": {"Backend engineer": "Backend dev"}})
    assert r.status_code == 200, r.text
    assert member(client, rid, "ravi")["coder_role"] == "Backend dev"
    assert save(client, rid, "ravi", "web/x.ts", "x", 0).status_code == 202



def test_rejected_and_stale_requests_stay_listed_so_the_requester_keeps_their_edit(client):
    rid = setup_room(client)
    a = save(client, rid, "ravi", "web/a.ts", "ravi's a", 0).json()["request_id"]
    decide(client, rid, a, "reject", "fatima")
    b = save(client, rid, "ravi", "web/b.ts", "ok", 0).json()["request_id"]
    decide(client, rid, b, "approve", "fatima")
    listed = {q["id"]: q for q in client.get(f"/rooms/{rid}", headers=auth("ravi")).json()["change_requests"]}
    assert listed[a]["status"] == "rejected" and listed[a]["content"] == "ravi's a"
    assert b not in listed  # approved changes are in the files now
