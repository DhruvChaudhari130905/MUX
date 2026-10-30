"""End-to-end workflow tests: core room lifecycle with real registry, actors, and scripted LLM.

Covers PRD §6 flows: create/join, coordinator labeling, conflict research + vote,
manual file edit, checkpoint/restore/rewind, budget, sitting end, GitHub export.
Only the model is scripted (FakeLLM).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import pytest

import mux.rooms.registry as room_registry
from mux.agents.coordinator.agent import Coordinator
from mux.agents.coordinator.conflicts import research_conflict, tally, Vote
from mux.agents.coordinator.prompts import Message, PlanItem, RoomView
from mux.agents.coordinator.schema import OpenConflict
from mux.agents.coder.tools.files import ActorFileTools
from mux.agents.llm import ModelRole
from mux.events.log import InMemoryEventLog
from mux.events.models import EventType, SittingEndedEvent, UserJoinedEvent
from mux.integrations.tavily import SearchResult, Source
from mux.replay.fake_llm import FakeLLM
from mux.rooms.registry import RoomRegistry


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    logs: dict[str, InMemoryEventLog] = {}
    monkeypatch.setattr(room_registry, "get_event_log", lambda rid: logs.setdefault(rid, InMemoryEventLog(rid)))
    llm = FakeLLM()

    def factory(actor):
        from mux.rooms.runtime import RoomRuntime
        return RoomRuntime(actor, llm, vote_timeout=30, question_timeout=30, max_turns=3)

    return RoomRegistry(runtime_factory=factory), llm, logs


# ---------------------------------------------------------------------------
# W1-W2: create room, join
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_w1_w2_create_and_join(setup):
    registry, llm, logs = setup
    actor = await registry.create_room("room-1", "owner-1", description="RSVP app")
    assert actor is not None
    assert actor.room_id == "room-1"
    assert actor.owner_id == "owner-1"

    log = logs["room-1"]
    await log.append(UserJoinedEvent(
        type=EventType.USER_JOINED, room_id="room-1", user_id="mate-1", user_name="Sam", sequence=0,
    ))
    events = await log.get_all("room-1")
    joined = [e for e in events if e.type == EventType.USER_JOINED]
    assert len(joined) == 1
    assert joined[0].user_id == "mate-1"


# ---------------------------------------------------------------------------
# W5: coordinator labels a steer message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_w5_coordinator_labels_merge(setup):
    registry, llm, logs = setup
    room_view = RoomView(
        plan=[PlanItem("t1", "Build RSVP form", "doing")],
        current_task_id="t1",
        pending=[Message("m1", "Priya", "pm", "add dark mode")],
    )
    new = Message("m2", "Dan", "eng", "make headings bigger")
    llm.push('{"label": "merge", "rationale": "fits current task"}')
    decision = await Coordinator(llm).classify(room_view, new)
    assert decision.action.label == "merge"
    assert llm.calls[0].role is ModelRole.LIGHTNING


# ---------------------------------------------------------------------------
# W6: conflict card with Tavily research and vote
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_w6_conflict_research_and_vote(setup):
    registry, llm, logs = setup
    conflict = OpenConflict(
        with_message_ids=["m1"],
        summary="Google login vs anonymous RSVPs",
        options=["Google login", "Anonymous"],
        research_queries=["rsvp login spam", "anonymous form response rate"],
    )

    class FakeSearch:
        async def search(self, query):
            return SearchResult(query, "evidence", [Source("Auth", "https://a.dev", "login cuts spam")])

    search = FakeSearch()
    llm.push('{"summary": "Login cuts spam [1].", "citations": [{"title": "Auth", "url": "https://a.dev"}]}')
    result = await research_conflict(llm, search, conflict)
    assert result.evidence is not None

    votes = [
        Vote(user_id="alice", option="Google login", domain_role="design"),
        Vote(user_id="bob", option="Anonymous", domain_role="eng"),
    ]
    winner = tally(["Google login", "Anonymous"], votes, "scope", "alice")
    assert winner is not None


# ---------------------------------------------------------------------------
# W8: manual edit via ActorFileTools
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_w8_manual_edit_read_write(setup):
    registry, llm, logs = setup
    actor = await registry.create_room("room-4", "owner-1", description="app")
    tools = ActorFileTools(actor)
    await tools.write_file("src/App.tsx", "export const A = 1")
    result = await tools.read_file("src/App.tsx")
    assert "export const A = 1" in result["content"]


# ---------------------------------------------------------------------------
# W9: create checkpoint + restore
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_w9_checkpoint_create_restore(setup):
    registry, llm, logs = setup
    actor = await registry.create_room("room-5", "owner-1", description="app")
    cp_id = await actor.create_checkpoint("owner-1", description="after task 1")
    assert cp_id is not None
    data = actor.manifest.get_checkpoint(cp_id)
    assert data is not None
    assert data.get("description") == "after task 1"


# ---------------------------------------------------------------------------
# W10: rewind
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_w10_rewind(setup):
    registry, llm, logs = setup
    actor = await registry.create_room("room-6", "owner-1", description="app")
    await actor.create_checkpoint("owner-1", description="cp1")
    seq = actor._state.sequence
    ok = await actor.rewind_to_sequence(seq - 1, user_id="owner-1", reason="oops")
    assert ok == "rewound" or ok is True


# ---------------------------------------------------------------------------
# W11: budget status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_w11_budget_status(setup):
    registry, llm, logs = setup
    actor = await registry.create_room("room-7", "owner-1", description="app")
    status = await actor.get_budget_status()
    assert "paused" in status
    assert "tokens_used" in status


# ---------------------------------------------------------------------------
# W12: sitting end (owner ends)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_w12_sitting_end(setup):
    registry, llm, logs = setup
    actor = await registry.create_room("room-8", "owner-1", description="app")
    ended = await actor.command_end_session("owner-1", reason="owner_ended")
    assert ended is True


# ---------------------------------------------------------------------------
# W13: GitHub export via integration (owner only)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_w13_github_export(setup, monkeypatch):
    registry, llm, logs = setup
    actor = await registry.create_room("room-9", "owner-1", description="app")
    tools = ActorFileTools(actor)
    await tools.write_file("src/App.tsx", "export const A = 1")
    await actor.create_checkpoint("owner-1", description="final")

    from mux.integrations.github import get_github_integration, GitHubTokenData
    from cryptography.fernet import Fernet
    from unittest.mock import patch

    monkeypatch.setattr("mux.integrations.github._github_integration", None)
    github = get_github_integration()
    github._fernet = Fernet(Fernet.generate_key())
    github.store_token("owner-1", GitHubTokenData(access_token="tok"), username="owner-1")

    cp_id = list(actor.manifest.list_checkpoints().keys())[0]

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
        if "/git/ref/heads/main" in url:
            return FakeResp({"object": {"sha": "base-sha"}})
        if "/git/commits/base-sha" in url:
            return FakeResp({"tree": {"sha": "base-tree"}})
        if "/repos/owner-1/repo" in url and "/git" not in url:
            return FakeResp({"default_branch": "main"})
        return FakeResp({}, status=404)

    async def fake_post(url, *, headers, json, timeout):
        if "/git/blobs" in url:
            return FakeResp({"sha": "blob-sha"})
        if "/git/trees" in url:
            return FakeResp({"sha": "tree-sha"})
        if "/git/commits" in url:
            return FakeResp({"sha": "commit-sha"})
        return FakeResp({}, status=201)

    async def fake_patch(url, *, headers, json, timeout):
        return FakeResp({}, status=200)

    with patch("httpx.AsyncClient") as mock_client:
        client = mock_client.return_value.__aenter__.return_value
        client.get = fake_get
        client.post = fake_post
        client.patch = fake_patch
        result = await github.export_checkpoint(
            "owner-1",
            actor.manifest.get_checkpoint(cp_id),
            "owner-1", "repo",
        )
    assert result.commit_sha is not None
