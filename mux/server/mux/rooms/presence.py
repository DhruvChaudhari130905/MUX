"""Ephemeral presence state. Never written to the event log."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, replace
from typing import Dict, Optional, Any, TypedDict, Literal, Required, NotRequired
import threading


class CursorPosition(TypedDict, total=False):
    """Cursor position in a file."""
    file_path: str
    line: int
    column: int


class UserPresenceDict(TypedDict):
    """Serialized user presence for WebSocket broadcast."""
    user_id: Required[str]
    status: Required[Literal["online", "away", "offline"]]
    typing: Required[bool]
    last_seen: Required[float]
    joined_at: Required[float]
    user_name: NotRequired[Optional[str]]
    avatar_url: NotRequired[Optional[str]]
    typing_at: NotRequired[Optional[float]]
    active_tab: NotRequired[Optional[str]]
    cursor_position: NotRequired[Optional[CursorPosition]]


VALID_STATUSES = frozenset({"online", "away", "offline"})
TYPING_TIMEOUT = 3.0  # seconds


@dataclass(frozen=True, slots=True)
class UserPresence:
    """Ephemeral presence info for a single user in a room (immutable)."""

    user_id: str
    user_name: Optional[str] = None
    avatar_url: Optional[str] = None
    status: Literal["online", "away", "offline"] = "online"
    typing: bool = False
    typing_at: Optional[float] = None
    active_tab: Optional[str] = None
    cursor_position: Optional[CursorPosition] = None
    last_seen: float = field(default_factory=time.monotonic)
    joined_at: float = field(default_factory=time.monotonic)

    def is_idle(self, idle_seconds: int = 120) -> bool:
        """Return True if user has been idle longer than idle_seconds."""
        return (time.monotonic() - self.last_seen) > idle_seconds

    def is_typing_expired(self) -> bool:
        """Return True if typing indicator has expired."""
        if not self.typing or self.typing_at is None:
            return False
        return (time.monotonic() - self.typing_at) > TYPING_TIMEOUT

    def with_activity(self) -> "UserPresence":
        """Return new instance with updated last_seen."""
        return replace(self, last_seen=time.monotonic())

    def with_typing(self, typing: bool) -> "UserPresence":
        """Return new instance with updated typing status."""
        return replace(
            self,
            typing=typing,
            typing_at=time.monotonic() if typing else None,
            last_seen=time.monotonic(),
        )

    def with_active_tab(self, tab: Optional[str]) -> "UserPresence":
        return replace(self, active_tab=tab, last_seen=time.monotonic())

    def with_cursor(self, position: Optional[CursorPosition]) -> "UserPresence":
        return replace(self, cursor_position=position, last_seen=time.monotonic())

    def with_status(self, status: str) -> "UserPresence":
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Must be one of {VALID_STATUSES}")
        # Do NOT update last_seen when marking away/offline - that would reset idle timer
        return replace(self, status=status)

    def with_rejoin(self, user_name: Optional[str] = None, avatar_url: Optional[str] = None) -> "UserPresence":
        """Return new instance for rejoin (updates name/avatar, resets joined_at)."""
        return replace(
            self,
            user_name=user_name or self.user_name,
            avatar_url=avatar_url or self.avatar_url,
            status="online",
            last_seen=time.monotonic(),
            joined_at=time.monotonic(),
        )

    def to_dict(self) -> UserPresenceDict:
        """Serialize to dict for WebSocket broadcast (omits None values)."""
        d: UserPresenceDict = {
            "user_id": self.user_id,
            "status": self.status,
            "typing": self.typing,
            "last_seen": self.last_seen,
            "joined_at": self.joined_at,
        }
        if self.user_name is not None:
            d["user_name"] = self.user_name
        if self.avatar_url is not None:
            d["avatar_url"] = self.avatar_url
        if self.typing_at is not None:
            d["typing_at"] = self.typing_at
        if self.active_tab is not None:
            d["active_tab"] = self.active_tab
        if self.cursor_position is not None:
            d["cursor_position"] = self.cursor_position
        return d

    def __repr__(self) -> str:
        return f"UserPresence(user_id={self.user_id!r}, status={self.status!r}, typing={self.typing})"


class PresenceManager:
    """Manages ephemeral presence state for a single room.

    This state is never persisted to the event log - it's purely
    for real-time UI (avatars, typing indicators, active users list).
    """

    def __init__(
        self,
        idle_timeout: int = 120,
        *,
        use_asyncio: bool = True,
        offline_cleanup_age: int = 3600,
    ) -> None:
        """Initialize presence manager.

        Args:
            idle_timeout: Seconds after which a user is considered idle (default 2 min).
            use_asyncio: If True, use asyncio.Lock. If False, use threading.RLock.
            offline_cleanup_age: Seconds after which offline users are removed (default 1 hour).
        """
        self._users: Dict[str, UserPresence] = {}
        self._connections: Dict[str, int] = {}
        self._idle_timeout = idle_timeout
        self._offline_cleanup_age = offline_cleanup_age
        self._use_asyncio = use_asyncio
        # Async methods take the asyncio lock and *_sync methods the RLock, whatever the mode
        # (a plain `with` can't take an asyncio.Lock, nor `async with` an RLock).
        self._async_mutex = asyncio.Lock()
        self._sync_mutex = threading.RLock()

    def _get_now(self) -> float:
        return time.monotonic()

    async def join(
        self,
        user_id: str,
        user_name: Optional[str] = None,
        avatar_url: Optional[str] = None,
    ) -> UserPresence:
        """Add or update a user's presence when they join the room.

        If the user already has presence (e.g., reconnecting), updates
        their info and sets status to online.
        """
        async with self._async_mutex:
            existing = self._users.get(user_id)
            self._connections[user_id] = self._connections.get(user_id, 0) + 1
            if existing:
                updated = existing.with_rejoin(user_name, avatar_url)
                self._users[user_id] = updated
                return updated

            presence = UserPresence(
                user_id=user_id,
                user_name=user_name,
                avatar_url=avatar_url,
                status="online",
            )
            self._users[user_id] = presence
            return presence

    def join_sync(
        self,
        user_id: str,
        user_name: Optional[str] = None,
        avatar_url: Optional[str] = None,
    ) -> UserPresence:
        with self._sync_mutex:
            existing = self._users.get(user_id)
            self._connections[user_id] = self._connections.get(user_id, 0) + 1
            if existing:
                updated = existing.with_rejoin(user_name, avatar_url)
                self._users[user_id] = updated
                return updated

            presence = UserPresence(
                user_id=user_id,
                user_name=user_name,
                avatar_url=avatar_url,
                status="online",
            )
            self._users[user_id] = presence
            return presence

    async def leave(self, user_id: str) -> Optional[UserPresence]:
        """Remove a user's presence when they leave the room."""
        async with self._async_mutex:
            connections = self._connections.get(user_id, 0)
            if connections > 1:
                self._connections[user_id] = connections - 1
                return self._users.get(user_id)
            self._connections.pop(user_id, None)
            return self._users.pop(user_id, None)

    def leave_sync(self, user_id: str) -> Optional[UserPresence]:
        with self._sync_mutex:
            connections = self._connections.get(user_id, 0)
            if connections > 1:
                self._connections[user_id] = connections - 1
                return self._users.get(user_id)
            self._connections.pop(user_id, None)
            return self._users.pop(user_id, None)

    async def set_typing(self, user_id: str, typing: bool) -> bool:
        """Update a user's typing indicator. Returns True if user exists."""
        async with self._async_mutex:
            user = self._users.get(user_id)
            if user is None:
                return False
            self._users[user_id] = user.with_typing(typing)
            return True

    def set_typing_sync(self, user_id: str, typing: bool) -> bool:
        with self._sync_mutex:
            user = self._users.get(user_id)
            if user is None:
                return False
            self._users[user_id] = user.with_typing(typing)
            return True

    async def set_active_tab(self, user_id: str, tab: Optional[str]) -> bool:
        async with self._async_mutex:
            user = self._users.get(user_id)
            if user is None:
                return False
            self._users[user_id] = user.with_active_tab(tab)
            return True

    def set_active_tab_sync(self, user_id: str, tab: Optional[str]) -> bool:
        with self._sync_mutex:
            user = self._users.get(user_id)
            if user is None:
                return False
            self._users[user_id] = user.with_active_tab(tab)
            return True

    async def set_cursor(self, user_id: str, position: Optional[CursorPosition]) -> bool:
        async with self._async_mutex:
            user = self._users.get(user_id)
            if user is None:
                return False
            self._users[user_id] = user.with_cursor(position)
            return True

    def set_cursor_sync(self, user_id: str, position: Optional[CursorPosition]) -> bool:
        with self._sync_mutex:
            user = self._users.get(user_id)
            if user is None:
                return False
            self._users[user_id] = user.with_cursor(position)
            return True

    async def set_status(self, user_id: str, status: str) -> bool:
        async with self._async_mutex:
            user = self._users.get(user_id)
            if user is None:
                return False
            self._users[user_id] = user.with_status(status)
            return True

    def set_status_sync(self, user_id: str, status: str) -> bool:
        with self._sync_mutex:
            user = self._users.get(user_id)
            if user is None:
                return False
            self._users[user_id] = user.with_status(status)
            return True

    async def update_activity(self, user_id: str) -> bool:
        async with self._async_mutex:
            user = self._users.get(user_id)
            if user is None:
                return False
            self._users[user_id] = user.with_activity()
            return True

    def update_activity_sync(self, user_id: str) -> bool:
        with self._sync_mutex:
            user = self._users.get(user_id)
            if user is None:
                return False
            self._users[user_id] = user.with_activity()
            return True

    async def get(self, user_id: str) -> Optional[UserPresence]:
        async with self._async_mutex:
            return self._users.get(user_id)

    def get_sync(self, user_id: str) -> Optional[UserPresence]:
        with self._sync_mutex:
            return self._users.get(user_id)

    async def get_all(self) -> Dict[str, UserPresenceDict]:
        """Get all users' presence as serialized dicts (safe copies)."""
        async with self._async_mutex:
            return {uid: pres.to_dict() for uid, pres in self._users.items()}

    def get_all_sync(self) -> Dict[str, UserPresenceDict]:
        with self._sync_mutex:
            return {uid: pres.to_dict() for uid, pres in self._users.items()}

    async def get_online_users(self) -> Dict[str, UserPresenceDict]:
        async with self._async_mutex:
            return {
                uid: pres.to_dict()
                for uid, pres in self._users.items()
                if pres.status != "offline"
            }

    async def get_typing_users(self) -> Dict[str, UserPresenceDict]:
        """Get users who are currently typing (auto-clears expired indicators)."""
        async with self._async_mutex:
            result = {}
            for uid, pres in self._users.items():
                if pres.typing:
                    if pres.is_typing_expired():
                        self._users[uid] = pres.with_typing(False)
                    else:
                        result[uid] = pres.to_dict()
            return result

    def get_typing_users_sync(self) -> Dict[str, UserPresenceDict]:
        """Get users who are currently typing (auto-clears expired indicators)."""
        with self._sync_mutex:
            result = {}
            for uid, pres in self._users.items():
                if pres.typing:
                    if pres.is_typing_expired():
                        self._users[uid] = pres.with_typing(False)
                    else:
                        result[uid] = pres.to_dict()
            return result

    async def prune_expired_typing(self) -> list[str]:
        """Clear expired typing indicators. Returns list of user_ids cleared."""
        cleared = []
        async with self._async_mutex:
            for uid, pres in self._users.items():
                if pres.typing and pres.is_typing_expired():
                    self._users[uid] = pres.with_typing(False)
                    cleared.append(uid)
        return cleared

    def prune_expired_typing_sync(self) -> list[str]:
        cleared = []
        with self._sync_mutex:
            for uid, pres in self._users.items():
                if pres.typing and pres.is_typing_expired():
                    self._users[uid] = pres.with_typing(False)
                    cleared.append(uid)
        return cleared

    async def get_count(self) -> int:
        """Return accurate user count (async-safe)."""
        async with self._async_mutex:
            return len(self._users)

    async def has_user(self, user_id: str) -> bool:
        """Return True if user is present (async-safe)."""
        async with self._async_mutex:
            return user_id in self._users

    def get_count_sync(self) -> int:
        with self._sync_mutex:
            return len(self._users)

    def has_user_sync(self, user_id: str) -> bool:
        with self._sync_mutex:
            return user_id in self._users

    async def get_idle_users(self) -> Dict[str, UserPresenceDict]:
        async with self._async_mutex:
            return {
                uid: pres.to_dict()
                for uid, pres in self._users.items()
                if pres.is_idle(self._idle_timeout)
            }

    async def mark_idle_as_away(self) -> list[str]:
        """Mark idle users as 'away'. Returns list of user_ids changed.
        Does NOT update last_seen (so they stay away until activity).
        """
        changed = []
        async with self._async_mutex:
            for user_id, pres in self._users.items():
                if pres.status == "online" and pres.is_idle(self._idle_timeout):
                    self._users[user_id] = pres.with_status("away")
                    changed.append(user_id)
        return changed

    def mark_idle_as_away_sync(self) -> list[str]:
        changed = []
        with self._sync_mutex:
            for user_id, pres in self._users.items():
                if pres.status == "online" and pres.is_idle(self._idle_timeout):
                    self._users[user_id] = pres.with_status("away")
                    changed.append(user_id)
        return changed

    async def cleanup_offline(self) -> list[str]:
        """Remove users who have been offline longer than offline_cleanup_age."""
        removed = []
        now = self._get_now()
        async with self._async_mutex:
            for user_id, pres in list(self._users.items()):
                if pres.status == "offline" and (now - pres.last_seen) > self._offline_cleanup_age:
                    del self._users[user_id]
                    removed.append(user_id)
        return removed

    def cleanup_offline_sync(self) -> list[str]:
        removed = []
        now = self._get_now()
        with self._sync_mutex:
            for user_id, pres in list(self._users.items()):
                if pres.status == "offline" and (now - pres.last_seen) > self._offline_cleanup_age:
                    del self._users[user_id]
                    removed.append(user_id)
        return removed

    def __len__(self) -> int:
        """Return user count (thread-safe via sync mutex)."""
        with self._sync_mutex:
            return len(self._users)

    def __contains__(self, user_id: str) -> bool:
        """Return True if user is present (thread-safe via sync mutex)."""
        with self._sync_mutex:
            return user_id in self._users
