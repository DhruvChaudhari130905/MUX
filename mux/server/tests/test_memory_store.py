"""Persistence boundaries for room-scoped agent memory."""

from mux.memory.pins import Pin
from mux.memory.store import RoomMemoryStore
from mux.memory.task_log import Log, LogDraft


def test_room_memory_survives_reload_and_is_isolated(tmp_path):
    first = RoomMemoryStore("room/one", tmp_path)
    first.save_task_log(Log(LogDraft(app="Yoga booking app."), [Pin("vote", "c1", "Use tabs")]))

    loaded = RoomMemoryStore("room/one", tmp_path)
    other = RoomMemoryStore("room-two", tmp_path)

    assert loaded.current_log is not None
    assert loaded.current_log.draft.app == "Yoga booking app."
    assert loaded.current_log.pins == [Pin("vote", "c1", "Use tabs")]
    assert "Yoga booking app." in loaded.markdown_path.read_text()
    assert other.current_log is None


def test_pending_pin_is_retained_until_it_is_written_to_a_task_log(tmp_path):
    store = RoomMemoryStore("room-one", tmp_path)
    pin = Pin("override", "c1", "Use tabs")
    store.queue_pin(pin)

    reloaded = RoomMemoryStore("room-one", tmp_path)
    assert reloaded.pending_pins == [pin]

    reloaded.save_task_log(Log(LogDraft(app="Yoga booking app."), [pin]))
    assert RoomMemoryStore("room-one", tmp_path).pending_pins == []
