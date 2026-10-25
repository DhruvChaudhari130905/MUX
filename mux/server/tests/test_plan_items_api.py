"""Tests for the targeted plan-item API: POST /plan/items, PATCH + DELETE /plan/items/{id} and
POST /plan/reorder. These send one task per call through the server's own plan, so two editors
editing at the same time can't overwrite each other (the whole-plan PATCH keeps only the last
send, which is why the web app uses these instead)."""

import json

import mux.rooms.registry as room_registry
from tests.test_api import auth, client, connect_ws, create_room, registry_call  # noqa: F401


def room_plan(c, rid):
    room = registry_call(c, room_registry.get_registry().get_room, rid)
    return registry_call(c, room.get_plan)


def join_as_editor(c, rid, user_id="bob"):
    c.post(f"/rooms/{rid}/members", json={"user_id": user_id}, headers=auth("alice"))
    c.post(f"/rooms/{rid}/join", json={"user_name": user_id.title()}, headers=auth(user_id))


def test_add_adds_one_task_without_sending_the_whole_plan(client):  # noqa: F811
    rid = create_room(client)
    r = client.post(f"/rooms/{rid}/plan/items", json={"title": "Login page"}, headers=auth("alice"))
    assert r.status_code == 200, r.text
    # New manual work stays a draft until the owner approves it (approval gating)
    plan = room_plan(client, rid)
    assert [(i["title"], i["status"]) for i in plan] == [("Login page", "draft")]


def test_add_while_a_draft_awaits_approval_joins_the_drafts(client):  # noqa: F811
    rid = create_room(client, initial_plan=[{"id": "p1", "title": "Kickoff draft", "status": "draft"}])
    r = client.post(f"/rooms/{rid}/plan/items", json={"title": "Login page"}, headers=auth("alice"))
    assert r.status_code == 200
    plan = room_plan(client, rid)
    assert [(i["title"], i["status"]) for i in plan] == [("Kickoff draft", "draft"), ("Login page", "draft")]


def test_two_editors_adding_in_quick_succession_both_land(client):  # noqa: F811
    """The bug that used to lose tasks: editor A adds while editor B's whole-plan PATCH (built
    from a snapshot without A's task) lands after — the replace kept only the last send."""
    rid = create_room(client)
    join_as_editor(client, rid)
    r1 = client.post(f"/rooms/{rid}/plan/items", json={"title": "Task from A"}, headers=auth("alice"))
    r2 = client.post(f"/rooms/{rid}/plan/items", json={"title": "Task from B"}, headers=auth("bob"))
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    titles = [i["title"] for i in room_plan(client, rid)]
    assert titles == ["Task from A", "Task from B"]


def test_rename_and_remove_touch_one_task(client):  # noqa: F811
    rid = create_room(client, initial_plan=[
        {"id": "p1", "title": "First", "status": "todo"},
        {"id": "p2", "title": "Second", "status": "todo"},
    ])
    assert client.patch(f"/rooms/{rid}/plan/items/p2", json={"title": "Second (renamed)"}, headers=auth("alice")).status_code == 200
    assert client.delete(f"/rooms/{rid}/plan/items/p1", headers=auth("alice")).status_code == 200
    plan = room_plan(client, rid)
    assert [(i["id"], i["title"]) for i in plan] == [("p2", "Second (renamed)")]


def test_missing_tasks_answer_404(client):  # noqa: F811
    rid = create_room(client)
    assert client.patch(f"/rooms/{rid}/plan/items/nope", json={"title": "x"}, headers=auth("alice")).status_code == 404
    assert client.delete(f"/rooms/{rid}/plan/items/nope", headers=auth("alice")).status_code == 404


def test_non_members_cannot_edit_tasks(client):  # noqa: F811
    rid = create_room(client, initial_plan=[{"id": "p1", "title": "First", "status": "todo"}])
    anon = "stranger"
    assert client.post(f"/rooms/{rid}/plan/items", json={"title": "x"}, headers=auth(anon)).status_code == 403
    assert client.patch(f"/rooms/{rid}/plan/items/p1", json={"title": "x"}, headers=auth(anon)).status_code == 403
    assert client.delete(f"/rooms/{rid}/plan/items/p1", headers=auth(anon)).status_code == 403
    assert client.post(f"/rooms/{rid}/plan/reorder", json={"item_ids": ["p1"]}, headers=auth(anon)).status_code == 403


def test_full_reorder_applies_the_sent_item_order(client):  # noqa: F811
    """Drag & drop happy path: the client sends the complete order of its movable tasks."""
    rid = create_room(client, initial_plan=[
        {"id": "p1", "title": "A", "status": "todo"},
        {"id": "p2", "title": "B", "status": "todo"},
        {"id": "p3", "title": "C", "status": "todo"},
    ])
    r = client.post(f"/rooms/{rid}/plan/reorder", json={"item_ids": ["p3", "p1", "p2"]}, headers=auth("alice"))
    assert r.status_code == 200, r.text
    assert [i["title"] for i in room_plan(client, rid)] == ["C", "A", "B"]


def test_reorder_from_a_stale_view_is_rejected_and_changes_nothing(client):  # noqa: F811
    """The editor's page is stale: someone else added a task after it loaded. Its reorder
    doesn't mention the new task — the server refuses (409) instead of silently deleting it
    the way a whole-plan replace used to."""
    rid = create_room(client, initial_plan=[
        {"id": "p1", "title": "A", "status": "todo"},
        {"id": "p2", "title": "B", "status": "todo"},
        {"id": "p3", "title": "C", "status": "todo"},
    ])
    join_as_editor(client, rid)
    # Someone adds a task after this editor's page loaded
    client.post(f"/rooms/{rid}/plan/items", json={"title": "Newly added"}, headers=auth("bob"))
    # The editor drags C above A: its stale view only knows p1-p3
    r = client.post(f"/rooms/{rid}/plan/reorder", json={"item_ids": ["p3", "p1", "p2"]}, headers=auth("alice"))
    assert r.status_code == 409
    assert "task list changed" in r.json()["detail"].lower()
    # Nothing was applied: every task survives, including the one the editor hadn't seen
    assert [i["title"] for i in room_plan(client, rid)] == ["A", "B", "C", "Newly added"]


def test_reorder_rejects_unknown_ids(client):  # noqa: F811
    rid = create_room(client, initial_plan=[
        {"id": "p1", "title": "A", "status": "todo"},
        {"id": "p2", "title": "B", "status": "todo"},
    ])
    r = client.post(f"/rooms/{rid}/plan/reorder", json={"item_ids": ["p2", "gone", "p1"]}, headers=auth("alice"))
    assert r.status_code == 409
    assert [i["title"] for i in room_plan(client, rid)] == ["A", "B"]


def test_item_ops_broadcast_the_item_events(client):  # noqa: F811
    """Adds, renames and removals reach the room socket as plan.item_added / plan.item_updated /
    plan.item_removed — plan_item_removed used to never exist on the wire."""
    rid = create_room(client, initial_plan=[{"id": "p1", "title": "First", "status": "todo"}])
    ws, _dump = connect_ws(client, rid)
    try:
        client.post(f"/rooms/{rid}/plan/items", json={"title": "Added"}, headers=auth("alice"))
        client.patch(f"/rooms/{rid}/plan/items/p1", json={"title": "Renamed"}, headers=auth("alice"))
        # Reorder before the delete so it actually changes something (a same-order reorder is a no-op)
        added_id = [i["id"] for i in room_plan(client, rid) if i["title"] == "Added"][0]
        client.post(f"/rooms/{rid}/plan/reorder", json={"item_ids": [added_id, "p1"]}, headers=auth("alice"))
        client.delete(f"/rooms/{rid}/plan/items/p1", headers=auth("alice"))
        seen = set()
        for _ in range(30):
            m = json.loads(ws.receive_text())
            seen.add(m["type"])
            if {"plan.item_added", "plan.item_updated", "plan.item_removed", "plan.edited"} <= seen:
                break
        assert {"plan.item_added", "plan.item_updated", "plan.item_removed", "plan.edited"} <= seen, seen
    finally:
        ws.__exit__(None, None, None)
