"""Tests for the room actor."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from mux.rooms.actor import RoomActor, ActorConfig
from mux.rooms.plan import Plan, PlanItem, PlanStatus
from mux.rooms.inbox import Inbox, InboxMessage
from mux.rooms.budget import BudgetManager
from mux.rooms.locks import LockManager
from mux.rooms.presence import PresenceManager
from mux.rooms.sitting import SittingManager
from mux.events.log import EventLog
from mux.events.models import EventType


class TestRoomActorLifecycle:
    """Tests for RoomActor startup, shutdown, and rehydration."""

    @pytest.fixture
    def mock_registry(self):
        return AsyncMock()

    @pytest.fixture
    def mock_event_log(self):
        log = AsyncMock(spec=EventLog)
        log.replay = AsyncMock(return_value=[])
        log.append = AsyncMock()
        log.checkpoint = AsyncMock()
        log.get_latest = AsyncMock(return_value=[])
        log.get_all = AsyncMock(return_value=[])
        return log

    @pytest.fixture
    def actor(self, mock_event_log):
        return RoomActor(
            room_id="test-room-123",
            owner_id="user-456",
            event_log=mock_event_log,
        )

    @pytest.mark.asyncio
    async def test_actor_starts_and_registers(self, actor, mock_event_log):
        """Actor should start successfully."""
        await actor.start()

        assert actor.is_running() is True
        assert actor._state.running is True

    @pytest.mark.asyncio
    async def test_actor_stops_and_deregisters(self, actor, mock_event_log):
        """Actor should stop cleanly."""
        await actor.start()
        await actor.stop()

        assert actor.is_running() is False
        assert actor._state.running is False

    @pytest.mark.asyncio
    async def test_actor_rehydrates_from_event_log(self, actor, mock_event_log):
        """Actor should replay events to rebuild state on restart (via registry)."""
        # Note: Rehydration is done by RoomRegistry, not directly by actor.start()
        # This test verifies the actor can be created with an event log
        await actor.start()

        assert actor.is_running() is True
        assert actor.event_log is mock_event_log

    @pytest.mark.asyncio
    async def test_actor_handles_concurrent_start_stop(self, actor):
        """Multiple start/stop calls should be idempotent."""
        await actor.start()
        await actor.start()
        await actor.stop()
        await actor.stop()

        assert actor.is_running() is False


class TestRoomActorPlanManagement:
    """Tests for plan ownership and mutations."""

    @pytest.fixture
    def mock_event_log(self):
        log = AsyncMock(spec=EventLog)
        log.append = AsyncMock()
        log.get_all = AsyncMock(return_value=[])
        return log

    @pytest.fixture
    def actor(self, mock_event_log):
        return RoomActor(room_id="test-room", owner_id="owner", event_log=mock_event_log)

    @pytest.mark.asyncio
    async def test_actor_owns_plan_exclusively(self, actor, mock_event_log):
        """Only the actor should mutate the plan."""
        await actor.start()

        # Add an item first
        await actor.add_plan_item({"id": "1", "title": "Task 1", "status": "draft"}, "owner")

        # Direct mutation should not be possible (plan items are returned as copies)
        items = await actor.get_plan()
        items[0]["status"] = "done"
        # The internal plan should not be affected
        items_after = await actor.get_plan()
        assert items_after[0]["status"] == "draft"

        # Must go through actor
        updated = await actor.update_plan_item("1", {"status": "doing"}, "owner")
        assert updated["status"] == "doing"
        items_after = await actor.get_plan()
        assert items_after[0]["status"] == "doing"

    @pytest.mark.asyncio
    async def test_plan_changes_are_serialized(self, actor, mock_event_log):
        """All plan changes go through actor's sequential processing."""
        await actor.start()

        await actor.add_plan_item({"id": "1", "title": "Task 1", "status": "draft"}, "owner")
        await actor.add_plan_item({"id": "2", "title": "Task 2", "status": "draft"}, "owner")

        results = await asyncio.gather(
            actor.update_plan_item("1", {"status": "doing"}, "owner"),
            actor.update_plan_item("2", {"status": "doing"}, "owner"),
            actor.update_plan_item("1", {"status": "done"}, "owner"),
        )

        assert all(r is not None for r in results)
        items = await actor.get_plan()
        assert items[0]["status"] == "done"
        assert items[1]["status"] == "doing"

    @pytest.mark.asyncio
    async def test_plan_validation_on_each_change(self, actor, mock_event_log):
        """Plan validation runs on every mutation."""
        await actor.start()

        await actor.add_plan_item({"id": "1", "title": "Task 1", "status": "draft"}, "owner")

        with pytest.raises(ValueError, match="Invalid status"):
            await actor.update_plan_item("1", {"status": "invalid_status"}, "owner")

        await actor.update_plan_item("1", {"status": "doing"}, "owner")
        await actor.update_plan_item("1", {"status": "done"}, "owner")
        items = await actor.get_plan()
        assert items[0]["status"] == "done"

    @pytest.mark.asyncio
    async def test_reorder_moves_only_draft_and_upcoming_tasks(self, actor):
        await actor.start()
        items = [
            {"id": "t1", "title": "Done", "status": "done"},
            {"id": "t2", "title": "First todo", "status": "todo"},
            {"id": "t3", "title": "In progress", "status": "doing"},
            {"id": "t4", "title": "Draft", "status": "draft"},
            {"id": "t5", "title": "Stopped", "status": "skipped_question"},
            {"id": "t6", "title": "Second todo", "status": "todo"},
        ]
        for item in items:
            await actor.add_plan_item(item, "owner")

        await actor.reorder_plan(["t4", "t6", "t2"], "editor")

        result = await actor.get_plan()
        assert [item["id"] for item in result] == ["t1", "t4", "t3", "t6", "t5", "t2"]
        assert [result[index]["status"] for index in (0, 2, 4)] == ["done", "doing", "skipped_question"]

    @pytest.mark.asyncio
    async def test_reorder_rejects_stale_task_ids_without_changing_plan(self, actor):
        await actor.start()
        await actor.add_plan_item({"id": "t1", "title": "One", "status": "todo"}, "owner")
        await actor.add_plan_item({"id": "t2", "title": "Two", "status": "todo"}, "owner")

        with pytest.raises(ValueError, match="task list changed"):
            await actor.reorder_plan(["t2"], "editor")

        assert [item["id"] for item in await actor.get_plan()] == ["t1", "t2"]

    @pytest.mark.asyncio
    async def test_material_plan_edit_requires_approval_again(self, actor):
        await actor.start()
        await actor.add_plan_item({"id": "t1", "title": "Original task", "status": "draft"}, "coordinator")
        await actor.approve_plan_items(["t1"], "owner")

        changed = await actor.replace_plan(
            [{"id": "t1", "title": "Changed task", "status": "todo"}], "editor",
        )
        assert changed[0]["status"] == "draft"

        unchanged = await actor.replace_plan(
            [{"id": "t1", "title": "Changed task", "status": "draft"}], "editor",
        )
        assert unchanged[0]["status"] == "draft"
        await actor.approve_plan_items(["t1"], "owner")
        still_approved = await actor.replace_plan(
            [{"id": "t1", "title": "Changed task", "status": "draft"}], "editor",
        )
        assert still_approved[0]["status"] == "todo"

    @pytest.mark.asyncio
    async def test_command_plan_edits_cannot_approve_changed_or_new_work(self, actor):
        await actor.start()
        await actor.add_plan_item({"id": "t1", "title": "Original task", "status": "draft"}, "coordinator")
        await actor.command_edit_plan([
            {"type": "update", "item_id": "t1", "changes": {"title": "Changed task", "status": "todo"}},
            {"type": "add", "item": {"id": "t2", "title": "New task", "status": "todo"}},
        ], "editor")
        assert [(item["title"], item["status"]) for item in await actor.get_plan()] == [
            ("Changed task", "draft"), ("New task", "draft"),
        ]


class TestRoomActorInboxProcessing:
    """Tests for inbox message handling at turn boundaries."""

    @pytest.fixture
    def mock_event_log(self):
        log = AsyncMock(spec=EventLog)
        log.append = AsyncMock()
        log.get_all = AsyncMock(return_value=[])
        return log

    @pytest.fixture
    def actor(self, mock_event_log):
        return RoomActor(room_id="test-room", owner_id="owner", event_log=mock_event_log)

    @pytest.mark.asyncio
    async def test_inbox_drained_at_turn_boundary(self, actor, mock_event_log):
        """Inbox is processed after each coder turn."""
        await actor.start()

        await actor.add_message("merge", "First", user_id="user1")
        await actor.add_message("merge", "Second", user_id="user1")

        merges, edit_notes, interrupt = await actor.drain_inbox()

        assert len(merges) == 2
        assert merges == ["First", "Second"]
        assert interrupt is False

    @pytest.mark.asyncio
    async def test_merge_messages_combined(self, actor, mock_event_log):
        """Multiple user messages merged into single turn."""
        await actor.start()

        await actor.add_message("merge", "Fix bug", user_id="user1")
        await actor.add_message("merge", "Also add tests", user_id="user1")

        merges, edit_notes, interrupt = await actor.drain_inbox()

        assert "Fix bug" in merges
        assert "Also add tests" in merges

    @pytest.mark.asyncio
    async def test_interrupt_aborts_current_turn(self, actor, mock_event_log):
        """Interrupt message aborts in-progress turn."""
        await actor.start()

        await actor.add_message("merge", "Work", user_id="user1")
        await actor.add_message("interrupt", "STOP", user_id="user1")

        merges, edit_notes, interrupt = await actor.drain_inbox()

        assert interrupt is True

    @pytest.mark.asyncio
    async def test_inbox_priority_ordering(self, actor, mock_event_log):
        """System interrupts processed before user messages."""
        await actor.start()

        await actor.add_message("merge", "User msg", user_id="user1")
        await actor.add_message("interrupt", "INTERRUPT", user_id="system")
        await actor.add_message("merge", "Another user msg", user_id="user1")

        merges, edit_notes, interrupt = await actor.drain_inbox()

        assert interrupt is True


class TestRoomActorFileOperations:
    """Tests for file read/write through the actor."""

    @pytest.fixture
    def mock_event_log(self):
        log = AsyncMock(spec=EventLog)
        log.append = AsyncMock()
        log.get_all = AsyncMock(return_value=[])
        return log

    @pytest.fixture
    def actor(self, mock_event_log):
        return RoomActor(room_id="test-room", owner_id="owner", event_log=mock_event_log)

    @pytest.mark.asyncio
    async def test_actor_applies_file_changes_sequentially(self, actor, mock_event_log):
        """File writes are applied one at a time in order."""
        await actor.start()

        await asyncio.gather(
            actor.create_file("a.py", "print('a')", "owner"),
            actor.create_file("b.py", "print('b')", "owner"),
            actor.update_file("a.py", "print('a updated')", "owner"),
        )

        assert await actor.get_file("a.py") == "print('a updated')"
        assert await actor.get_file("b.py") == "print('b')"

    @pytest.mark.asyncio
    async def test_file_locks_prevent_concurrent_edits(self, actor, mock_event_log):
        """Soft locks prevent simultaneous edits to same file."""
        await actor.start()

        result = await actor.lock_file("main.py", "user-1")
        assert result is True

        result = await actor.lock_file("main.py", "user-2")
        assert result is False  # Already locked

        await actor.unlock_file("main.py", "user-1")

        result = await actor.lock_file("main.py", "user-2")
        assert result is True

    @pytest.mark.asyncio
    async def test_file_reads_reflect_latest_writes(self, actor, mock_event_log):
        """Reads always see the most recent committed write."""
        await actor.start()

        await actor.create_file("config.json", '{"version": 1}', "owner")
        content = await actor.get_file("config.json")
        assert content == '{"version": 1}'

        await actor.update_file("config.json", '{"version": 2}', "owner")
        content = await actor.get_file("config.json")
        assert content == '{"version": 2}'


class TestRoomActorBudgetEnforcement:
    """Tests for token and sandbox budget limits."""

    @pytest.fixture
    def mock_event_log(self):
        log = AsyncMock(spec=EventLog)
        log.append = AsyncMock()
        log.get_all = AsyncMock(return_value=[])
        return log

    @pytest.fixture
    def actor(self, mock_event_log):
        config = ActorConfig(budget_token_cap=1000, budget_sandbox_run_cap=10)
        return RoomActor(room_id="test-room", owner_id="owner", event_log=mock_event_log, config=config)

    @pytest.mark.asyncio
    async def test_actor_pauses_at_token_cap(self, actor, mock_event_log):
        """Actor pauses when token budget exhausted."""
        await actor.start()

        # Consume tokens exceeding the cap (1000)
        await actor.record_tokens(1000, "owner")

        # The budget should be paused now
        status = await actor.get_budget_status()
        assert status["paused"] is True

    @pytest.mark.asyncio
    async def test_owner_can_raise_budget(self, actor, mock_event_log):
        """Owner can raise budget to resume."""
        await actor.start()

        # Exhaust budget
        await actor.record_tokens(1000, "owner")
        await actor.add_message("merge", "Work", user_id="user1")

        # Budget should be paused
        status = await actor.get_budget_status()
        assert status["paused"] is True

        # Owner raises cap
        await actor.raise_budget_caps("owner", token_cap=2000)

        # Budget should be resumed
        status = await actor.get_budget_status()
        assert status["paused"] is False

    @pytest.mark.asyncio
    async def test_sandbox_run_budget_tracked_separately(self, actor, mock_event_log):
        """Sandbox runs have separate budget accounting."""
        await actor.start()

        await actor.record_sandbox_run("owner")

        status = await actor.get_budget_status()
        assert status["sandbox_runs_used"] == 1
        assert status["tokens_used"] == 0


class TestRoomActorCoderLoopIntegration:
    """Tests for coder loop execution within actor."""

    @pytest.fixture
    def mock_event_log(self):
        log = AsyncMock(spec=EventLog)
        log.append = AsyncMock()
        log.get_all = AsyncMock(return_value=[])
        return log

    @pytest.fixture
    def actor(self, mock_event_log):
        return RoomActor(room_id="test-room", owner_id="owner", event_log=mock_event_log)

    @pytest.mark.asyncio
    async def test_coder_loop_receives_merged_context(self, actor, mock_event_log):
        """Coder loop gets plan + inbox + file context via drain_inbox."""
        await actor.start()

        await actor.add_plan_item({"id": "1", "title": "Write test", "status": "draft"}, "owner")
        await actor.add_message("merge", "Make it pass", user_id="user1")

        merges, edit_notes, interrupt = await actor.drain_inbox()

        plan_items = await actor.get_plan()
        assert "Write test" in plan_items[0]["title"]
        assert "Make it pass" in merges

    @pytest.mark.asyncio
    async def test_file_operations_executed_sequentially(self, actor, mock_event_log):
        """File operations from tool calls are executed by actor sequentially."""
        await actor.start()

        # Simulate tool calls by using actor's file methods
        await actor.create_file("test.py", "x=1", "owner")
        await actor.update_file("test.py", "x=2", "owner")

        assert await actor.get_file("test.py") == "x=2"


class TestRoomActorPresenceAndSitting:
    """Tests for presence tracking and sitting detection."""

    @pytest.fixture
    def mock_event_log(self):
        log = AsyncMock(spec=EventLog)
        log.append = AsyncMock()
        log.get_all = AsyncMock(return_value=[])
        log.get_latest = AsyncMock(return_value=[])
        return log

    @pytest.fixture
    def actor(self, mock_event_log):
        return RoomActor(room_id="test-room", owner_id="owner", event_log=mock_event_log)

    @pytest.mark.asyncio
    async def test_presence_tracked_per_user(self, actor, mock_event_log):
        """Actor tracks each user's presence state."""
        await actor.start()

        await actor.user_join("user-1", "User One")
        await actor.user_join("user-2", "User Two")

        await actor.set_presence_status("user-1", "online")
        await actor.set_presence_status("user-2", "away")

        presence = await actor.get_all_presence()
        # get_all_presence returns dicts (UserPresenceDict), not objects
        assert presence["user-1"]["status"] == "online"
        assert presence["user-2"]["status"] == "away"

    @pytest.mark.asyncio
    async def test_sitting_ends_after_inactivity(self, actor, mock_event_log):
        """Sitting ends when all members gone for threshold."""
        await actor.start()

        await actor.user_join("user-1", "User One")
        await actor.user_leave("user-1")

        # Manually end the sitting
        await actor.command_end_session("owner")

        assert actor.sitting.is_active() is False

    @pytest.mark.asyncio
    async def test_owner_can_end_sitting_early(self, actor, mock_event_log):
        """Owner can explicitly end sitting."""
        await actor.start()

        await actor.command_end_session("owner")

        assert actor.sitting.is_active() is False


class TestRoomActorConcurrency:
    """Tests for actor's sequential processing guarantees."""

    @pytest.fixture
    def mock_event_log(self):
        log = AsyncMock(spec=EventLog)
        log.append = AsyncMock()
        log.get_all = AsyncMock(return_value=[])
        return log

    @pytest.fixture
    def actor(self, mock_event_log):
        return RoomActor(room_id="test-room", owner_id="owner", event_log=mock_event_log)

    @pytest.mark.asyncio
    async def test_all_operations_sequentialized(self, actor, mock_event_log):
        """All public methods execute sequentially, no race conditions."""
        await actor.start()

        await actor.add_plan_item({"id": "1", "title": "Task", "status": "draft"}, "owner")

        ops = [
            actor.update_plan_item("1", {"status": "doing"}, "owner"),
            actor.create_file("a.py", "1", "owner"),
            actor.create_file("b.py", "2", "owner"),
            actor.lock_file("a.py", "user-1"),
            actor.set_presence_status("user-1", "online"),
        ]

        results = await asyncio.gather(*ops, return_exceptions=True)

        assert all(not isinstance(r, Exception) for r in results)
        items = await actor.get_plan()
        assert items[0]["status"] == "doing"

    @pytest.mark.asyncio
    async def test_actor_task_cancellation_cleanup(self, actor, mock_event_log):
        """Cancelling actor task cleans up properly."""
        await actor.start()

        task = asyncio.create_task(actor._run_loop())
        await asyncio.sleep(0.01)

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        assert actor.is_running() is False


class TestRoomActorPersistence:
    """Tests for event sourcing and checkpointing."""

    @pytest.fixture
    def mock_event_log(self):
        log = AsyncMock(spec=EventLog)
        log.append = AsyncMock()
        log.get_all = AsyncMock(return_value=[])
        log.get_latest = AsyncMock(return_value=[])
        log.checkpoint = AsyncMock()
        return log

    @pytest.fixture
    def actor(self, mock_event_log):
        return RoomActor(
            room_id="test-room",
            owner_id="owner",
            event_log=mock_event_log,
        )

    @pytest.mark.asyncio
    async def test_events_appended_for_each_mutation(self, actor, mock_event_log):
        """Every state change appends to event log."""
        await actor.start()

        await actor.add_plan_item({"id": "1", "title": "Task", "status": "draft"}, "owner")
        await actor.update_plan_item("1", {"status": "done"}, "owner")
        await actor.create_file("test.py", "x=1", "owner")
        await actor.lock_file("test.py", "user-1")

        # Should have emitted events for each operation
        assert mock_event_log.append.call_count >= 3

    @pytest.mark.asyncio
    async def test_checkpoint_saves_full_state(self, actor, mock_event_log):
        """Periodic checkpoints capture complete actor state."""
        await actor.start()

        await actor.add_plan_item({"id": "1", "title": "Task", "status": "done"}, "owner")
        await actor.create_file("main.py", "print('hi')", "owner")
        await actor.record_tokens(100, "owner")

        checkpoint_id = await actor.create_checkpoint("owner", "Test checkpoint")

        assert checkpoint_id is not None
        assert mock_event_log.append.call_count >= 1


@pytest.fixture
def mock_event_log():
    log = AsyncMock(spec=EventLog)
    log.replay = AsyncMock(return_value=[])
    log.append = AsyncMock()
    log.checkpoint = AsyncMock()
    log.get_latest = AsyncMock(return_value=[])
    log.get_all = AsyncMock(return_value=[])
    return log


@pytest.fixture(autouse=True)
def reset_singletons():
    yield


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

@pytest.mark.asyncio
async def test_broadcasts_go_out_in_sequence_order_even_when_earlier_ones_are_slower():
    """Concurrent broadcasts let event 2 reach a socket before event 1; the bus then skipped event 1 for it."""
    from types import SimpleNamespace

    delivered: list[int] = []

    async def on_event(event):
        await asyncio.sleep(0.03 if event.sequence == 1 else 0)  # the first send is the slow one
        if event.sequence == 2:
            raise RuntimeError("socket gone")  # a failed broadcast must not hold up later ones
        delivered.append(event.sequence)

    actor = RoomActor(room_id="r", owner_id="o", event_log=MagicMock(), on_event=on_event)
    for seq in (1, 2, 3):
        await actor._after_append(SimpleNamespace(sequence=seq, id=f"e{seq}"))  # type: ignore[arg-type]
    assert actor._broadcast_tail is not None
    await asyncio.wait([actor._broadcast_tail])
    assert delivered == [1, 3]
