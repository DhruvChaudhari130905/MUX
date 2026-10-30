"""Tests for sitting lifecycle (mux/rooms/sitting.py)."""

import asyncio
import time

import pytest

from mux.events.log import InMemoryEventLog
from mux.events.models import EventType, SittingEndedEvent
from mux.rooms.sitting import SittingManager, SittingConfig, VALID_END_REASONS, create_sitting_manager


# ---------------------------------------------------------------------------
# Config and constants
# ---------------------------------------------------------------------------

def test_valid_end_reasons():
    assert "idle_timeout" in VALID_END_REASONS
    assert "owner_ended" in VALID_END_REASONS


def test_config_defaults():
    c = SittingConfig()
    assert c.idle_timeout == 1800   # 30 minutes
    assert c.check_interval == 60


# ---------------------------------------------------------------------------
# SittingManager
# ---------------------------------------------------------------------------

def test_sitting_manager_defaults():
    log = InMemoryEventLog("room-1")
    sm = SittingManager(room_id="room-1", event_log=log, owner_id="owner-1")
    assert sm.room_id == "room-1"
    assert sm.owner_id == "owner-1"
    assert sm.config.idle_timeout == 1800
    assert sm.is_active() is True


def test_config_custom():
    log = InMemoryEventLog("room-1")
    sm = SittingManager(room_id="room-1", event_log=log, owner_id="owner-1",
                        config=SittingConfig(idle_timeout=60, check_interval=10))
    assert sm.config.idle_timeout == 60
    assert sm.config.check_interval == 10


@pytest.mark.asyncio
async def test_end_session_emits_event():
    log = InMemoryEventLog("room-1")
    sm = SittingManager(
        room_id="room-1", event_log=log, owner_id="owner-1",
        config=SittingConfig(idle_timeout=1),
    )
    await sm.start()
    ok = await sm.end_session("owner-1")
    assert ok is True
    # event is appended to the log inside _end_sitting
    events = await log.get_all("room-1")
    ended = [e for e in events if e.type == EventType.SITTING_ENDED]
    assert len(ended) == 1
    assert ended[0].reason == "owner_ended"
    assert sm.is_active() is False


@pytest.mark.asyncio
async def test_end_session_non_owner_rejected():
    log = InMemoryEventLog("room-1")
    sm = SittingManager(room_id="room-1", event_log=log, owner_id="owner-1")
    await sm.start()
    ok = await sm.end_session("not-owner")
    assert ok is False


@pytest.mark.asyncio
async def test_end_session_already_ended():
    log = InMemoryEventLog("room-1")
    sm = SittingManager(room_id="room-1", event_log=log, owner_id="owner-1")
    await sm.start()
    await sm.end_session("owner-1")
    ok = await sm.end_session("owner-1")
    assert ok is False


@pytest.mark.asyncio
async def test_create_sitting_manager_starts():
    log = InMemoryEventLog("room-1")
    sm = await create_sitting_manager("room-1", log, "owner-1",
                                      config=SittingConfig(idle_timeout=60, check_interval=5))
    assert sm.is_active() is True
    await sm.stop()


@pytest.mark.asyncio
async def test_record_activity_and_idle():
    log = InMemoryEventLog("room-1")
    sm = SittingManager(room_id="room-1", event_log=log, owner_id="owner-1",
                        config=SittingConfig(idle_timeout=1, check_interval=1))
    await sm.start()
    await sm.record_activity("user-1")
    idle = await sm.get_idle_time()
    assert idle is not None and idle < 1
    status = await sm.get_status()
    assert status["active"] is True
    assert status["member_count"] == 1
    await sm.stop()
