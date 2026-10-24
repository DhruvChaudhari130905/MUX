"""The room runtime: coordinator labels, plans, votes and questions, and the coder working through tasks.

Real registry, actors and runtimes; only the model is scripted (FakeLLM).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import mux.rooms.registry as room_registry
from mux.agents.coder.tools.files import ActorFileTools, FileToolError
from mux.agents.coordinator.schema import AddPlanItem, CoordinatorAction, OpenConflict, Review
from mux.agents.llm import LLMReply, ToolCall, Usage
from mux.events.log import InMemoryEventLog
from mux.events.models import AgentNoticeEvent, BaseEvent, EventType
from mux.events.wire import to_envelope
from mux.memory.store import RoomMemoryStore
from mux.memory.task_log import Log, LogDraft
from mux.replay.fake_llm import FakeLLM
from mux.rooms.actor import RoomActor
from mux.rooms.registry import RoomRegistry
from mux.rooms.runtime import RETRY, SKIP, RoomRuntime

PLAN = [{"id": "t1", "title": "Booking page", "status": "draft"}]
MEMORY_LOG = json.dumps({"app": "Project.", "changed": [], "open_threads": [], "conventions": []})


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the actor's FileStore lives under cwd
    logs: dict[str, InMemoryEventLog] = {}
    monkeypatch.setattr(room_registry, "get_event_log", lambda rid: logs.setdefault(rid, InMemoryEventLog(rid)))
    llm = FakeLLM()
    runtimes: list[RoomRuntime] = []

    def factory(actor: RoomActor) -> RoomRuntime:
        rt = RoomRuntime(actor, llm, vote_timeout=30, question_timeout=30, max_turns=3)
        runtimes.append(rt)
        return rt

    return RoomRegistry(runtime_factory=factory), llm, logs, runtimes


async def until(check: Callable[[], Any], timeout: float = 3.0) -> None:
    """Wait for background work (event callbacks, the coder) to reach a state."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        result = check()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out waiting for the runtime")
        await asyncio.sleep(0.01)


def notices(log: InMemoryEventLog, kind: str) -> list[dict[str, Any]]:
    return [e.data for e in log._events if isinstance(e, AgentNoticeEvent) and e.kind == kind]


def of_type(log: InMemoryEventLog, t: EventType) -> list[BaseEvent]:
    return [e for e in log._events if e.type == t]


async def new_room(registry: RoomRegistry, llm: FakeLLM, logs, *, domain_role: str | None = "design",
                   plan: list[dict[str, Any]] | None = PLAN) -> RoomActor:
    actor = await registry.create_room("room_test1", "alice", name="Yoga", description="Yoga booking", domain_role=domain_role)
    if plan:
        await actor.draft_plan(plan, "alice")  # what POST /rooms does with initial_plan
    return actor


def tool_reply(*calls: tuple[str, dict[str, Any]]) -> LLMReply:
    return LLMReply(
        text="", model="fake-super", usage=Usage(10, 5), finish_reason="tool_calls",
        tool_calls=[ToolCall(f"c{i}", name, args, json.dumps(args)) for i, (name, args) in enumerate(calls)],
    )


@pytest.mark.asyncio
async def test_room_starts_with_an_empty_plan_that_fills_from_the_feed(setup):
    registry, llm, logs, runtimes = setup
    actor = await new_room(registry, llm, logs, plan=None)
    log = logs[actor.room_id]
    await runtimes[0].idle()
    # Nothing is planned from the room's description
    assert await actor.get_plan() == [] and llm.calls == []

    # What the user asks for becomes a task, and with no draft to approve the coder builds it straight away
    llm.push(
        CoordinatorAction(label="queue", rationale="new work", add_plan_item=AddPlanItem(title="Booking page")),
        tool_reply(("finish_task", {"summary": "Added the booking page"})),
    )
    await actor.add_message("chat", "build a booking page", message_id="m1", user_id="alice", enqueue=False)
    await until(lambda: of_type(log, EventType.TASK_FINISHED))
    assert [(p["id"], p["title"], p["status"]) for p in await actor.get_plan()] == [("t1", "Booking page", "done")]
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_messages_are_labeled_and_applied(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)
    log = logs[actor.room_id]

    llm.push(CoordinatorAction(label="chat", rationale="a question", reply="It uses SQLite."))
    await actor.add_message("chat", "what db do we use?", message_id="m1", user_id="alice", enqueue=False)
    await until(lambda: notices(log, "coordinator.reply"))
    assert notices(log, "message.labeled")[0] == {"message_id": "m1", "label": "chat", "rationale": "a question", "domain": None}
    reply = to_envelope(of_type(log, EventType.AGENT_NOTICE)[-1])
    assert reply is not None and reply["type"] == "coordinator.reply" and reply["payload"]["text"] == "It uses SQLite."

    llm.push(CoordinatorAction(label="queue", rationale="new work", add_plan_item=AddPlanItem(title="Pricing page")))
    await actor.add_message("chat", "add a pricing page", message_id="m2", user_id="alice", enqueue=False)
    await until(lambda: len(of_type(log, EventType.PLAN_ITEM_ADDED)) == 1)
    # The plan isn't approved yet, so the new work joins the draft and the coder doesn't start
    assert [(p["title"], p["status"]) for p in await actor.get_plan()] == [("Booking page", "draft"), ("Pricing page", "draft")]
    assert not of_type(log, EventType.TASK_STARTED)

    # With no task running there's nothing to merge into, so the change becomes a (draft) task
    llm.push(CoordinatorAction(label="merge", rationale="fits", domain="ui"))
    await actor.add_message("chat", "bigger headings", message_id="m3", user_id="alice", enqueue=False)
    await until(lambda: len(of_type(log, EventType.PLAN_ITEM_ADDED)) == 2)
    assert (await actor.get_plan())[-1]["title"] == "bigger headings"
    merges, _, interrupt = await actor.drain_inbox()
    assert merges == [] and not interrupt

    # Team notes never reach the coordinator
    calls = len(llm.calls)
    await actor.add_message("chat", "@bob login?", message_id="m4", user_id="alice", to="team", enqueue=False)
    await asyncio.sleep(0.05)
    assert len(llm.calls) == calls
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_conflict_vote_is_role_weighted(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs, domain_role="design")
    await actor.add_member("bob", "editor", granted_by="alice", domain_role="eng")
    log = logs[actor.room_id]

    llm.push(CoordinatorAction(
        label="conflict", rationale="contradicts", domain="ui",
        open_conflict=OpenConflict(with_message_ids=["m1"], summary="Dark or light theme", options=["Dark", "Light"]),
    ))
    await actor.add_message("chat", "make it dark", message_id="m1", user_id="bob", enqueue=False)
    await until(lambda: of_type(log, EventType.CONFLICT_DETECTED))
    opened = to_envelope(of_type(log, EventType.CONFLICT_DETECTED)[0])
    assert opened is not None and opened["payload"]["options"] == ["Dark", "Light"]
    cid = opened["payload"]["id"]

    # 1-1 on heads, but Design owns UI, so alice's vote counts 2
    await actor.command_vote(option_id="Light", issued_by="alice", vote_value=True, plan_item_id=cid)
    await actor.command_vote(option_id="Dark", issued_by="bob", vote_value=True, plan_item_id=cid)
    await until(lambda: of_type(log, EventType.CONFLICT_RESOLVED))
    resolved = of_type(log, EventType.CONFLICT_RESOLVED)[0]
    assert resolved.resolution == "Light"  # type: ignore[attr-defined]
    assert resolved.resolution_details["totals"] == {"Dark": 1, "Light": 2}  # type: ignore[attr-defined]
    await until(lambda: len(actor.inbox) == 1)  # the decision reaches the coder at its next turn
    merges, _, _ = await actor.drain_inbox()
    assert merges == ['The room decided "Light" on: Dark or light theme']
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_coder_builds_approved_tasks(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)
    log = logs[actor.room_id]

    llm.push(
        tool_reply(("write_file", {"path": "src/Booking.tsx", "content": "export const Booking = () => null;\n"})),
        tool_reply(("finish_task", {"summary": "Added the booking page"})),
    )
    await actor.approve_plan_items(["t1"], "alice")
    await until(lambda: of_type(log, EventType.CHECKPOINT_CREATED))

    assert await actor.get_file("src/Booking.tsx") == "export const Booking = () => null;\n"
    assert (await actor.get_plan())[0]["status"] == "done"
    # No sandbox here: the coder isn't offered builds or told to wait for a passing one
    coder_call = next(c for c in llm.calls if c.tools)
    offered = {t["function"]["name"] for t in coder_call.tools or []}
    assert "write_file" in offered and not offered & {"run_build", "run_tests"}
    assert "passing build" not in coder_call.messages[0]["content"]
    types = [env["type"] for env in map(to_envelope, log._events) if env]
    for expected in ("task.started", "tool.called", "tool.result", "file.changed", "agent.text", "task.finished", "checkpoint.created"):
        assert expected in types, types
    changed = next(e for e in map(to_envelope, log._events) if e and e["type"] == "file.changed")
    assert changed["actor"] == "coder" and changed["payload"]["version"] == 1
    await registry.shutdown_all()


async def test_token_usage_reaches_the_room_after_every_coder_turn(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    logs: dict[str, InMemoryEventLog] = {}
    monkeypatch.setattr(room_registry, "get_event_log", lambda rid: logs.setdefault(rid, InMemoryEventLog(rid)))
    llm = FakeLLM()
    sent: list[dict[str, Any]] = []

    async def publish(room_id: str, message: dict[str, Any]) -> None:
        sent.append(message)

    registry = RoomRegistry(runtime_factory=lambda actor: RoomRuntime(actor, llm, publish=publish, max_turns=3))
    actor = await new_room(registry, llm, logs)
    llm.push(
        tool_reply(("write_file", {"path": "a.txt", "content": "a\n"})),
        tool_reply(("finish_task", {"summary": "done"})),
    )
    await actor.approve_plan_items(["t1"], "alice")
    await until(lambda: of_type(logs[actor.room_id], EventType.CHECKPOINT_CREATED))

    # Each turn costs 15 tokens: the meter moves turn by turn, not in one jump when the task ends
    used = [m["payload"]["tokens_used"] for m in sent if m["type"] == "budget.updated"]
    assert len(used) >= 2 and used[-1] - used[-2] == 15
    assert (await actor.budget.get_status())["tokens_used"] == used[-1]
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_a_change_asked_for_after_the_plan_is_done_gets_built(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)
    log = logs[actor.room_id]
    llm.push(
        tool_reply(("write_file", {"path": "index.html", "content": "<h1>Yoga</h1>\n"})),
        tool_reply(("finish_task", {"summary": "Booking page"})),
    )
    await actor.approve_plan_items(["t1"], "alice")
    await until(lambda: len(of_type(log, EventType.TASK_FINISHED)) == 1)

    # The coder is idle; a change asked for in the feed becomes a task and runs straight away
    llm.push(
        CoordinatorAction(label="merge", rationale="small change to the page", domain="ui"),
        tool_reply(("edit_file", {"path": "index.html", "base_version": 1, "edits": [{"find": "<h1>", "replace": "<h1 style=\"color:blue\">"}]})),
        tool_reply(("finish_task", {"summary": "Blue heading"})),
    )
    await actor.add_message("chat", "make the heading blue", message_id="m1", user_id="alice", enqueue=False)
    await until(lambda: len(of_type(log, EventType.TASK_FINISHED)) == 2)
    plan = await actor.get_plan()
    assert [(p["title"], p["status"]) for p in plan] == [("Booking page", "done"), ("make the heading blue", "done")]
    assert "blue" in (await actor.get_file("index.html") or "")
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_stopped_task_is_parked_behind_a_question(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)
    log = logs[actor.room_id]

    # max_turns=3 and the model never calls a tool: the turn limit stops the task
    llm.push("thinking", "still thinking", "hmm")
    await actor.approve_plan_items(["t1"], "alice")
    await until(lambda: of_type(log, EventType.QUESTION_ASKED))
    asked = to_envelope(of_type(log, EventType.QUESTION_ASKED)[0])
    assert asked is not None and asked["payload"]["options"] == [RETRY, SKIP] and asked["payload"]["default_option"] == SKIP
    assert (await actor.get_plan())[0]["status"] == "skipped_question"

    # Retry puts it back in the queue and the coder runs it again
    llm.push(tool_reply(("finish_task", {"summary": "done this time"})))
    await actor.command_answer_question(asked["payload"]["id"], RETRY, "alice")
    await until(lambda: of_type(log, EventType.CHECKPOINT_CREATED))
    assert (await actor.get_plan())[0]["status"] == "done"
    await registry.shutdown_all()


async def test_run_plan_retries_every_stopped_task(setup):
    registry, llm, logs, runtimes = setup
    actor = await new_room(registry, llm, logs, plan=[
        {"id": "t1", "title": "Booking page", "status": "draft"},
        {"id": "t2", "title": "Pricing page", "status": "skipped_question", "notes": "error: 429"},
    ])
    log = logs[actor.room_id]

    # t1 stops and is parked behind "Retry it?"; t2 stopped earlier and its card is long gone
    llm.push("thinking", "still thinking", "hmm")
    await actor.approve_plan_items(["t1"], "alice")
    await until(lambda: of_type(log, EventType.QUESTION_ASKED))
    # A task waiting on the coder's own question is left for the team to answer
    await runtimes[0]._ask("Which colour?", ["Red", "Blue"], "Red", "t3", parked=False)
    await actor.add_plan_item({"id": "t3", "title": "Theme", "status": "skipped_question"}, "alice")

    llm.push(tool_reply(("finish_task", {"summary": "t1 done"})), MEMORY_LOG,
             tool_reply(("finish_task", {"summary": "t2 done"})), MEMORY_LOG)
    assert await runtimes[0].run_stopped("alice") == 2
    await until(lambda: len(of_type(log, EventType.CHECKPOINT_CREATED)) == 2)
    assert [(p["id"], p["status"]) for p in await actor.get_plan()] == [
        ("t1", "done"), ("t2", "done"), ("t3", "skipped_question")]
    # The parked card was answered, so it closes for everyone
    assert of_type(log, EventType.COMMAND_ANSWER_QUESTION)
    assert await runtimes[0].run_stopped("alice") == 0
    await registry.shutdown_all()


async def test_errors_left_after_a_task_become_a_fix_task_the_coder_runs(setup):
    registry, llm, logs, runtimes = setup
    actor = await new_room(registry, llm, logs)
    log = logs[actor.room_id]

    # The coder leaves an import of a missing file behind: finish_task is refused twice, then let through
    broken = tool_reply(("write_file", {"path": "app.js", "content": "import x from './missing.js';\n"}),
                        ("finish_task", {"summary": "done"}))
    fixed = tool_reply(("write_file", {"path": "missing.js", "content": "export default 1;\n"}),
                       ("finish_task", {"summary": "fixed the import"}))
    llm.push(broken, tool_reply(("finish_task", {"summary": "done now"})),
             tool_reply(("finish_task", {"summary": "really done"})), MEMORY_LOG, fixed)
    await actor.approve_plan_items(["t1"], "alice")
    await until(lambda: len(of_type(log, EventType.CHECKPOINT_CREATED)) == 2)
    plan = await actor.get_plan()
    assert [(p["id"], p["status"]) for p in plan] == [("t1", "done"), ("t2", "done")]
    assert plan[1]["title"] == "Fix errors in the code" and "missing.js" in plan[1]["notes"]
    assert runtimes[0]._fix_attempts == 0  # the check after the fix found nothing
    await registry.shutdown_all()


async def test_reported_errors_get_one_fix_task_at_a_time_and_stop_after_two_tries(setup):
    registry, llm, logs, runtimes = setup
    actor = await new_room(registry, llm, logs)  # a draft plan: fix tasks wait for approval, so nothing runs
    log = logs[actor.room_id]
    rt = runtimes[0]

    first = await rt.report_errors(["TypeError: x is undefined (app.js:3)"], "preview")
    assert first == "t2" and (await actor.get_plan())[1]["status"] == "draft"
    assert await rt.report_errors(["TypeError: x is undefined (app.js:3)"], "preview") is None  # one is waiting
    await actor.update_plan_item(first, {"status": "done"}, "coder")
    assert await rt.report_errors(["still broken"], "preview") == "t3"
    await actor.update_plan_item("t3", {"status": "done"}, "coder")
    # Two fixes didn't help: the room is told once, and no more tasks are added
    assert await rt.report_errors(["still broken"], "preview") is None
    assert await rt.report_errors(["still broken"], "preview") is None
    replies = notices(log, "coordinator.reply")
    assert len(replies) == 1 and "couldn't fix" in replies[0]["text"]
    assert len(await actor.get_plan()) == 3
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_coder_does_not_read_binary_files_as_text(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)
    tools = ActorFileTools(actor)

    assert await actor.save_checked("logo.png", "data:image/png;base64,iVBORw0KGgo=", None, "alice") == ("ok", 1)
    with pytest.raises(FileToolError, match="binary file"):
        await tools.read_file("logo.png")
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_coder_never_overwrites_a_locked_or_newer_file(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)
    tools = ActorFileTools(actor)

    assert (await tools.write_file("a.ts", "one"))["version"] == 1
    # A person saves v2 after the coder read v1: the coder's edit is stale, not an overwrite
    assert await actor.save_checked("a.ts", "two", 1, "alice") == ("ok", 2)
    stale = await tools.edit_file("a.ts", 1, [{"find": "one", "replace": "uno"}])
    assert stale["ok"] is False and stale["current_version"] == 2

    await actor.lock_file("a.ts", "alice")
    locked = await tools.edit_file("a.ts", 2, [{"find": "two", "replace": "dos"}])
    assert locked["ok"] is False and "locked" in locked["error"]
    assert await actor.get_file("a.ts") == "two"
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_deleting_a_file_reaches_the_web_app(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)

    assert await actor.save_checked("index.html", "<html></html>", None, "alice") == ("ok", 1)
    assert await actor.save_checked("index.html", None, 1, "alice") == ("ok", None)
    deleted = to_envelope(of_type(logs[actor.room_id], EventType.FILE_DELETED)[0])
    assert deleted is not None
    assert deleted["type"] == "file.deleted" and deleted["payload"]["path"] == "index.html"
    await registry.shutdown_all()


# --- MCP tools ------------------------------------------------------------------

@pytest.fixture
def docs_mcp(monkeypatch):
    from mcp.server.mcpserver import MCPServer

    import mux.mcp.client as mcp_client

    server = MCPServer("docs")

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    monkeypatch.setattr(mcp_client, "default_connect", lambda spec: mcp_client.open_client(spec, target=server))
    return server


ADD_TOOL = [{"name": "add", "description": "Add two numbers.", "input_schema": {"type": "object"}}]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, ok", [("Allow", True), ("Deny", False)])
async def test_coder_uses_room_mcp_tools_after_asking(setup, docs_mcp, answer, ok):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)
    log = logs[actor.room_id]
    await actor.save_mcp_server("docs", "https://93.184.216.34/mcp", {}, ADD_TOOL, {"add": {"enabled": True, "mode": "ask"}}, "alice")

    llm.push(tool_reply(("docs__add", {"a": 1, "b": 2})), tool_reply(("finish_task", {"summary": "added"})))
    await actor.approve_plan_items(["t1"], "alice")
    await until(lambda: of_type(log, EventType.QUESTION_ASKED))
    asked = to_envelope(of_type(log, EventType.QUESTION_ASKED)[0])
    assert asked is not None and asked["payload"]["options"] == ["Allow", "Deny"] and asked["payload"]["default_option"] == "Deny"
    await actor.command_answer_question(asked["payload"]["id"], answer, "alice")
    await until(lambda: of_type(log, EventType.CHECKPOINT_CREATED))

    offered = {t["function"]["name"] for t in next(c for c in llm.calls if c.tools).tools or []}
    assert "docs__add" in offered and "write_file" in offered
    result = next(d for d in notices(log, "tool.result") if d["tool"] == "docs__add")
    assert result["ok"] is ok and (result["summary"] == "3" if ok else "denied" in result["summary"])
    assert (await actor.get_plan())[0]["status"] == "done"  # the answer didn't re-queue or skip the task
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_unreachable_mcp_server_is_reported_and_removed_servers_are_gone_next_task(setup, docs_mcp, monkeypatch):
    import mux.mcp.client as mcp_client
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs, plan=[{"id": "t1", "title": "One", "status": "draft"},
                                                      {"id": "t2", "title": "Two", "status": "draft"}])
    log = logs[actor.room_id]
    await actor.save_mcp_server("docs", "https://93.184.216.34/mcp", {}, ADD_TOOL, {}, "alice")
    await actor.save_mcp_server("down", "https://93.184.216.34/down", {}, [], {}, "alice")
    real = mcp_client.default_connect

    def connect(spec):
        if spec.name == "down":
            raise ConnectionError("refused")
        return real(spec)

    monkeypatch.setattr(mcp_client, "default_connect", connect)
    llm.push(tool_reply(("finish_task", {"summary": "one"})))
    await actor.approve_plan_items(["t1"], "alice")
    await until(lambda: notices(log, "mcp.unavailable"))
    assert notices(log, "mcp.unavailable")[0] == {"server": "down", "error": "could not reach the server", "task_id": "t1"}
    await until(lambda: of_type(log, EventType.CHECKPOINT_CREATED))
    assert "docs__add" in {t["function"]["name"] for t in [c for c in llm.calls if c.tools][-1].tools or []}

    await actor.remove_mcp_server("docs", "alice")
    llm.push(tool_reply(("finish_task", {"summary": "two"})))
    await actor.approve_plan_items(["t2"], "alice")
    await until(lambda: len(of_type(log, EventType.CHECKPOINT_CREATED)) == 2)
    assert "docs__add" not in {t["function"]["name"] for t in [c for c in llm.calls if c.tools][-1].tools or []}
    await registry.shutdown_all()


# --- review requests ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_review_requests_become_read_only_tasks_that_report_in_the_feed(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)  # the plan is still a draft: reviews don't wait for approval
    log = logs[actor.room_id]
    await actor.create_file("src/App.jsx", "export default function App() { return null; }\n", "alice")

    review = ("Critical\n- none\n\nImportant\n1. src/App.jsx:1 renders nothing: the page is blank. Fix: add a heading.\n\n"
              "Minor\n- none\n\nVerdict: fix the blank page.\nFiles reviewed: 1 of 1")
    llm.push(
        CoordinatorAction(label="review", rationale="asks for a review", review=Review(focus="the whole project")),
        tool_reply(("read_file", {"path": "src/App.jsx"})),
        tool_reply(("write_file", {"path": "src/New.jsx", "content": "x"})),
        tool_reply(("finish_task", {"summary": review})),
    )
    await actor.add_message("chat", "review my project", message_id="m1", user_id="alice", enqueue=False)
    await until(lambda: notices(log, "agent.text"))

    item = next(p for p in await actor.get_plan() if p.get("kind") == "review")
    assert item["title"] == "Review: the whole project" and item["status"] == "done"
    assert (await actor.get_plan())[0]["status"] == "draft"  # the draft plan is untouched
    assert notices(log, "agent.text")[-1]["text"] == review
    assert await actor.get_file("src/New.jsx") is None  # the write was refused
    offered = {t["function"]["name"] for t in [c for c in llm.calls if c.tools][-1].tools or []}
    assert offered == {"read_file", "list_files", "web_search", "finish_task", "search_code"}
    assert not of_type(log, EventType.CHECKPOINT_CREATED)  # nothing changed, nothing to checkpoint
    await registry.shutdown_all()


# --- room AI provider -------------------------------------------------------------

class SwitchableLLM(FakeLLM):
    """FakeLLM with RoomLLM's available() switch, and an optional error for every call."""

    def __init__(self) -> None:
        super().__init__()
        self.on = False
        self.error: Exception | None = None

    def available(self) -> bool:
        return self.on

    async def chat(self, *args, **kwargs):
        if self.error is not None:
            raise self.error
        return await super().chat(*args, **kwargs)


@pytest.fixture
def switchable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    logs: dict[str, InMemoryEventLog] = {}
    monkeypatch.setattr(room_registry, "get_event_log", lambda rid: logs.setdefault(rid, InMemoryEventLog(rid)))
    llm = SwitchableLLM()

    def factory(actor: RoomActor) -> RoomRuntime:
        return RoomRuntime(actor, llm, vote_timeout=30, question_timeout=30, max_turns=3, model_error_interval=60)

    return RoomRegistry(runtime_factory=factory), llm, logs


@pytest.mark.asyncio
async def test_no_model_means_no_agent_work_until_a_key_is_saved(switchable):
    registry, llm, logs = switchable
    actor = await new_room(registry, llm, logs)
    log = logs[actor.room_id]
    await actor.approve_plan_items(["t1"], "alice")
    await actor.add_message("chat", "hello", message_id="m1", user_id="alice", enqueue=False)
    await asyncio.sleep(0.2)
    assert llm.calls == [] and not notices(log, "ai.error")
    assert (await actor.get_plan())[0]["status"] == "todo"

    llm.on = True
    llm.push(tool_reply(("finish_task", {"summary": "done"})))
    await actor.save_ai_settings("openai", "https://api.example.com/v1", "ENC", {"lightning": "a", "super": "b", "ultra": "b"}, "alice")
    await until(lambda: of_type(log, EventType.CHECKPOINT_CREATED))
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_model_errors_reach_the_room_once_a_minute(switchable):
    from mux.agents.llm import ModelError
    registry, llm, logs = switchable
    actor = await new_room(registry, llm, logs, plan=None)
    log = logs[actor.room_id]
    llm.on = True
    llm.error = ModelError("401 invalid key [hidden]")
    await actor.add_message("chat", "one", message_id="m1", user_id="alice", enqueue=False)
    await actor.add_message("chat", "two", message_id="m2", user_id="alice", enqueue=False)
    await until(lambda: notices(log, "ai.error"))
    await asyncio.sleep(0.2)
    assert notices(log, "ai.error") == [{"error": "401 invalid key [hidden]"}]
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_out_of_credit_pauses_the_coder_until_the_settings_change(switchable):
    from mux.agents.llm import ModelError
    registry, llm, logs = switchable
    actor = await new_room(registry, llm, logs, plan=[{"id": "t1", "title": "One", "status": "draft"},
                                                     {"id": "t2", "title": "Two", "status": "draft"}])
    log = logs[actor.room_id]
    llm.on = True
    llm.error = ModelError("the AI account is out of credit (402). Add funds", stuck=True)
    await actor.approve_plan_items(["t1", "t2"], "alice")
    await until(lambda: notices(log, "ai.error"))
    await asyncio.sleep(0.2)

    # One clear notice, both tasks still queued, no "Retry it?" card per task, and the coder stopped trying
    assert notices(log, "ai.error") == [{"error": "the AI account is out of credit (402). Add funds", "paused": True}]
    assert [i["status"] for i in await actor.get_plan()] == ["todo", "todo"]
    assert not of_type(log, EventType.QUESTION_ASKED)

    # A new key (or funds added, then Run plan) and the queue carries on
    llm.error = None
    llm.push(tool_reply(("finish_task", {"summary": "one"})), MEMORY_LOG,
             tool_reply(("finish_task", {"summary": "two"})), MEMORY_LOG)
    await actor.save_ai_settings("openai", "https://api.example.com/v1", "ENC", {"lightning": "a", "super": "b", "ultra": "b"}, "alice")
    await until(lambda: len(of_type(log, EventType.CHECKPOINT_CREATED)) == 2)
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_a_reply_after_a_pause_restarts_the_coder(switchable):
    from mux.agents.llm import ModelError
    registry, llm, logs = switchable
    actor = await new_room(registry, llm, logs)
    log = logs[actor.room_id]
    llm.on = True
    llm.error = ModelError("out of credit", stuck=True)
    await actor.approve_plan_items(["t1"], "alice")
    await until(lambda: notices(log, "ai.error"))

    # Funds added on the provider's site: the next message gets an answer, and the waiting task runs
    llm.error = None
    llm.push(CoordinatorAction(label="chat", rationale="hello", reply="Hi"), tool_reply(("finish_task", {"summary": "done"})))
    await actor.add_message("chat", "are you back?", message_id="m1", user_id="alice", enqueue=False)
    await until(lambda: of_type(log, EventType.CHECKPOINT_CREATED))
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_reviews_get_no_mcp_tools(setup, docs_mcp):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)
    log = logs[actor.room_id]
    await actor.save_mcp_server("docs", "https://93.184.216.34/mcp", {}, ADD_TOOL, {}, "alice")
    llm.push(
        CoordinatorAction(label="review", rationale="asks for a review", review=Review(focus="the whole project")),
        tool_reply(("finish_task", {"summary": "Critical\n- none\nImportant\n- none\nMinor\n- none\nFiles reviewed: 0 of 0"})),
    )
    await actor.add_message("chat", "review my project", message_id="m1", user_id="alice", enqueue=False)
    await until(lambda: notices(log, "agent.text"))
    offered = {t["function"]["name"] for t in [c for c in llm.calls if c.tools][-1].tools or []}
    assert "docs__add" not in offered and offered == {"read_file", "list_files", "web_search", "finish_task", "search_code"}
    await registry.shutdown_all()


# --- kickoff ----------------------------------------------------------------------

@pytest.fixture
def quick(tmp_path, monkeypatch):
    """Like `setup`, with question cards that expire after 0.3 s."""
    monkeypatch.chdir(tmp_path)
    logs: dict[str, InMemoryEventLog] = {}
    monkeypatch.setattr(room_registry, "get_event_log", lambda rid: logs.setdefault(rid, InMemoryEventLog(rid)))
    llm = FakeLLM()
    return RoomRegistry(runtime_factory=lambda a: RoomRuntime(a, llm, vote_timeout=30, question_timeout=0.3, max_turns=3)), llm, logs


def kickoff_steps(log) -> list[str]:
    return [d["text"] for d in notices(log, "kickoff.step")]


def two_questions():
    from mux.agents.coordinator.schema import KickoffQuestion, KickoffQuestions
    return KickoffQuestions(questions=[
        KickoffQuestion(question="Who is it for?", options=["Students", "Studios"], default="Studios"),
        KickoffQuestion(question="First version?", options=["Booking only", "Booking and payments"], default="Booking only"),
    ])


@pytest.mark.asyncio
async def test_kickoff_from_an_idea_asks_one_question_at_a_time_then_drafts_a_plan(setup):
    from mux.agents.coordinator.schema import PlanDraft
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs, plan=None)
    log = logs[actor.room_id]
    llm.push(two_questions())
    await actor.request_kickoff("alice")

    await until(lambda: of_type(log, EventType.QUESTION_ASKED))
    first = to_envelope(of_type(log, EventType.QUESTION_ASKED)[0])
    assert first is not None and first["payload"]["text"] == "Who is it for?"
    await asyncio.sleep(0.1)
    assert len(of_type(log, EventType.QUESTION_ASKED)) == 1  # the second waits for the first answer
    # A message during the kickoff is still labelled as usual (queued before the planner's answer, so the
    # fake model hands each call its own reply)
    llm.push(CoordinatorAction(label="chat", rationale="a question", reply="Yes."))
    await actor.add_message("chat", "is this working?", message_id="m1", user_id="alice", enqueue=False)
    await until(lambda: notices(log, "message.labeled"))
    await actor.command_answer_question(first["payload"]["id"], "Students", "alice")
    await until(lambda: len(of_type(log, EventType.QUESTION_ASKED)) == 2)
    second = to_envelope(of_type(log, EventType.QUESTION_ASKED)[1])
    assert second is not None
    llm.push(PlanDraft(tasks=[{"title": "Class schedule page"}, {"title": "Booking form"}]))  # type: ignore[list-item]
    await actor.command_answer_question(second["payload"]["id"], "Booking and payments", "bob")
    await until(lambda: any("Plan drafted" in s for s in kickoff_steps(log)))

    plan = await actor.get_plan()
    assert [(p["title"], p["status"]) for p in plan] == [("Class schedule page", "draft"), ("Booking form", "draft")]
    planner_prompt = "\n".join(m["content"] for m in llm.calls[-1].messages)
    assert "Who is it for? Students" in planner_prompt and "First version? Booking and payments" in planner_prompt
    assert kickoff_steps(log) == ["Question 1 of 2", "Question 2 of 2", "Plan drafted from your answers — approve it to start"]
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_kickoff_reads_an_imported_project_first(setup):
    from mux.agents.coordinator.schema import PlanDraft
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs, plan=None)
    log = logs[actor.room_id]
    await actor.create_file("src/App.jsx", "export default function Shop() { return null; }\n", "alice")
    llm.push(
        tool_reply(("read_file", {"path": "src/App.jsx"})),
        tool_reply(("finish_task", {"summary": "A shop app with one empty page; no cart yet."})),
        MEMORY_LOG,
        two_questions(),
        PlanDraft(tasks=[{"title": "Add a cart"}]),  # type: ignore[list-item]
    )
    await actor.request_kickoff("alice")
    await until(lambda: len(of_type(log, EventType.QUESTION_ASKED)) == 1)

    understand = next(p for p in await actor.get_plan() if p.get("kind") == "understand")
    assert understand["status"] == "done"
    coder_tools = {t["function"]["name"] for t in next(c for c in llm.calls if c.tools).tools or []}
    assert "write_file" not in coder_tools
    questions_call = next(call for call in llm.calls if call.schema and call.schema.__name__ == "KickoffQuestions")
    questions_prompt = "\n".join(m["content"] for m in questions_call.messages)
    assert "A shop app with one empty page" in questions_prompt
    assert kickoff_steps(log)[0] == "Reading your project…"
    assert not of_type(log, EventType.CHECKPOINT_CREATED)
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_unanswered_questions_take_their_default(quick):
    from mux.agents.coordinator.schema import PlanDraft
    registry, llm, logs = quick
    actor = await new_room(registry, llm, logs, plan=None)
    log = logs[actor.room_id]
    llm.push(two_questions(), PlanDraft(tasks=[{"title": "Studio dashboard"}]))  # type: ignore[list-item]
    await actor.request_kickoff("alice")
    await until(lambda: any("Plan drafted" in s for s in kickoff_steps(log)), timeout=5)
    planner_prompt = "\n".join(m["content"] for m in llm.calls[-1].messages)
    assert "Who is it for? Studios" in planner_prompt and "First version? Booking only" in planner_prompt
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_kickoff_without_usable_questions_still_plans(setup):
    from mux.agents.coordinator.schema import PlanDraft
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs, plan=None)
    log = logs[actor.room_id]
    llm.push("not json", "still not json", PlanDraft(tasks=[{"title": "Home page"}]))  # type: ignore[list-item]
    await actor.request_kickoff("alice")
    await until(lambda: any("Plan drafted" in s for s in kickoff_steps(log)))
    assert not of_type(log, EventType.QUESTION_ASKED)
    assert [p["title"] for p in await actor.get_plan()] == ["Home page"]
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_kickoff_uses_the_submitted_brief(setup):
    from mux.agents.coordinator.schema import PlanDraft
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs, plan=None)
    log = logs[actor.room_id]
    brief = "Build a shared kitchen inventory for a four-person household."
    llm.push("not json", "still not json", PlanDraft(tasks=[{"title": "Inventory overview"}]))  # type: ignore[list-item]

    await actor.request_kickoff("alice", brief)
    await until(lambda: any("Plan drafted" in step for step in kickoff_steps(log)))

    planner_prompt = "\n".join(message["content"] for message in llm.calls[-1].messages)
    assert f"App description:\n{brief}" in planner_prompt
    assert "Yoga booking" not in planner_prompt
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_a_failed_understand_task_falls_back_to_the_description(setup):
    from mux.agents.coordinator.schema import PlanDraft
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs, plan=None)
    log = logs[actor.room_id]
    await actor.create_file("a.js", "x", "alice")
    # max_turns=3 and the coder never finishes: the understand task parks
    llm.push("thinking", "still thinking", "hmm", MEMORY_LOG, "not json", "nope",
             PlanDraft(tasks=[{"title": "Home page"}]))  # type: ignore[list-item]
    await actor.request_kickoff("alice")
    await until(lambda: any("Plan drafted" in s for s in kickoff_steps(log)))
    assert "Couldn't read the project; planning from the description" in kickoff_steps(log)
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_model_errors_stop_the_kickoff_cleanly(switchable):
    from mux.agents.llm import ModelError
    registry, llm, logs = switchable
    actor = await new_room(registry, llm, logs, plan=None)
    log = logs[actor.room_id]
    llm.on = True
    llm.error = ModelError("429 rate limited")
    await actor.request_kickoff("alice")
    await until(lambda: any(s.startswith("Kickoff stopped") for s in kickoff_steps(log)))
    assert notices(log, "ai.error") == [{"error": "429 rate limited"}]
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_stopping_the_room_mid_kickoff_is_clean(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs, plan=None)
    log = logs[actor.room_id]
    llm.push(two_questions())
    await actor.request_kickoff("alice")
    await until(lambda: of_type(log, EventType.QUESTION_ASKED))
    await registry.shutdown_all()  # cancels the waiting kickoff without errors


# --- kickoff review fixes ------------------------------------------------------------

@pytest.mark.asyncio
async def test_kickoff_goes_on_when_the_understand_item_is_removed(setup):
    from mux.agents.coordinator.schema import PlanDraft
    registry, llm, logs, runtimes = setup
    actor = await new_room(registry, llm, logs, plan=None)
    log = logs[actor.room_id]
    await actor.create_file("a.js", "x", "alice")
    runtime = runtimes[-1]

    async def nothing_runs() -> None:
        return None
    runtime._next_task = nothing_runs  # type: ignore[method-assign]  # the coder is busy elsewhere
    llm.push("not json", "nope", PlanDraft(tasks=[{"title": "Home page"}]))  # type: ignore[list-item]
    await actor.request_kickoff("alice")
    await until(lambda: any(p.get("kind") == "understand" for p in actor.plan._items))
    await actor.replace_plan([], "alice")  # someone deletes the queued item
    await until(lambda: any("Plan drafted" in s for s in kickoff_steps(log)))
    assert "Couldn't read the project; planning from the description" in kickoff_steps(log)
    assert not runtime.kickoff_running
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_kickoff_stops_waiting_for_the_project_after_a_timeout(setup):
    from mux.agents.coordinator.schema import PlanDraft
    registry, llm, logs, runtimes = setup
    actor = await new_room(registry, llm, logs, plan=None)
    log = logs[actor.room_id]
    await actor.create_file("a.js", "x", "alice")
    runtime = runtimes[-1]
    runtime.understand_timeout = 0.2

    async def nothing_runs() -> None:
        return None
    runtime._next_task = nothing_runs  # type: ignore[method-assign]  # e.g. the budget is paused
    llm.push("not json", "nope", PlanDraft(tasks=[{"title": "Home page"}]))  # type: ignore[list-item]
    await actor.request_kickoff("alice")
    await until(lambda: any("Plan drafted" in s for s in kickoff_steps(log)))
    assert "Couldn't read the project; planning from the description" in kickoff_steps(log)
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_plan_ids_never_clash(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs, plan=None)
    await actor.add_plan_item({"id": "t1", "title": "Home page", "status": "draft"}, "coordinator")
    added = await actor.add_plan_item({"id": "t1", "title": "Contact form", "status": "draft"}, "coordinator")
    assert added["id"] == "t2"
    assert [(p["id"], p["title"]) for p in await actor.get_plan()] == [("t1", "Home page"), ("t2", "Contact form")]
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_changes_asked_during_a_read_only_task_become_tasks(setup):
    from mux.agents.coordinator.prompts import Message as CoordMessage
    registry, llm, logs, runtimes = setup
    actor = await new_room(registry, llm, logs, plan=None)
    runtime = runtimes[-1]
    runtime._current_task, runtime._current_kind = "t9", "understand"
    action = CoordinatorAction(label="merge", rationale="small change")
    await runtime._apply(action, CoordMessage("m1", "alice", "design", "make the header blue"), [])
    assert [p["title"] for p in await actor.get_plan()] == ["make the header blue"]
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_reviews_get_the_review_prompt_without_the_plan_and_more_turns(setup):
    from mux.agents.coder.tools.review import REVIEW_MAX_TURNS
    registry, llm, logs, runtimes = setup
    actor = await new_room(registry, llm, logs)  # PLAN has "Booking page"
    log = logs[actor.room_id]
    await actor.create_file("src/App.jsx", "export default 1;\n", "alice")
    llm.push(
        CoordinatorAction(label="review", rationale="asks for a review", review=Review(focus="the whole project")),
        tool_reply(("finish_task", {"summary": "Critical\n- none\nImportant\n- none\nMinor\n- none\nFiles reviewed: 0 of 1"})),
    )
    await actor.add_message("chat", "review my project", message_id="m1", user_id="alice", enqueue=False)
    await until(lambda: [c for c in llm.calls if c.tools])
    system = next(c for c in llm.calls if c.tools).messages[0]["content"]
    context = "\n".join(str(m["content"]) for m in next(c for c in llm.calls if c.tools).messages)
    assert "Critical / Important / Minor" in system and "Booking page" not in context
    assert "src/App.jsx" in context  # the files in scope are listed
    assert REVIEW_MAX_TURNS == 60
    await registry.shutdown_all()


# --- skills ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_coder_sees_and_uses_the_rooms_skills(setup, tmp_path, monkeypatch):
    import mux.skills.library as skills_library
    root = tmp_path / "skill-root"
    for name, desc in (("frontend-design", "Distinctive UIs"), ("tdd", "Tests first")):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(f"---\ndescription: {desc}\n---\nInstructions for {name}.")
    lib = skills_library.SkillLibrary([(root, "server")])
    monkeypatch.setattr(skills_library, "library", lambda: lib)

    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)
    log = logs[actor.room_id]
    await actor.set_skills(["frontend-design", "gone"], "alice")  # "gone" isn't on the server
    llm.push(tool_reply(("use_skill", {"name": "frontend-design"})), tool_reply(("finish_task", {"summary": "done"})))
    await actor.approve_plan_items(["t1"], "alice")
    await until(lambda: of_type(log, EventType.CHECKPOINT_CREATED))

    coder_call = next(c for c in llm.calls if c.tools)
    system = coder_call.messages[0]["content"]
    assert "- frontend-design: Distinctive UIs" in system and "tdd" not in system and "gone" not in system
    assert notices(log, "skill.used") == [{"name": "frontend-design"}]
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_coder_builds_in_the_sandbox_on_the_rooms_files(tmp_path, monkeypatch):
    from mux.replay.fake_sandbox import FakeSandbox
    from mux.sandbox.client import RunResult
    from mux.sandbox.runner import BUILD_CMD, Runner, wrap

    monkeypatch.chdir(tmp_path)
    logs: dict[str, InMemoryEventLog] = {}
    monkeypatch.setattr(room_registry, "get_event_log", lambda rid: logs.setdefault(rid, InMemoryEventLog(rid)))
    llm = FakeLLM()
    sandbox = FakeSandbox({wrap("build", BUILD_CMD): [RunResult(1, "src/Booking.tsx:1: bad type", None, 1.0)]})
    registry = RoomRegistry(runtime_factory=lambda actor: RoomRuntime(
        actor, llm, runner=Runner(sandbox, image="mux-sandbox"), vote_timeout=30, question_timeout=30, max_turns=3))
    actor = await new_room(registry, llm, logs)
    log = logs[actor.room_id]

    llm.push(
        tool_reply(("write_file", {"path": "src/Booking.tsx", "content": "export const Booking = 1;\n"})),
        tool_reply(("run_build", {})),
        tool_reply(("finish_task", {"summary": "Added the booking page"})),
    )
    await actor.approve_plan_items(["t1"], "alice")
    await until(lambda: of_type(log, EventType.TASK_FINISHED))

    # The coder is offered builds, and the build gets the room's current files under /app
    offered = {t["function"]["name"] for t in next(c for c in llm.calls if c.tools).tools or []}
    assert {"run_build", "run_tests"} <= offered
    assert sandbox.calls[0]["files"]["/app/src/Booking.tsx"] == b"export const Booking = 1;\n"
    assert notices(log, "build.result")[0]["errors"] == ["src/Booking.tsx:1: bad type"]
    assert (await actor.get_budget_status())["sandbox_runs_used"] == 1
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_coder_loads_and_updates_room_memory(setup):
    registry, llm, logs, runtimes = setup
    RoomMemoryStore("room_test1").save_task_log(Log(LogDraft(app="A yoga booking app.")))
    actor = await new_room(registry, llm, logs)
    room_log = logs[actor.room_id]

    llm.push(
        tool_reply(("write_file", {"path": "notes/booking.md", "content": "Booking flow notes"})),
        tool_reply(("finish_task", {"summary": "Added the booking notes"})),
        json.dumps({"app": "A yoga booking app with booking notes.",
                    "changed": ["Added booking notes", "Created notes/booking.md"],
                    "open_threads": [], "conventions": []}),
    )
    await actor.approve_plan_items(["t1"], "alice")
    await until(lambda: (Path(".mux/memory/room_test1/current.md").exists() and
                         "booking notes" in Path(".mux/memory/room_test1/current.md").read_text()))

    coder_call = next(call for call in llm.calls if call.tools)
    prompt = "\n".join(message["content"] for message in coder_call.messages)
    assert "LATEST ROOM LOG" in prompt and "A yoga booking app." in prompt
    memory_call = next(call for call in llm.calls if call.schema is not None)
    assert "write_file: notes/booking.md" in memory_call.messages[-1]["content"]
    assert "Created notes/booking.md" in Path(".mux/memory/room_test1/current.md").read_text()
    assert runtimes[0].memory.current_log is not None
    assert runtimes[0].memory.current_log.draft.app == "A yoga booking app with booking notes."
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_ending_sitting_compacts_task_logs(setup):
    registry, llm, logs, runtimes = setup
    actor = await new_room(registry, llm, logs)
    llm.push(
        tool_reply(("finish_task", {"summary": "Added the booking page"})),
        json.dumps({"app": "Booking app after task.", "changed": ["Added booking page"],
                    "open_threads": [], "conventions": []}),
        json.dumps({"app": "Compacted sitting summary.", "changed": ["Booking page is available"],
                    "open_threads": [], "conventions": []}),
    )
    await actor.approve_plan_items(["t1"], "alice")
    await until(lambda: runtimes[0].memory.current_log is not None)
    await until(lambda: len(runtimes[0].memory.task_logs) == 1)

    assert await actor.command_end_session("alice")
    await runtimes[0].idle()
    await until(lambda: runtimes[0].memory.day_log is not None)

    assert runtimes[0].memory.day_log.draft.app == "Compacted sitting summary."
    assert runtimes[0].memory.task_logs == []
    assert "Compacted sitting summary." in Path(".mux/memory/room_test1/current.md").read_text()
    await registry.shutdown_all()


# --- folder access: the coder works within the requester's folders ----------

async def backend_coder(actor: RoomActor) -> None:
    await actor.add_member("ravi", "editor", granted_by="alice")
    await actor.add_member("fatima", "editor", granted_by="alice")
    await actor.set_coder_roles({"Backend": ["server"], "Frontend": ["web"]}, "alice")
    await actor.set_member_access("ravi", "Backend", [], "alice")
    await actor.set_member_access("fatima", "Frontend", [], "alice")


@pytest.mark.asyncio
async def test_agent_edits_outside_the_requesters_folders_are_held(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)
    await backend_coder(actor)
    tools = ActorFileTools(actor, on_behalf_of=lambda: ["ravi"], task_id="t1")
    assert (await tools.write_file("server/a.py", "ok"))["version"] == 1
    held = await tools.write_file("web/App.tsx", "x")
    assert held["ok"] is True and held["held"] is True and "Do not retry" in held["message"]
    assert await actor.file_version("web/App.tsx") is None
    request = actor.change_requests[held["request_id"]]
    assert (request.via, request.requested_by, request.task_id) == ("agent", "ravi", "t1")
    assert (await tools.delete_file("server/a.py", 1))["deleted"] is True
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_agent_without_a_single_requester_is_unrestricted(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)
    await backend_coder(actor)
    assert (await ActorFileTools(actor, on_behalf_of=lambda: []).write_file("web/App.tsx", "x"))["ok"] is True
    assert (await ActorFileTools(actor).write_file("README.md", "x"))["ok"] is True
    assert actor.pending_changes() == []
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_a_task_from_one_person_holds_the_agents_edits_outside_their_folders(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)
    await backend_coder(actor)
    log = logs[actor.room_id]
    llm.push(tool_reply(("finish_task", {"summary": "Booking page"})))
    await actor.approve_plan_items(["t1"], "alice")
    await until(lambda: len(of_type(log, EventType.TASK_FINISHED)) == 1)

    llm.push(
        CoordinatorAction(label="merge", rationale="small change", domain="ui"),
        tool_reply(("write_file", {"path": "server/api.py", "content": "ok\n"}),
                   ("write_file", {"path": "web/App.tsx", "content": "<h1/>\n"})),
        tool_reply(("finish_task", {"summary": "Login button"})),
    )
    await actor.add_message("chat", "fix the login button", message_id="m1", user_id="ravi", enqueue=False)
    await until(lambda: len(of_type(log, EventType.TASK_FINISHED)) == 2)
    task = (await actor.get_plan())[-1]
    assert task["requested_by"] == ["ravi"] and task["status"] == "done"
    assert await actor.get_file("server/api.py") == "ok\n" and await actor.file_version("web/App.tsx") is None
    [request] = actor.pending_changes()
    assert (request.path, request.via, request.requested_by, request.task_id) == ("web/App.tsx", "agent", "ravi", task["id"])
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_queued_work_records_who_asked(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)
    await backend_coder(actor)
    log = logs[actor.room_id]
    llm.push(CoordinatorAction(label="queue", rationale="new work", add_plan_item=AddPlanItem(title="Pricing API")))
    await actor.add_message("chat", "add a pricing api", message_id="m1", user_id="ravi", enqueue=False)
    await until(lambda: len(of_type(log, EventType.PLAN_ITEM_ADDED)) == 1)
    assert (await actor.get_plan())[-1]["requested_by"] == ["ravi"]
    assert (await actor.get_plan())[0].get("requested_by") is None  # the owner's plan
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_people_merging_in_add_their_folders_as_limits(setup):
    from mux.agents.coordinator.prompts import Message
    registry, llm, logs, runtimes = setup
    actor = await new_room(registry, llm, logs)
    await backend_coder(actor)
    rt = runtimes[0]
    rt._current_task, rt._current_kind, rt._task_requesters = "t1", None, set()  # a task the room agreed on
    merge = CoordinatorAction(label="merge", rationale="fits", domain="ui")
    await rt._apply(merge, Message("m1", "ravi", None, "also change web/app.ts"), await actor.get_plan())
    assert rt._task_requesters == {"ravi"}
    await rt._apply(merge, Message("m2", "fatima", None, "make it red"), await actor.get_plan())
    assert rt._task_requesters == {"ravi", "fatima"}
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_agent_writes_must_suit_every_requester(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)
    await backend_coder(actor)
    tools = ActorFileTools(actor, on_behalf_of=lambda: ["ravi", "fatima"], task_id="t1")
    server, web = await tools.write_file("server/a.py", "x"), await tools.write_file("web/b.ts", "y")
    assert server["held"] and web["held"]
    assert {(r.path, r.requested_by) for r in actor.pending_changes()} == {("server/a.py", "fatima"), ("web/b.ts", "ravi")}
    assert (await ActorFileTools(actor, on_behalf_of=lambda: ["alice"]).write_file("README.md", "ok"))["ok"]
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_repeated_agent_edits_to_one_file_build_one_request(setup):
    registry, llm, logs, _ = setup
    actor = await new_room(registry, llm, logs)
    await backend_coder(actor)
    assert await actor.save_checked("web/App.tsx", "a\nb\n", None, "fatima") == ("ok", 1)
    tools = ActorFileTools(actor, on_behalf_of=lambda: ["ravi"], task_id="t1")
    first = await tools.edit_file("web/App.tsx", 1, [{"find": "a", "replace": "A"}])
    # The agent reads its own held change back, so the next edit builds on it
    assert (await tools.read_file("web/App.tsx"))["content"].startswith("A")
    second = await tools.edit_file("web/App.tsx", 1, [{"find": "b", "replace": "B"}])
    assert first["request_id"] == second["request_id"]
    [request] = actor.pending_changes()
    assert request.content == "A\nB\n" and request.base_version == 1
    await registry.shutdown_all()


@pytest.mark.asyncio
async def test_fix_tasks_after_a_task_keep_its_requesters(setup):
    registry, llm, logs, runtimes = setup
    actor = await new_room(registry, llm, logs)
    rt = runtimes[0]
    task = await rt.report_errors(["boom"], "the code check", ["ravi"])
    assert next(i for i in await actor.get_plan() if i["id"] == task)["requested_by"] == ["ravi"]
    await registry.shutdown_all()
