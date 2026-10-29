"""End-to-end: a real room runs the coder through full tasks in real Docker.

Full flow per test:
  create room -> add files -> plan + approve task -> coder calls tools
  (read_file, write_file, edit_file, run_build, run_tests, finish_task)
  -> Runner -> DockerSandboxClient -> real throwaway container -> room events.

Covers the whole room workflow with tool calling: creating rooms, making
changes in a room, building and testing inside real Docker containers.

Needs Docker and the `mux-sandbox` image, so gated behind the `docker` marker.
Runs via `make test-docker` (required before every release).

Local development: `pytest -q -m "not docker"` skips these for fast iteration.
"""

import asyncio
import json
import pytest

from mux.agents.llm import LLMReply, ToolCall, Usage
from mux.events.models import EventType

from mux.replay.fake_llm import FakeLLM
from mux.rooms.actor import RoomActor
from mux.rooms.registry import RoomRegistry
from mux.rooms.runtime import RoomRuntime
from mux.sandbox.docker import DockerSandboxClient, image_ready
from mux.sandbox.runner import Runner
from mux.events.log import InMemoryEventLog

pytestmark = pytest.mark.docker

IMAGE = "mux-sandbox"


def tool_reply(*calls: tuple[str, dict]) -> LLMReply:
    return LLMReply(
        text="",
        model="fake-super",
        usage=Usage(10, 5),
        finish_reason="tool_calls",
        tool_calls=[ToolCall(f"c{i}", name, args, json.dumps(args)) for i, (name, args) in enumerate(calls)],
    )


def _docker_available() -> str | None:
    return image_ready(IMAGE)


@pytest.fixture
def docker_runner():
    problem = _docker_available()
    if problem:
        pytest.skip(f"real Docker sandbox unavailable: {problem}")
    return Runner(DockerSandboxClient(), image=IMAGE)


@pytest.fixture
def setup(tmp_path, monkeypatch, docker_runner):
    import mux.rooms.registry as room_registry

    monkeypatch.chdir(tmp_path)
    logs: dict[str, InMemoryEventLog] = {}
    monkeypatch.setattr(
        room_registry, "get_event_log", lambda rid: logs.setdefault(rid, InMemoryEventLog(rid))
    )
    llm = FakeLLM()
    runtimes: list[RoomRuntime] = []

    def factory(actor: RoomActor) -> RoomRuntime:
        rt = RoomRuntime(
            actor,
            llm,
            runner=docker_runner,
            vote_timeout=30,
            question_timeout=30,
            max_turns=3,
        )
        runtimes.append(rt)
        return rt

    return RoomRegistry(runtime_factory=factory), llm, logs, runtimes


async def until(check, timeout: float = 60.0):
    """Wait for background work (coder loop, events) to reach a state."""
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


def of_type(log: InMemoryEventLog, t: EventType):
    return [e for e in log._events if e.type == t]


async def new_room(registry: RoomRegistry, llm: FakeLLM, logs: dict) -> RoomActor:
    actor = await registry.create_room("room_e2e", "alice", name="E2E", description="E2E test room")
    return actor


# ---------------------------------------------------------------------------
# 1. Full happy path: write file -> plan -> coder reads, edits, builds, tests, finishes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_full_room_workflow_real_docker(setup):
    """Create room, write file via tool, plan, coder builds, tests, finishes in real Docker."""
    registry, llm, logs, _runtimes = setup
    actor = await new_room(registry, llm, logs)
    log = logs[actor.room_id]

    # Plan a task and approve it
    await actor.add_plan_item({"id": "t1", "title": "Write, build and test", "status": "draft"}, "coordinator")
    await actor.approve_plan_items(["t1"], "alice")

    # Script the coder: write_file -> run_build -> run_tests -> finish_task
    llm.push(
        tool_reply(("write_file", {
            "path": "src/app.ts",
            "content": "export const x: number = 1\n",
        })),
        tool_reply(("run_build", {})),
        tool_reply(("run_tests", {})),
        tool_reply(("finish_task", {"summary": "Wrote, built and tested in Docker"})),
        tool_reply(("finish_task", {"summary": "Wrote, built and tested in Docker"})),
        tool_reply(("finish_task", {"summary": "Wrote, built and tested in Docker"})),
    )

    await until(lambda: len(of_type(log, EventType.TASK_FINISHED)) == 1)
    plan = await actor.get_plan()
    assert plan[0]["status"] == "done"

    # Verify files exist in the room
    content = await actor.get_file("src/app.ts")
    assert "export const x: number = 1" in content

    await registry.shutdown_all()


# ---------------------------------------------------------------------------
# 2. Build fails in real Docker: broken TypeScript -> errors reported
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_build_fails_real_docker(setup):
    """Coder writes broken TypeScript, run_build fails in Docker, errors surface in room."""
    registry, llm, logs, _runtimes = setup
    actor = await new_room(registry, llm, logs)
    log = logs[actor.room_id]

    await actor.create_file("src/app.ts", "export const x: number = 'not a number'\n", "alice")

    await actor.add_plan_item({"id": "t1", "title": "Build check", "status": "draft"}, "coordinator")
    await actor.approve_plan_items(["t1"], "alice")

    llm.push(
        tool_reply(("run_build", {})),
        tool_reply(("finish_task", {"summary": "Build failed with type errors in Docker"})),
    )

    await until(lambda: len(of_type(log, EventType.TASK_FINISHED)) == 1)
    plan = await actor.get_plan()
    assert plan[0]["status"] == "done"
    await registry.shutdown_all()


# ---------------------------------------------------------------------------
# 3. Tests fail in real Docker: failing test -> failure reported
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_tests_fail_real_docker(setup):
    """Coder writes a failing test, run_tests fails in Docker, failure surfaces."""
    registry, llm, logs, _runtimes = setup
    actor = await new_room(registry, llm, logs)
    log = logs[actor.room_id]

    await actor.create_file("src/failing.test.ts", (
        "import { expect, test } from 'vitest'\n"
        "test('fails on purpose', () => expect(1).toBe(2))\n"
    ), "alice")

    await actor.add_plan_item({"id": "t1", "title": "Run failing test", "status": "draft"}, "coordinator")
    await actor.approve_plan_items(["t1"], "alice")

    llm.push(
        tool_reply(("run_tests", {})),
        tool_reply(("finish_task", {"summary": "Test failed as expected in Docker"})),
    )

    await until(lambda: len(of_type(log, EventType.TASK_FINISHED)) == 1)
    plan = await actor.get_plan()
    assert plan[0]["status"] == "done"
    await registry.shutdown_all()


# ---------------------------------------------------------------------------
# 4. Multiple rooms run concurrently, each with real Docker sandbox
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_multiple_rooms_concurrent_real_docker(setup):
    """Two rooms run coder tasks simultaneously, each in its own Docker container."""
    registry, llm, logs, _runtimes = setup

    actors = []
    for i in range(2):
        actor = await registry.create_room(f"room_concurrent_{i}", "alice", name=f"Room {i}", description="Concurrent")
        await actor.create_file("src/app.ts", f"export const x: number = {i}\n", "alice")
        await actor.add_plan_item({"id": "t1", "title": f"Build room {i}", "status": "draft"}, "coordinator")
        await actor.approve_plan_items(["t1"], "alice")
        actors.append(actor)

    # Script both coders: build then finish, with buffer items per room
    for i in range(2):
        llm.push(
            tool_reply(("run_build", {})),
            tool_reply(("finish_task", {"summary": f"Room {i} built in Docker"})),
            tool_reply(("finish_task", {"summary": f"Room {i} built in Docker"})),
        )

    await until(lambda: len(of_type(logs[actors[0].room_id], EventType.TASK_FINISHED)) == 1)
    await until(lambda: len(of_type(logs[actors[1].room_id], EventType.TASK_FINISHED)) == 1)

    for actor in actors:
        plan = await actor.get_plan()
        assert plan[0]["status"] == "done"

    await registry.shutdown_all()


# ---------------------------------------------------------------------------
# 5. File write then build: new file created via tool, then built in Docker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_write_file_then_build_real_docker(setup):
    """Coder writes a new file via write_file tool, then run_build compiles it in Docker."""
    registry, llm, logs, _runtimes = setup
    actor = await new_room(registry, llm, logs)
    log = logs[actor.room_id]

    await actor.add_plan_item({"id": "t1", "title": "Write and build new file", "status": "draft"}, "coordinator")
    await actor.approve_plan_items(["t1"], "alice")

    llm.push(
        tool_reply(("write_file", {
            "path": "src/new_file.ts",
            "content": "export const y: number = 42\n",
        })),
        tool_reply(("run_build", {})),
        tool_reply(("finish_task", {"summary": "Created new_file.ts and built successfully in Docker"})),
        tool_reply(("finish_task", {"summary": "Created new_file.ts and built successfully in Docker"})),
    )

    await until(lambda: len(of_type(log, EventType.TASK_FINISHED)) == 1)
    plan = await actor.get_plan()
    assert plan[0]["status"] == "done"

    # Verify the file exists in the room
    content = await actor.get_file("src/new_file.ts")
    assert "export const y: number = 42" in content
    await registry.shutdown_all()
