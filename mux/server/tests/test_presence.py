import pytest

from mux.rooms.presence import PresenceManager


@pytest.mark.asyncio
async def test_user_remains_present_until_last_connection_leaves():
    presence = PresenceManager()
    await presence.join("user-1", "User")
    await presence.join("user-1", "User")

    assert await presence.leave("user-1") is not None
    assert await presence.has_user("user-1")

    assert await presence.leave("user-1") is not None
    assert not await presence.has_user("user-1")


def test_sync_connections_are_counted_separately():
    presence = PresenceManager(use_asyncio=False)
    presence.join_sync("user-1", "User")
    presence.join_sync("user-1", "User")

    assert presence.leave_sync("user-1") is not None
    assert presence.has_user_sync("user-1")
    assert presence.leave_sync("user-1") is not None
    assert not presence.has_user_sync("user-1")
