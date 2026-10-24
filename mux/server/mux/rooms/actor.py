"""RoomActor: an asyncio task that owns the plan, inbox, files, and coder loop, and applies every change one at a time."""

from __future__ import annotations

import asyncio
import inspect
import logging
import mimetypes
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Any, Awaitable, Iterable, cast
from uuid import uuid4
from datetime import datetime, timezone

from mux.events.log import EventLog
from mux.events.models import (
    MessageReactionToggledEvent,
    BaseEvent,
    EventType,
    RoomCreatedEvent,
    RoomJoinedEvent,
    RoomClosedEvent,
    RoomSharingUpdatedEvent,
    CoderRolesUpdatedEvent,
    MemberAccessUpdatedEvent,
    ChangeRequestedEvent,
    ChangeRequestDecidedEvent,
    RoomInviteCreatedEvent,
    RoomInviteRevokedEvent,
    RoomPasswordSetEvent,
    RoomMcpServerSavedEvent,
    RoomMcpServerRemovedEvent,
    RoomMcpAdminToggledEvent,
    RoomAiSettingsSavedEvent,
    RoomAiSettingsClearedEvent,
    KickoffRequestedEvent,
    RoomSkillsSetEvent,
    UserJoinedEvent,
    UserLeftEvent,
    UserTypingEvent,
    UserPresenceChangedEvent,
    PlanCreatedEvent,
    PlanUpdatedEvent,
    PlanItemAddedEvent,
    PlanItemUpdatedEvent,
    PlanItemRemovedEvent,
    PlanItemCompletedEvent,
    UserMessageSentEvent,
    CommandApprovePlanEvent,
    CommandEditPlanEvent,
    CommandRewindEvent,
    CommandEndSessionEvent,
    CommandSteerEvent,
    CommandVoteEvent,
    CommandOverrideEvent,
    CommandAnswerQuestionEvent,
    FileCreatedEvent,
    FileUpdatedEvent,
    FileDeletedEvent,
    CheckpointCreatedEvent,
    CheckpointRestoredEvent,
    ConflictDetectedEvent,
    ConflictResolvedEvent,
    QuestionAskedEvent,
    QuestionAnsweredEvent,
    AIMessageStartedEvent,
    AIMessageChunkEvent,
    AIMessageCompletedEvent,
    TaskStartedEvent,
    TaskFinishedEvent,
    AgentNoticeEvent,
    BudgetExceededEvent,
    SittingEndedEvent,
)
from mux.rooms.plan import Plan
from mux.rooms.file_access import AccessRules, ChangeHeld, ChangeRequest, normalize_folder
from mux.files.room_files import check_path
from mux.rooms.inbox import Inbox
from mux.rooms.presence import CursorPosition, PresenceManager, UserPresence, UserPresenceDict
from mux.rooms.locks import LockManager
from mux.rooms.sitting import SittingManager, SittingConfig, SittingStatus
from mux.rooms.budget import BudgetManager, BudgetConfig, BudgetState, BudgetStatus
from mux.files.store import FileStore
from mux.files.manifest import FileManifest

logger = logging.getLogger(__name__)

ROOM_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MEMBER_ROLES = frozenset({"editor", "viewer"})
AGENT = "coder"  # the coder agent's user id (mux/agents/coder/tools/files.py CODER)

# Events that only exist to be broadcast or that the replay does not need
_PLAN_EDIT_TYPES = frozenset({"add", "update", "remove", "complete"})


class _BroadcastingLog:
    """Wraps an EventLog so that every append (from the actor, budget or sitting
    manager) also runs the actor's post-append hook (sequence sync + broadcast)."""

    room_id: str  # declared for the EventLog protocol; read through __getattr__

    def __init__(self, log: EventLog, after_append: Callable[[BaseEvent], Awaitable[None]]) -> None:
        self._log = log
        self._after_append = after_append

    async def append(self, event: BaseEvent) -> None:
        await self._log.append(event)
        await self._after_append(event)

    async def read_from(self, seq: int, limit: int = 100) -> list[BaseEvent]:
        return await self._log.read_from(seq, limit)

    async def get_latest(self, limit: int = 100) -> list[BaseEvent]:
        return await self._log.get_latest(limit)

    async def get_all(self, room_id: str) -> list[BaseEvent]:
        return await self._log.get_all(room_id)

    async def has_events(self, room_id: str) -> bool:
        return await self._log.has_events(room_id)

    async def get_latest_checkpoint(self, room_id: str) -> Optional[CheckpointCreatedEvent]:
        return await self._log.get_latest_checkpoint(room_id)

    async def get_since(self, room_id: str, sequence: int) -> list[BaseEvent]:
        return await self._log.get_since(room_id, sequence)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._log, name)


@dataclass(frozen=True, slots=True)
class ActorConfig:
    """Configuration for RoomActor."""
    sitting_idle_timeout: int = 1800      # 30 minutes
    sitting_check_interval: int = 60
    budget_token_cap: int = 1_000_000
    budget_sandbox_run_cap: int = 100
    budget_warning_threshold: float = 0.8
    presence_idle_timeout: int = 120
    inbox_max_size: int = 1000
    lock_idle_timeout: int = 120


@dataclass
class ActorState:
    """Mutable runtime state for the actor."""
    room_id: str
    owner_id: str
    created_at: float = field(default_factory=time.time)
    sequence: int = 0
    running: bool = False



class PathConflictError(ValueError):
    """A file can't be saved where a folder is (or inside something that is a file)."""


class RoomActor:
    """
    RoomActor is the single asyncio task that owns all room state and processes
    every event/command sequentially. This ensures deterministic, conflict-free
    room evolution.

    Components owned:
    - Plan: the task plan with validation
    - Inbox: pending messages for the coder
    - PresenceManager: ephemeral user presence (typing, tabs, cursors)
    - LockManager: soft file locks for manual editing
    - SittingManager: detects session end (idle or owner)
    - BudgetManager: token/sandbox caps with pause/resume
    - FileStore + Manifest: file contents and metadata
    """

    def __init__(
        self,
        room_id: str,
        owner_id: str,
        event_log: EventLog,
        config: Optional[ActorConfig] = None,
        *,
        on_event: Optional[Callable[[BaseEvent], Any]] = None,
        on_sitting_ended: Optional[Callable[[str, Any], None]] = None,
        on_budget_pause: Optional[Callable[[str, str], None]] = None,
        on_budget_resume: Optional[Callable[[str], None]] = None,
        on_budget_warning: Optional[Callable[[str, float, float], None]] = None,
        on_lock_expire: Optional[Callable[[str, str], None]] = None,
        files_root: Optional[Path] = None,
    ) -> None:
        if not ROOM_ID_PATTERN.match(room_id):
            raise ValueError(f"Invalid room id {room_id!r}")
        self.room_id = room_id
        self.owner_id = owner_id
        self.event_log = event_log
        self.config = config or ActorConfig()
        self._on_event = on_event
        self._on_sitting_ended = on_sitting_ended
        self._on_budget_pause = on_budget_pause
        self._on_budget_resume = on_budget_resume
        self._on_budget_warning = on_budget_warning
        self._on_lock_expire = on_lock_expire
        self._state = ActorState(room_id=room_id, owner_id=owner_id)
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        self._turn_event = asyncio.Event()
        self._last_event_id: Optional[str] = None
        # The newest broadcast; each one waits for the one before (see _after_append)
        self._broadcast_tail: Optional[asyncio.Task[None]] = None
        # All appends go through this wrapper so every event is sequenced and broadcast
        self._log = _BroadcastingLog(event_log, self._after_append)

        # Persistent room settings (rebuilt from ROOM_* events on rehydration)
        self.members: dict[str, str] = {}  # user_id -> "editor" | "viewer" (owner is implicit)
        self.message_ids: set[str] = set()  # people's messages, the ones that can get reactions
        self.reactions: dict[str, dict[str, set[str]]] = {}  # message_id -> emoji -> user ids
        self.domain_roles: dict[str, str] = {}  # user_id -> "pm" | "design" | "eng" (owner included)
        self.member_names: dict[str, str] = {}  # user_id -> display name, when one was given
        # Coder roles and folder access (mux/rooms/file_access.py), and changes held for approval
        self.access = AccessRules()
        self.change_requests: dict[str, ChangeRequest] = {}  # id -> request, every status
        self.created_at: Optional[datetime] = None
        self.public = False
        self.link_permission = "editor"  # role joining through the link grants when public
        self.allow_anonymous = False
        self.invites: dict[str, str] = {}  # lowercased email -> role, until the person joins or it's removed
        self.password_hash: Optional[str] = None  # mux/rooms/access.py; joining with the password grants editor
        # MCP (mux/mcp): the room's own servers (name -> url, encrypted headers, tools, settings) and which
        # server-wide servers this room uses (name -> enabled, settings)
        self.mcp_servers: dict[str, dict[str, Any]] = {}
        self.mcp_admin: dict[str, dict[str, Any]] = {}
        # The room's own AI provider (mux/agents/room_llm.py): provider, base_url, encrypted api_key, models,
        # version (the saving event's id, so clients are rebuilt when it changes). None: the server's model.
        self.ai_settings: Optional[dict[str, Any]] = None
        self.skills_enabled: set[str] = set()  # skills (mux/skills) the room's coder may use
        self.closed = False

        # Initialize components
        self.plan = Plan()
        self.inbox = Inbox(max_size=self.config.inbox_max_size)
        self.presence = PresenceManager(idle_timeout=self.config.presence_idle_timeout)
        self.locks = LockManager(
            idle_timeout=self.config.lock_idle_timeout,
            on_lock_expire=on_lock_expire,
        )
        self.sitting = SittingManager(
            room_id=room_id,
            event_log=self._log,
            owner_id=owner_id,
            config=SittingConfig(
                idle_timeout=self.config.sitting_idle_timeout,
                check_interval=self.config.sitting_check_interval,
            ),
            on_sitting_ended=on_sitting_ended,
        )
        self.budget = BudgetManager(
            room_id=room_id,
            event_log=self._log,
            owner_id=owner_id,
            config=BudgetConfig(
                token_cap=self.config.budget_token_cap,
                sandbox_run_cap=self.config.budget_sandbox_run_cap,
                warning_threshold=self.config.budget_warning_threshold,
            ),
            on_pause=on_budget_pause,
            on_resume=on_budget_resume,
            on_warning=on_budget_warning,
        )
        # Each room gets its own directory so rooms never overwrite each other's files
        self.files = FileStore(root=(files_root or Path.cwd() / ".mux" / "files") / room_id)
        self.manifest = FileManifest()

    async def start(self) -> None:
        """Start the actor and all background components."""
        async with self._lock:
            if self._state.running:
                return
            self._state.running = True
            self._shutdown_event.clear()
            await self.sitting.start()
            self._task = asyncio.create_task(self._run_loop())
            logger.info(f"RoomActor started for room {self.room_id}")

    async def stop(self, reason: str = "stopped") -> None:
        """Stop the actor and all background components."""
        async with self._lock:
            if not self._state.running:
                return
            self._state.running = False
            self._shutdown_event.set()
            await self.sitting.stop()
            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None
            logger.info(f"RoomActor stopped for room {self.room_id}: {reason}")

    def is_running(self) -> bool:
        return self._state.running

    # ---- Event emission ----

    def _next_sequence(self) -> int:
        self._state.sequence += 1
        return self._state.sequence

    async def _emit(self, event: BaseEvent) -> None:
        """Append event to the log (which assigns its sequence) and broadcast it."""
        await self._log.append(event)

    async def _after_append(self, event: BaseEvent) -> None:
        """Runs after every append, including budget/sitting events."""
        # The log assigns the sequence; keep the actor in sync with it
        self._state.sequence = max(self._state.sequence, event.sequence)
        self._last_event_id = str(event.id)

        if self._on_event:
            # One task per event, chained so broadcasts go out strictly in sequence order. Run side by side,
            # a later event could reach a socket first, and the bus (which skips seq <= the socket's last)
            # would then drop the earlier one for that socket. The chain also keeps every task referenced.
            self._broadcast_tail = asyncio.create_task(self._broadcast_after(self._broadcast_tail, self._on_event, event))

    async def _broadcast_after(self, previous: Optional["asyncio.Task[None]"], cb: Callable[[BaseEvent], Any],
                               event: BaseEvent) -> None:
        if previous is not None and not previous.done():
            await asyncio.wait([previous])  # never raises, even if the earlier broadcast failed or was cancelled
        await self._safe_callback(cb, event)

    async def _safe_callback(self, cb: Callable[[BaseEvent], Any], event: BaseEvent) -> None:
        try:
            result = cb(event)
            # on_event may be sync or async (main.py passes an async broadcaster)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("on_event callback failed")

    def _event_fields(self, user_id: Optional[str]) -> dict:
        """Common BaseEvent fields. The sequence is a placeholder; the log assigns the real one."""
        return {
            "id": uuid4(),
            "room_id": self.room_id,
            "user_id": user_id,
            "timestamp": datetime.now(timezone.utc),
            "sequence": self._next_sequence(),
            "prev_event_id": None,
        }

    # ---- Room settings / membership ----

    def role_of(self, user_id: str) -> Optional[str]:
        """Return the user's role in this room: owner, editor, viewer, or None."""
        if user_id == self.owner_id:
            return "owner"
        if user_id in self.members:
            return self.members[user_id]
        if self.public:
            return "viewer"
        return None

    async def init_room(self, name: str, description: Optional[str] = None, domain_role: Optional[str] = None) -> None:
        """Record the ROOM_CREATED event (persists the owner and metadata for rehydration)."""
        async with self._lock:
            self.manifest.set_room_metadata(name=name, description=description)
            if domain_role:
                self.domain_roles[self.owner_id] = domain_role
            event = RoomCreatedEvent(
                **self._event_fields(self.owner_id),
                room_name=name,
                room_description=description,
                created_by=self.owner_id,
                initial_plan=None,
                domain_role=domain_role,
            )
            self.created_at = event.timestamp
            await self._emit(event)

    async def add_member(
        self, user_id: str, role: str, granted_by: str, user_name: Optional[str] = None, domain_role: Optional[str] = None
    ) -> None:
        """Grant (or change) persistent membership."""
        if role not in MEMBER_ROLES:
            raise ValueError(f"Invalid role {role!r}. Must be one of {sorted(MEMBER_ROLES)}")
        if user_id == self.owner_id:
            raise ValueError("The owner's role cannot be changed")
        async with self._lock:
            self.members[user_id] = role
            if domain_role:
                self.domain_roles[user_id] = domain_role
            if user_name:
                self.member_names[user_id] = user_name
            await self._emit(RoomJoinedEvent(
                **self._event_fields(user_id),
                user_name=user_name,
                role=role,
                granted_by=granted_by,
                domain_role=domain_role,
            ))

    # ---- Coder roles and folder access ----

    async def set_coder_roles(self, roles: dict[str, list[str]], user_id: str, renames: Optional[dict[str, str]] = None) -> None:
        """Replace the room's coder roles. `renames` (old name -> new name) moves members to the new name;
        members whose role is gone keep only their own folders."""
        clean: dict[str, list[str]] = {}
        for name, folders in roles.items():
            label = name.strip()
            if not label or len(label) > 40:
                raise ValueError("A coder role needs a name of 1 to 40 characters")
            clean[label] = sorted({normalize_folder(f) for f in folders})
        async with self._lock:
            self.access.roles = clean
            await self._emit(CoderRolesUpdatedEvent(**self._event_fields(user_id), roles=clean, updated_by=user_id))
            for member_id, role in list(self.access.member_role.items()):
                renamed = (renames or {}).get(role, "").strip()
                if role not in clean:
                    new_role = renamed if renamed in clean else None
                    folders = self.access.member_folders.get(member_id, [])
                    self._apply_member_access(member_id, new_role, folders)
                    await self._emit(MemberAccessUpdatedEvent(
                        **self._event_fields(user_id), member_id=member_id, coder_role=new_role,
                        folders=folders, updated_by=user_id,
                    ))

    async def set_member_access(self, member_id: str, coder_role: Optional[str], folders: list[str], user_id: str) -> None:
        """Give an editor a coder role and/or folders of their own (both empty: unrestricted)."""
        clean = sorted({normalize_folder(f) for f in folders})
        async with self._lock:
            if self.members.get(member_id) != "editor":
                raise ValueError(f"{member_id} is not an editor in this room")
            if coder_role is not None and coder_role not in self.access.roles:
                raise ValueError(f"Unknown coder role {coder_role!r}")
            self._apply_member_access(member_id, coder_role, clean)
            await self._emit(MemberAccessUpdatedEvent(
                **self._event_fields(user_id), member_id=member_id, coder_role=coder_role, folders=clean, updated_by=user_id,
            ))

    def _apply_member_access(self, member_id: str, coder_role: Optional[str], folders: list[str]) -> None:
        if coder_role:
            self.access.member_role[member_id] = coder_role
        else:
            self.access.member_role.pop(member_id, None)
        if folders:
            self.access.member_folders[member_id] = list(folders)
        else:
            self.access.member_folders.pop(member_id, None)

    async def set_sharing(self, public: bool, allow_anonymous: bool, user_id: str, link_permission: str = "editor") -> None:
        if link_permission not in MEMBER_ROLES:
            raise ValueError(f"Invalid link permission {link_permission!r}")
        async with self._lock:
            self.public = public
            self.allow_anonymous = allow_anonymous
            self.link_permission = link_permission
            await self._emit(RoomSharingUpdatedEvent(
                **self._event_fields(user_id),
                public=public,
                allow_anonymous=allow_anonymous,
                updated_by=user_id,
                link_permission=link_permission,
            ))

    async def create_invite(self, email: str, role: str, invited_by: str) -> None:
        """Invite an email address (inviting it again changes the role)."""
        if role not in MEMBER_ROLES:
            raise ValueError(f"Invalid role {role!r}. Must be one of {sorted(MEMBER_ROLES)}")
        email = email.strip().lower()
        async with self._lock:
            self.invites[email] = role
            await self._emit(RoomInviteCreatedEvent(
                **self._event_fields(invited_by), email=email, role=role, invited_by=invited_by,
            ))

    async def revoke_invite(self, email: str, user_id: str, accepted: bool = False) -> bool:
        """End a pending invite. False if there was none for this email."""
        email = email.strip().lower()
        async with self._lock:
            if self.invites.pop(email, None) is None:
                return False
            await self._emit(RoomInviteRevokedEvent(**self._event_fields(user_id), email=email, accepted=accepted))
            return True

    async def set_password(self, password_hash: Optional[str], user_id: str) -> None:
        """Set or (with None) remove the room password. Takes the hash, never the password."""
        async with self._lock:
            self.password_hash = password_hash
            await self._emit(RoomPasswordSetEvent(**self._event_fields(user_id), password_hash=password_hash))

    async def save_mcp_server(self, name: str, url: str, headers: dict[str, str], tools: list[dict[str, Any]],
                              settings: dict[str, dict[str, Any]], user_id: str) -> None:
        """Add or replace one of the room's MCP servers. `headers` values must already be encrypted."""
        async with self._lock:
            self.mcp_servers[name] = {"url": url, "headers": headers, "tools": tools, "settings": settings}
            await self._emit(RoomMcpServerSavedEvent(
                **self._event_fields(user_id), name=name, url=url, headers=headers, tools=tools, settings=settings,
            ))

    async def remove_mcp_server(self, name: str, user_id: str) -> bool:
        async with self._lock:
            if self.mcp_servers.pop(name, None) is None:
                return False
            await self._emit(RoomMcpServerRemovedEvent(**self._event_fields(user_id), name=name))
            return True

    async def set_mcp_admin(self, name: str, enabled: bool, settings: dict[str, dict[str, Any]], user_id: str) -> None:
        async with self._lock:
            self.mcp_admin[name] = {"enabled": enabled, "settings": settings}
            await self._emit(RoomMcpAdminToggledEvent(
                **self._event_fields(user_id), name=name, enabled=enabled, settings=settings,
            ))

    async def save_ai_settings(self, provider: str, base_url: str, api_key: str, models: dict[str, str],
                               user_id: str) -> None:
        """Set the room's AI provider. `api_key` must already be encrypted."""
        async with self._lock:
            event = RoomAiSettingsSavedEvent(
                **self._event_fields(user_id), provider=provider, base_url=base_url, api_key=api_key, models=models,
            )
            self.ai_settings = {"provider": provider, "base_url": base_url, "api_key": api_key, "models": models,
                                "version": str(event.id)}
            await self._emit(event)

    async def clear_ai_settings(self, user_id: str) -> bool:
        async with self._lock:
            if self.ai_settings is None:
                return False
            self.ai_settings = None
            await self._emit(RoomAiSettingsClearedEvent(**self._event_fields(user_id)))
            return True

    async def set_skills(self, enabled: list[str], user_id: str) -> None:
        async with self._lock:
            names = sorted(set(enabled))
            self.skills_enabled = set(names)
            await self._emit(RoomSkillsSetEvent(**self._event_fields(user_id), enabled=names))

    async def request_kickoff(self, user_id: str, brief: Optional[str] = None) -> None:
        """Ask the room's runtime to plan the room with the team (rooms/runtime.py _kickoff)."""
        async with self._lock:
            await self._emit(KickoffRequestedEvent(**self._event_fields(user_id), requested_by=user_id, brief=brief))

    async def close_room(self, user_id: str, reason: Optional[str] = None) -> None:
        async with self._lock:
            self.closed = True
            await self._emit(RoomClosedEvent(
                **self._event_fields(user_id),
                closed_by=user_id,
                reason=reason,
            ))

    # ---- User lifecycle ----

    async def user_join(
        self,
        user_id: str,
        user_name: Optional[str] = None,
        avatar_url: Optional[str] = None,
    ) -> UserPresence:
        """User joins the room."""
        async with self._lock:
            presence = await self.presence.join(user_id, user_name, avatar_url)
            await self.sitting.record_activity(user_id)
            event = UserJoinedEvent(
                id=uuid4(),
                type=EventType.USER_JOINED,
                room_id=self.room_id,
                user_id=user_id,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                user_name=user_name,
            )
            await self._emit(event)
        return presence

    async def user_leave(self, user_id: str) -> Optional[UserPresence]:
        """User leaves the room."""
        async with self._lock:
            removed = await self.presence.leave(user_id)
            presence = await self.presence.get(user_id)
            if presence is not None:
                # Another tab is still connected, so the user remains present in the room.
                return presence
            await self.sitting.record_leave(user_id)
            event = UserLeftEvent(
                id=uuid4(),
                type=EventType.USER_LEFT,
                room_id=self.room_id,
                user_id=user_id,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
            )
            await self._emit(event)
        return removed

    # ---- Presence updates ----

    async def set_typing(self, user_id: str, typing: bool) -> bool:
        async with self._lock:
            result = await self.presence.set_typing(user_id, typing)
            if result:
                event = UserTypingEvent(
                    id=uuid4(),
                    type=EventType.USER_TYPING,
                    room_id=self.room_id,
                    user_id=user_id,
                    timestamp=datetime.now(timezone.utc),
                    sequence=self._next_sequence(),
                    prev_event_id=None,
                    is_typing=typing,
                )
                await self._emit(event)
        return result

    async def set_active_tab(self, user_id: str, tab: Optional[str]) -> bool:
        async with self._lock:
            return await self.presence.set_active_tab(user_id, tab)

    async def set_cursor(self, user_id: str, position: Optional[CursorPosition]) -> bool:
        async with self._lock:
            return await self.presence.set_cursor(user_id, position)

    async def set_presence_status(self, user_id: str, status: str) -> bool:
        async with self._lock:
            result = await self.presence.set_status(user_id, status)
            if result:
                event = UserPresenceChangedEvent(
                    id=uuid4(),
                    type=EventType.USER_PRESENCE_CHANGED,
                    room_id=self.room_id,
                    user_id=user_id,
                    timestamp=datetime.now(timezone.utc),
                    sequence=self._next_sequence(),
                    prev_event_id=None,
                    presence=status,
                )
                await self._emit(event)
        return result

    # ---- Plan operations ----

    async def draft_plan(self, items: list[dict], user_id: str) -> list[dict]:
        async with self._lock:
            new_items = await self.plan.draft(items)
            event = PlanCreatedEvent(
                id=uuid4(),
                type=EventType.PLAN_CREATED,
                room_id=self.room_id,
                user_id=user_id,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                plan=new_items,
                created_by=user_id,
            )
            await self._emit(event)
        return new_items

    async def replace_plan(self, items: list[dict], user_id: str) -> list[dict]:
        async with self._lock:
            old = {i["id"]: i for i in await self.plan.get_items()}
            normalized = []
            for item in items:
                previous = old.get(item.get("id", ""))
                item = self._bind_requesters(previous, item, user_id)
                if previous is None or any(item.get(key) != previous.get(key) for key in ("title", "notes", "owner_role")):
                    # A new or materially changed task cannot inherit an approved status from the client.
                    item["status"] = "draft"
                else:
                    # Preserve execution state only for an unchanged task; clients cannot approve drafts.
                    item["status"] = previous.get("status", "draft")
                normalized.append(item)
            items = normalized
            new_items = await self.plan.replace(items)
            event = PlanUpdatedEvent(
                id=uuid4(),
                type=EventType.PLAN_UPDATED,
                room_id=self.room_id,
                user_id=user_id,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                plan=new_items,
                updated_by=user_id,
            )
            await self._emit(event)
        return new_items

    async def reorder_plan(self, item_ids: list[str], user_id: str) -> list[dict]:
        """Reorder draft and upcoming tasks in their existing slots, preserving all other task state."""
        async with self._lock:
            current = await self.plan.get_items()
            movable = [item for item in current if item.get("status") in ("draft", "todo")]
            movable_ids = [item["id"] for item in movable]
            if len(item_ids) != len(set(item_ids)) or set(item_ids) != set(movable_ids):
                raise ValueError("The task list changed. Refresh the plan and try again")
            by_id = {item["id"]: item for item in movable}
            ordered = iter(by_id[item_id] for item_id in item_ids)
            new_items = [next(ordered) if item.get("status") in ("draft", "todo") else item for item in current]
            if new_items == current:
                return current
            new_items = await self.plan.replace(new_items)
            event = PlanUpdatedEvent(
                id=uuid4(),
                type=EventType.PLAN_UPDATED,
                room_id=self.room_id,
                user_id=user_id,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                plan=new_items,
                updated_by=user_id,
            )
            await self._emit(event)
            return new_items

    async def add_plan_item(self, item: dict, user_id: str) -> dict:
        async with self._lock:
            # Agents pick ids from a plan they read earlier; another add may have taken it since (a chat task
            # while the kickoff drafts). Take the next free id instead of failing and losing the item.
            taken = {i["id"] for i in await self.plan.get_items()}
            if item.get("id") in taken:
                numbers = [int(i[1:]) for i in taken if i[:1] == "t" and i[1:].isdigit()]
                item = {**item, "id": f"t{max(numbers, default=0) + 1}"}
            added = await self.plan.add_item(item)
            items_list = await self.plan.get_items()
            position = len(items_list) - 1
            event = PlanItemAddedEvent(
                id=uuid4(),
                type=EventType.PLAN_ITEM_ADDED,
                room_id=self.room_id,
                user_id=user_id,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                item_id=added["id"],
                title=added["title"],
                description=added.get("notes"),
                position=position,
                added_by=user_id,
                # Full item so replay restores status/owner_role/notes exactly
                metadata={"item": added},
            )
            await self._emit(event)

            # Emit TaskStartedEvent if item is added with status "doing"
            if added.get("status") == "doing":
                task_started = TaskStartedEvent(
                    id=uuid4(),
                    type=EventType.TASK_STARTED,
                    room_id=self.room_id,
                    user_id=user_id,
                    timestamp=datetime.now(timezone.utc),
                    sequence=self._next_sequence(),
                    prev_event_id=None,
                    item_id=added["id"],
                    title=added["title"],
                    started_by=user_id,
                )
                await self._emit(task_started)
        return added

    async def update_plan_item(self, item_id: str, changes: dict, user_id: str) -> dict:
        async with self._lock:
            # Get old item to check status change
            items = await self.plan.get_items()
            old_item = next((item for item in items if item["id"] == item_id), None)
            old_status = old_item.get("status") if old_item else None

            updated = await self.plan.update_item(item_id, changes)
            event = PlanItemUpdatedEvent(
                id=uuid4(),
                type=EventType.PLAN_ITEM_UPDATED,
                room_id=self.room_id,
                user_id=user_id,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                item_id=item_id,
                updates=changes,
                updated_by=user_id,
            )
            await self._emit(event)

            # Emit TaskStartedEvent if status changed to "doing"
            new_status = updated.get("status")
            if new_status == "doing" and old_status != "doing":
                task_started = TaskStartedEvent(
                    id=uuid4(),
                    type=EventType.TASK_STARTED,
                    room_id=self.room_id,
                    user_id=user_id,
                    timestamp=datetime.now(timezone.utc),
                    sequence=self._next_sequence(),
                    prev_event_id=None,
                    item_id=item_id,
                    title=updated["title"],
                    started_by=user_id,
                )
                await self._emit(task_started)

            # Emit TaskFinishedEvent if status changed to "done"
            if new_status == "done" and old_status != "done":
                task_finished = TaskFinishedEvent(
                    id=uuid4(),
                    type=EventType.TASK_FINISHED,
                    room_id=self.room_id,
                    user_id=user_id,
                    timestamp=datetime.now(timezone.utc),
                    sequence=self._next_sequence(),
                    prev_event_id=None,
                    item_id=item_id,
                    title=updated["title"],
                    finished_by=user_id,
                )
                await self._emit(task_finished)
        return updated

    async def remove_plan_item(self, item_id: str, user_id: str) -> dict:
        async with self._lock:
            removed = await self.plan.remove_item(item_id)
            event = PlanItemRemovedEvent(
                id=uuid4(),
                type=EventType.PLAN_ITEM_REMOVED,
                room_id=self.room_id,
                user_id=user_id,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                item_id=item_id,
                removed_by=user_id,
            )
            await self._emit(event)
        return removed

    async def complete_plan_item(self, item_id: str, user_id: str) -> dict:
        async with self._lock:
            completed = await self.plan.complete_item(item_id)
            now = datetime.now(timezone.utc)
            event = PlanItemCompletedEvent(
                id=uuid4(),
                type=EventType.PLAN_ITEM_COMPLETED,
                room_id=self.room_id,
                user_id=user_id,
                timestamp=now,
                sequence=self._next_sequence(),
                prev_event_id=None,
                item_id=item_id,
                completed_by=user_id,
                completed_at=now,
            )
            await self._emit(event)

            # Emit TaskFinishedEvent
            task_finished = TaskFinishedEvent(
                id=uuid4(),
                type=EventType.TASK_FINISHED,
                room_id=self.room_id,
                user_id=user_id,
                timestamp=now,
                sequence=self._next_sequence(),
                prev_event_id=None,
                item_id=item_id,
                title=completed["title"],
                finished_by=user_id,
            )
            await self._emit(task_finished)
        return completed

    async def approve_plan_items(self, item_ids: list[str], user_id: str) -> list[dict]:
        async with self._lock:
            items = await self.plan.approve_items(item_ids)
            event = CommandApprovePlanEvent(
                id=uuid4(),
                type=EventType.COMMAND_APPROVE_PLAN,
                room_id=self.room_id,
                user_id=user_id,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                plan_item_ids=item_ids,
                issued_by=user_id,
                approval_note=None,
            )
            await self._emit(event)
        return items

    # ---- Message / Inbox ----

    async def add_message(
        self,
        label: str,
        content: str,
        *,
        message_id: Optional[str] = None,
        user_id: Optional[str] = None,
        rationale: Optional[str] = None,
        domain: Optional[str] = None,
        to: Optional[str] = None,
        enqueue: bool = True,
        user_name: Optional[str] = None,
        attachments: Optional[list[str]] = None,
    ) -> None:
        """Post a message and add it to the inbox (coordinator labels: merge, queue, interrupt, conflict, chat).

        `to="team"` is a note between people: it is posted to the room but never reaches the coder's inbox.
        `enqueue=False` only posts it; the room runtime enqueues it after the coordinator labels it.
        """
        async with self._lock:
            if enqueue and to != "team":
                await self.inbox.add(label, content, message_id=message_id, user_id=user_id, rationale=rationale, domain=domain)
            if user_id and user_name:
                self.member_names[user_id] = user_name
            event = UserMessageSentEvent(
                id=uuid4(),
                type=EventType.USER_MESSAGE_SENT,
                room_id=self.room_id,
                user_id=user_id or "system",
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                message_id=message_id or str(uuid4()),
                content=content,
                user_name=user_name or (self.member_names.get(user_id) if user_id else None),
                reply_to=None,
                to=to,
                attachments=attachments or [],
            )
            await self._emit(event)
            self.message_ids.add(event.message_id)

    async def toggle_reaction(self, message_id: str, user_id: str, emoji: str) -> bool:
        """Add or remove `user_id`'s `emoji` on a person's message; returns whether it is now on.

        Raises KeyError for a message id that isn't a person's message in this room.
        """
        async with self._lock:
            if message_id not in self.message_ids:
                raise KeyError(message_id)
            users = self.reactions.setdefault(message_id, {}).setdefault(emoji, set())
            added = user_id not in users
            if added:
                users.add(user_id)
            else:
                users.discard(user_id)
            await self._emit(MessageReactionToggledEvent(
                **self._event_fields(user_id), message_id=message_id, emoji=emoji, added=added,
            ))
            return added

    async def enqueue_message(
        self, label: str, content: str, *, message_id: Optional[str] = None, user_id: Optional[str] = None,
        rationale: Optional[str] = None, domain: Optional[str] = None,
    ) -> None:
        """Put an already-posted message in the coder's inbox with its coordinator label (no event)."""
        async with self._lock:
            await self.inbox.add(label, content, message_id=message_id, user_id=user_id, rationale=rationale, domain=domain)

    async def post_notice(self, kind: str, data: dict, user_id: str = "coordinator") -> None:
        """Store agent output for the feed (message.labeled, coordinator.reply, tool.called, ...)."""
        async with self._lock:
            await self._emit(AgentNoticeEvent(**self._event_fields(user_id), kind=kind, data=data))

    async def drain_inbox(self) -> tuple[list[str], list[str], bool]:
        """Drain inbox at turn boundary. Returns (merges, edit_notes, interrupt)."""
        async with self._lock:
            return await self.inbox.drain()

    # ---- File operations ----

    async def create_file(self, path: str, content: str, user_id: str) -> str:
        async with self._lock:
            return await self._create_file_unlocked(path, content, user_id)

    async def _create_file_unlocked(self, path: str, content: str, user_id: str) -> str:
        file_id = str(uuid4())
        content_hash = await self.files.write(path, content)
        # Infer file type from extension
        file_type = mimetypes.guess_type(path)[0]
        self.manifest.add(file_id, path, len(content.encode()), hash=content_hash, file_type=file_type)
        event = FileCreatedEvent(
            id=uuid4(),
            type=EventType.FILE_CREATED,
            room_id=self.room_id,
            user_id=user_id,
            timestamp=datetime.now(timezone.utc),
            sequence=self._next_sequence(),
            prev_event_id=None,
            file_id=file_id,
            path=path,
            name=path.split("/")[-1],
            content=content,
            file_type=file_type,
            size=len(content.encode()),
            created_by=user_id,
            hash=content_hash,
            version=self._version_of(path),
        )
        await self._emit(event)
        return file_id

    async def update_file(self, path: str, content: str, user_id: str) -> None:
        async with self._lock:
            await self._update_file_unlocked(path, content, user_id)

    async def _update_file_unlocked(self, path: str, content: str, user_id: str) -> None:
        old_content = await self.files.read(path)
        content_hash = await self.files.write(path, content)
        file_id = self.manifest.get_id(path)
        if file_id is None:
            file_id = str(uuid4())
            file_type = mimetypes.guess_type(path)[0]
            self.manifest.add(file_id, path, len(content.encode()), hash=content_hash, file_type=file_type)
        else:
            self.manifest.update(path, len(content.encode()), hash=content_hash)
        event = FileUpdatedEvent(
            id=uuid4(),
            type=EventType.FILE_UPDATED,
            room_id=self.room_id,
            user_id=user_id,
            timestamp=datetime.now(timezone.utc),
            sequence=self._next_sequence(),
            prev_event_id=None,
            file_id=file_id,
            path=path,
            content=content,
            content_delta={"old": old_content, "new": content} if old_content != content else None,
            size=len(content.encode()),
            updated_by=user_id,
            hash=content_hash,
            version=self._version_of(path),
        )
        await self._emit(event)

    def _version_of(self, path: str) -> int:
        entry = self.manifest.get_entry(path)
        return entry.version if entry else 0

    async def file_version(self, path: str) -> Optional[int]:
        """Current version of a file, or None if it doesn't exist."""
        async with self._lock:
            entry = self.manifest.get_entry(path)
            return entry.version if entry else None

    async def delete_file(self, path: str, user_id: str) -> None:
        async with self._lock:
            await self._delete_file_unlocked(path, user_id)

    async def _delete_file_unlocked(self, path: str, user_id: str) -> None:
        file_id = self.manifest.get_id(path) or ""
        await self.files.delete(path)
        self.manifest.remove(path)
        event = FileDeletedEvent(
            id=uuid4(),
            type=EventType.FILE_DELETED,
            room_id=self.room_id,
            user_id=user_id,
            timestamp=datetime.now(timezone.utc),
            sequence=self._next_sequence(),
            prev_event_id=None,
            file_id=file_id,
            path=path,
            deleted_by=user_id,
        )
        await self._emit(event)

    def _path_clash(self, path: str) -> Optional[str]:
        """Why a file can't be saved at `path` because of the room's folders, or None when it can."""
        paths = self.manifest.list_paths()
        if not path or path.endswith("/"):
            return "a file path needs a file name"
        if any(p.startswith(f"{path}/") for p in paths):
            return f"{path} is a folder"
        parts = path.split("/")
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth])
            if parent in paths:
                return f"{parent} is a file, so it can't contain {path}"
        return None

    async def save_checked(
        self, path: str, content: Optional[str], base_version: Optional[int], user_id: str,
        on_behalf_of: Iterable[str] = (), task_id: Optional[str] = None,
    ) -> tuple[str, Optional[int]]:
        """Version-checked save, atomic against every other change to the room.

        `content=None` deletes. `base_version` is the version the edit started from (None or 0: the file
        must not exist yet). Returns ("ok", new version), ("stale", current version) or ("locked", None)
        when someone else holds the file's soft lock. Raises PathConflictError when the path clashes with a
        folder (saving "src" while "src/App.tsx" exists, or "a.txt/b" while "a.txt" is a file).

        A change outside the person's folders (mux/rooms/file_access.py) is stored as a change request and
        raises ChangeHeld. For the coder, `on_behalf_of` is everyone whose requests shaped its task: the change
        must suit all of their folders. With nobody (a task the whole room agreed on) it may change any file.
        Raises InvalidPath for paths that would leave the project or dodge a folder ("server/../web/a.ts").
        """
        check_path(path)
        async with self._lock:
            if content is not None and (clash := self._path_clash(path)):
                raise PathConflictError(clash)
            holder = await self.locks.locked_by(path)
            if holder and holder != user_id:
                return "locked", None
            current = self.manifest.get_entry(path)
            current_version = current.version if current else None
            if (base_version or None) != current_version:
                return "stale", current_version
            people = list(on_behalf_of) if user_id == AGENT else [user_id]
            subject = next((p for p in people if not self.access.can_change(p, path, self.owner_id)), None)
            if subject:
                # The agent editing a file it already sent for approval in this task adds to that one request
                request = self.held_change(task_id, path) if user_id == AGENT else None
                if request is not None and request.requested_by == subject:
                    request.content = content
                else:
                    request = ChangeRequest(
                        id=str(uuid4()), path=path, content=content, base_version=current_version, requested_by=subject,
                        via="agent" if user_id == AGENT else "person", task_id=task_id,
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                    self.change_requests[request.id] = request
                await self._emit(ChangeRequestedEvent(
                    **self._event_fields(user_id), request_id=request.id, path=path, content=content,
                    base_version=request.base_version, requested_by=subject, via=request.via, task_id=task_id,
                    approvers=sorted(self.change_approvers(path)),
                ))
                raise ChangeHeld(request)
            if content is None:
                if current is not None:
                    await self._delete_file_unlocked(path, user_id)
                return "ok", None
            if current is None:
                await self._create_file_unlocked(path, content, user_id)
            else:
                await self._update_file_unlocked(path, content, user_id)
            return "ok", self._version_of(path)

    def can_change(self, user_id: str, path: str) -> bool:
        """Whether `user_id` may change `path` directly (False: it would go for approval)."""
        return self.access.can_change(user_id, path, self.owner_id)

    def change_approvers(self, path: str) -> set[str]:
        """The owner plus every coder whose folders hold `path`."""
        editors = [u for u, role in self.members.items() if role == "editor"]
        return self.access.approvers(path, self.owner_id, editors)

    def pending_changes(self) -> list[ChangeRequest]:
        return [r for r in self.change_requests.values() if r.status == "pending"]

    def listed_changes(self, closed: int = 20) -> list[ChangeRequest]:
        """Pending changes, plus the latest rejected or stale ones so their requester can still get their edit."""
        done = [r for r in self.change_requests.values() if r.status in ("rejected", "stale")]
        return self.pending_changes() + done[-closed:]

    def held_change(self, task_id: Optional[str], path: str) -> Optional[ChangeRequest]:
        """The agent's pending change to `path` in task `task_id`, if it sent one for approval."""
        if task_id is None:
            return None
        return next((r for r in self.pending_changes() if r.via == "agent" and r.task_id == task_id and r.path == path), None)

    def _bind_requesters(self, old: Optional[dict], item: dict, user_id: str) -> dict:
        """Who a plan task is for is the server's to say: a client's `requested_by` is ignored. A task a
        restricted coder adds or changes is bound to their folders too, so editing the plan never lifts a limit."""
        item = {k: v for k, v in item.items() if k != "requested_by"}
        people = set((old or {}).get("requested_by") or [])
        changed = old is None or any(item.get(k) != old.get(k) for k in item)
        if changed and self.access.is_restricted(user_id, self.owner_id):
            people.add(user_id)
        if people:
            item["requested_by"] = sorted(people)
        return item

    async def decide_change_request(self, request_id: str, user_id: str, approve: bool) -> ChangeRequest:
        """Approve (apply it, unless the file moved on: then it is stale) or reject a pending change."""
        async with self._lock:
            request = self.change_requests[request_id]
            is_editor = user_id == self.owner_id or self.members.get(user_id) == "editor"
            if request.status != "pending" or not is_editor or not self.access.can_approve(user_id, request.path, self.owner_id):
                raise PermissionError("You can't decide this change request")
            status = "rejected"
            if approve:
                entry = self.manifest.get_entry(request.path)
                if (entry.version if entry else None) != request.base_version or (
                        request.content is not None and self._path_clash(request.path)):
                    status = "stale"
                else:
                    status = "approved"
                    if request.content is None:
                        if entry is not None:
                            await self._delete_file_unlocked(request.path, request.requested_by)
                    elif entry is None:
                        await self._create_file_unlocked(request.path, request.content, request.requested_by)
                    else:
                        await self._update_file_unlocked(request.path, request.content, request.requested_by)
            return await self._close_change_request(request, status, user_id)

    async def withdraw_change_request(self, request_id: str, user_id: str) -> ChangeRequest:
        """The person a pending change is for takes it back."""
        async with self._lock:
            request = self.change_requests[request_id]
            if request.status != "pending" or request.requested_by != user_id:
                raise PermissionError("Only the person who asked can withdraw a pending change")
            return await self._close_change_request(request, "withdrawn", user_id)

    async def _close_change_request(self, request: ChangeRequest, status: str, user_id: str) -> ChangeRequest:
        request.status = cast(Any, status)
        request.decided_by = user_id
        await self._emit(ChangeRequestDecidedEvent(
            **self._event_fields(user_id), request_id=request.id, status=status, decided_by=user_id,
        ))
        return request

    # ---- Lock operations ----

    async def lock_file(self, path: str, user_id: str) -> bool:
        async with self._lock:
            return await self.locks.lock(path, user_id)

    async def unlock_file(self, path: str, user_id: str) -> bool:
        async with self._lock:
            return await self.locks.unlock(path, user_id)

    async def is_locked(self, path: str) -> bool:
        async with self._lock:
            return await self.locks.is_locked(path)

    async def locked_by(self, path: str) -> Optional[str]:
        async with self._lock:
            return await self.locks.locked_by(path)

    # ---- Budget operations ----

    async def record_tokens(self, delta: int, user_id: str) -> bool:
        async with self._lock:
            return await self.budget.record_tokens(delta, user_id)

    async def record_sandbox_run(self, user_id: str) -> bool:
        async with self._lock:
            return await self.budget.record_sandbox_run(user_id)

    async def check_budget_allowance(self, estimated_tokens: int = 0, estimated_runs: int = 0) -> bool:
        async with self._lock:
            return await self.budget.check_allowance(estimated_tokens, estimated_runs)

    async def raise_budget_caps(self, user_id: str, token_cap: Optional[int] = None, sandbox_run_cap: Optional[int] = None) -> bool:
        async with self._lock:
            return await self.budget.raise_caps(user_id, token_cap, sandbox_run_cap)

    async def force_budget_resume(self, user_id: str) -> bool:
        async with self._lock:
            return await self.budget.force_resume(user_id)

    # ---- Queries ----

    async def get_plan(self) -> list[dict]:
        async with self._lock:
            return await self.plan.get_items()

    async def get_presence(self, user_id: str) -> Optional[UserPresence]:
        async with self._lock:
            return await self.presence.get(user_id)

    async def get_all_presence(self) -> dict[str, UserPresenceDict]:
        async with self._lock:
            return await self.presence.get_all()

    async def get_budget_status(self) -> BudgetStatus:
        async with self._lock:
            return await self.budget.get_status()

    async def get_sitting_status(self) -> SittingStatus:
        async with self._lock:
            return await self.sitting.get_status()

    async def get_file(self, path: str) -> Optional[str]:
        async with self._lock:
            if path not in self.manifest:
                return None
            try:
                return await self.files.read(path)
            except FileNotFoundError:
                return None

    async def list_files(self) -> list[str]:
        async with self._lock:
            return self.manifest.list_paths()

    # ---- Checkpoint / Rewind ----

    async def create_checkpoint(self, user_id: str, description: Optional[str] = None, include_files: bool = True, include_plan: bool = True) -> str:
        """Create a checkpoint of current room state."""
        checkpoint_id = str(uuid4())
        async with self._lock:
            checkpoint_data = await self._snapshot(
                checkpoint_id, description, user_id, include_files, include_plan,
                sequence=self._state.sequence, created_at=time.time(),
                parent_id=self.manifest.active_checkpoint_id,
            )
            self.manifest.add_checkpoint(checkpoint_id, checkpoint_data)

            event = CheckpointCreatedEvent(
                **self._event_fields(user_id),
                checkpoint_id=checkpoint_id,
                description=description,
                created_by=user_id,
                includes_files=include_files,
                includes_plan=include_plan,
                parent_id=checkpoint_data["parent_id"],
            )
            await self._emit(event)
        return checkpoint_id

    async def _snapshot(
        self,
        checkpoint_id: str,
        description: Optional[str],
        user_id: str,
        include_files: bool,
        include_plan: bool,
        *,
        sequence: int,
        created_at: float,
        parent_id: Optional[str] = None,
    ) -> dict:
        """Capture plan/files as checkpoint data (caller holds the lock)."""
        return {
            "checkpoint_id": checkpoint_id,
            "description": description,
            "sequence": sequence,
            "plan": await self.plan.get_items() if include_plan else [],
            "files": {path: await self.files.read(path) for path in self.manifest.list_paths()} if include_files else {},
            "includes_files": include_files,
            "includes_plan": include_plan,
            "created_at": created_at,
            "created_by": user_id,
            "parent_id": parent_id,
        }

    async def _restore_snapshot(self, checkpoint_data: dict) -> None:
        """Replace plan and/or files with a checkpoint's contents (caller holds the lock)."""
        if checkpoint_data.get("includes_plan", True):
            await self.plan.replace(checkpoint_data.get("plan") or [])
        if checkpoint_data.get("includes_files", True):
            files = checkpoint_data.get("files") or {}
            # Remove files created after the checkpoint, then write the saved ones
            await self.files.clear()
            for path, content in files.items():
                await self.files.write(path, content)
            self.manifest.rebuild_from_checkpoint(files)

    async def restore_checkpoint(self, checkpoint_id: str, user_id: str) -> bool:
        """Restore room state from a checkpoint."""
        async with self._lock:
            checkpoint_data = self.manifest.get_checkpoint(checkpoint_id)
            if not checkpoint_data:
                return False

            await self._restore_snapshot(checkpoint_data)
            self.manifest.set_active_checkpoint(checkpoint_id)

            event = CheckpointRestoredEvent(
                **self._event_fields(user_id),
                checkpoint_id=checkpoint_id,
                restored_by=user_id,
                restore_point=checkpoint_data.get("sequence", 0),
                plan=await self.plan.get_items(),
                files={entry.path: {"hash": entry.hash, "version": entry.version}
                       for entry in self.manifest.list_entries()},
            )
            await self._emit(event)
        return True

    async def rewind_to_sequence(self, target_sequence: int, user_id: str, reason: Optional[str] = None,
                                 preserve_checkpoint: bool = False,
                                 target_checkpoint_id: Optional[str] = None) -> Optional[str]:
        """Rewind the plan and files to their state as of a specific event sequence.

        Presence, membership, budget usage, sitting state and checkpoints are kept:
        rewinding the document must not refund spent budget or drop connected users.
        """
        initial_events = await self.event_log.get_all(self.room_id)
        if not initial_events or target_sequence < 0 or target_sequence > initial_events[-1].sequence:
            return None
        return_checkpoint_id = None
        if preserve_checkpoint:
            return_checkpoint_id = await self.create_checkpoint(
                user_id, f"Saved present before rewind at sequence {self._state.sequence}")

        async with self._lock:
            events = await self.event_log.get_all(self.room_id)
            if not events or target_sequence < 0 or target_sequence > events[-1].sequence:
                return None

            if target_checkpoint_id not in self.manifest.list_checkpoints():
                target_checkpoint_id = max(
                    ((cid, data) for cid, data in self.manifest.list_checkpoints().items()
                     if data.get("sequence", -1) <= target_sequence),
                    key=lambda pair: pair[1].get("sequence", -1), default=(None, {}),
                )[0]

            await self._reset_document_state()
            await self._replay_unlocked(
                [e for e in events if e.sequence <= target_sequence],
                include_session_state=False,
                record_checkpoints=False,
            )
            self.manifest.set_active_checkpoint(target_checkpoint_id)

            event = CommandRewindEvent(
                **self._event_fields(user_id),
                target_sequence=target_sequence,
                issued_by=user_id,
                reason=reason,
                preserve_checkpoint=preserve_checkpoint,
                target_checkpoint_id=target_checkpoint_id,
                return_checkpoint_id=return_checkpoint_id,
                plan=await self.plan.get_items(),
                files={entry.path: {"hash": entry.hash, "version": entry.version}
                       for entry in self.manifest.list_entries()},
            )
            await self._emit(event)
        return return_checkpoint_id or "rewound"

    async def subscribe_since(self, since: int, subscribe: Callable[[list[BaseEvent]], Awaitable[None]]) -> None:
        """Read the events after `since` and run `subscribe(events)` while no new event can be emitted.

        A WebSocket sends them as its initial dump and registers for live events inside `subscribe`,
        so no event falls between the two.
        """
        async with self._lock:
            await subscribe(await self.event_log.get_since(self.room_id, since))

    @property
    def sequence(self) -> int:
        """The room's latest event sequence."""
        return self._state.sequence

    # ---- Replay (rehydration and rewind share one implementation) ----

    async def _reset_document_state(self) -> None:
        """Clear plan and files. Keeps checkpoints, export history and room metadata."""
        self.plan = Plan()
        await self.files.clear()
        self.manifest.reset_files()

    async def replay(self, events: Iterable[BaseEvent]) -> None:
        """Rebuild state from the full event log (used by the registry on rehydration)."""
        async with self._lock:
            events = list(events)
            await self._replay_unlocked(events, include_session_state=True, record_checkpoints=True)
            if events:
                self._state.sequence = events[-1].sequence
                self._last_event_id = str(events[-1].id)

    async def _replay_unlocked(
        self,
        events: list[BaseEvent],
        *,
        include_session_state: bool,
        record_checkpoints: bool,
    ) -> None:
        """Apply events in order. A COMMAND_REWIND resets the document and re-applies
        only the events at or before its target, exactly like the live rewind did."""
        applied: list[BaseEvent] = []
        for event in events:
            if event.type == EventType.COMMAND_REWIND:
                rewind = cast(CommandRewindEvent, event)
                target = rewind.target_sequence
                applied = [e for e in applied if e.sequence <= target]
                await self._reset_document_state()
                for earlier in applied:
                    await self._apply_event(earlier, include_session_state=False)
                target_checkpoint_id = rewind.target_checkpoint_id
                if target_checkpoint_id not in self.manifest.list_checkpoints():
                    target_checkpoint_id = max(
                        ((cid, data) for cid, data in self.manifest.list_checkpoints().items()
                         if data.get("sequence", -1) <= target),
                        key=lambda pair: pair[1].get("sequence", -1), default=(None, {}),
                    )[0]
                self.manifest.set_active_checkpoint(target_checkpoint_id)
                continue

            if event.type == EventType.CHECKPOINT_CREATED and record_checkpoints:
                cp = cast(CheckpointCreatedEvent, event)
                # Checkpoint contents are not in the log; rebuild them from replayed state
                if not self.manifest.get_checkpoint(cp.checkpoint_id):
                    data = await self._snapshot(
                        cp.checkpoint_id, cp.description, cp.created_by,
                        cp.includes_files, cp.includes_plan,
                        sequence=applied[-1].sequence if applied else 0,
                        created_at=cp.timestamp.timestamp(),
                        # Older persisted events predate parent_id; infer their original linear parent
                        # from the active head while replaying so existing rooms gain branch history.
                        parent_id=cp.parent_id or self.manifest.active_checkpoint_id,
                    )
                    self.manifest.add_checkpoint(cp.checkpoint_id, data)

            await self._apply_event(event, include_session_state=include_session_state)
            applied.append(event)

    async def _apply_event(self, event: BaseEvent, *, include_session_state: bool) -> None:
        """Apply one event's effect on state (no events are emitted).

        Dispatches on `event.type`; each branch casts to the model class that type is stored as.
        """
        t = event.type
        try:
            # Room settings
            if t == EventType.ROOM_CREATED:
                e = cast(RoomCreatedEvent, event)
                self.manifest.set_room_metadata(name=e.room_name, description=e.room_description)
                self.created_at = e.timestamp
                if e.domain_role:
                    self.domain_roles[e.created_by] = e.domain_role
            elif t == EventType.ROOM_JOINED:
                e = cast(RoomJoinedEvent, event)
                self.members[e.user_id] = getattr(event, "role", "editor")
                if e.domain_role:
                    self.domain_roles[e.user_id] = e.domain_role
                if e.user_name:
                    self.member_names[e.user_id] = e.user_name
            elif t == EventType.USER_MESSAGE_SENT:
                e = cast(UserMessageSentEvent, event)
                if e.user_name:
                    self.member_names[e.user_id] = e.user_name
                self.message_ids.add(e.message_id)
            elif t == EventType.MESSAGE_REACTION_TOGGLED:
                # Chat state, not document state: a rewind re-applies earlier events with this False
                if include_session_state:
                    e = cast(MessageReactionToggledEvent, event)
                    users = self.reactions.setdefault(e.message_id, {}).setdefault(e.emoji, set())
                    if e.added:
                        users.add(e.user_id)
                    else:
                        users.discard(e.user_id)
            elif t == EventType.ROOM_SHARING_UPDATED:
                e = cast(RoomSharingUpdatedEvent, event)
                self.public = e.public
                self.allow_anonymous = e.allow_anonymous
                self.link_permission = e.link_permission
            # Who may change what is not document state: a rewind (which re-applies earlier events with this
            # False) must not roll the owner's access settings back
            elif t == EventType.CODER_ROLES_UPDATED:
                if include_session_state:
                    self.access.roles = dict(cast(CoderRolesUpdatedEvent, event).roles)
            elif t == EventType.MEMBER_ACCESS_UPDATED:
                if include_session_state:
                    e = cast(MemberAccessUpdatedEvent, event)
                    self._apply_member_access(e.member_id, e.coder_role, e.folders)
            elif t == EventType.CHANGE_REQUESTED:
                if include_session_state:
                    e = cast(ChangeRequestedEvent, event)
                    self.change_requests[e.request_id] = ChangeRequest(
                        id=e.request_id, path=e.path, content=e.content, base_version=e.base_version,
                        requested_by=e.requested_by, via=cast(Any, e.via), task_id=e.task_id,
                        created_at=e.timestamp.isoformat(),
                    )
            elif t == EventType.CHANGE_REQUEST_DECIDED:
                if include_session_state:
                    e = cast(ChangeRequestDecidedEvent, event)
                    if (request := self.change_requests.get(e.request_id)) is not None:
                        request.status = cast(Any, e.status)
                        request.decided_by = e.decided_by
            elif t == EventType.ROOM_INVITE_CREATED:
                e = cast(RoomInviteCreatedEvent, event)
                self.invites[e.email] = e.role
            elif t == EventType.ROOM_INVITE_REVOKED:
                self.invites.pop(cast(RoomInviteRevokedEvent, event).email, None)
            elif t == EventType.ROOM_PASSWORD_SET:
                self.password_hash = cast(RoomPasswordSetEvent, event).password_hash
            elif t == EventType.ROOM_MCP_SERVER_SAVED:
                e = cast(RoomMcpServerSavedEvent, event)
                self.mcp_servers[e.name] = {"url": e.url, "headers": e.headers, "tools": e.tools, "settings": e.settings}
            elif t == EventType.ROOM_MCP_SERVER_REMOVED:
                self.mcp_servers.pop(cast(RoomMcpServerRemovedEvent, event).name, None)
            elif t == EventType.ROOM_MCP_ADMIN_TOGGLED:
                e = cast(RoomMcpAdminToggledEvent, event)
                self.mcp_admin[e.name] = {"enabled": e.enabled, "settings": e.settings}
            elif t == EventType.ROOM_AI_SETTINGS_SAVED:
                e = cast(RoomAiSettingsSavedEvent, event)
                self.ai_settings = {"provider": e.provider, "base_url": e.base_url, "api_key": e.api_key,
                                    "models": e.models, "version": str(e.id)}
            elif t == EventType.ROOM_AI_SETTINGS_CLEARED:
                self.ai_settings = None
            elif t == EventType.ROOM_SKILLS_SET:
                self.skills_enabled = set(cast(RoomSkillsSetEvent, event).enabled)
            elif t == EventType.ROOM_CLOSED:
                self.closed = True

            # Plan
            elif t in (EventType.PLAN_CREATED, EventType.PLAN_UPDATED):
                e = cast(PlanCreatedEvent | PlanUpdatedEvent, event)
                await self.plan.replace(e.plan)
            elif t == EventType.COMMAND_OVERRIDE:
                e = cast(CommandOverrideEvent, event)
                await self.plan.replace(e.new_plan)
            elif t == EventType.PLAN_ITEM_ADDED:
                e = cast(PlanItemAddedEvent, event)
                item = (e.metadata or {}).get("item") or {
                    "id": e.item_id,
                    "title": e.title,
                    "notes": e.description,
                    "status": "draft",
                }
                await self.plan.add_item(item)
            elif t == EventType.PLAN_ITEM_UPDATED:
                e = cast(PlanItemUpdatedEvent, event)
                await self.plan.update_item(e.item_id, e.updates)
            elif t == EventType.PLAN_ITEM_REMOVED:
                e = cast(PlanItemRemovedEvent, event)
                await self.plan.remove_item(e.item_id)
            elif t == EventType.PLAN_ITEM_COMPLETED:
                e = cast(PlanItemCompletedEvent, event)
                await self.plan.complete_item(e.item_id)
            elif t == EventType.COMMAND_APPROVE_PLAN:
                e = cast(CommandApprovePlanEvent, event)
                await self.plan.approve_items(e.plan_item_ids)
            elif t == EventType.COMMAND_EDIT_PLAN:
                e = cast(CommandEditPlanEvent, event)
                await self.plan.replace(await self._plan_after_edits(e.edits))

            # Files
            elif t in (EventType.FILE_CREATED, EventType.FILE_UPDATED):
                e = cast(FileCreatedEvent | FileUpdatedEvent, event)
                content = e.content or ""
                content_hash = await self.files.write(e.path, content)
                file_id = self.manifest.get_id(e.path) or e.file_id
                self.manifest.add(file_id, e.path, e.size, hash=content_hash, file_type=mimetypes.guess_type(e.path)[0])
            elif t == EventType.FILE_DELETED:
                e = cast(FileDeletedEvent, event)
                await self.files.delete(e.path)
                self.manifest.remove(e.path)

            # Checkpoints
            elif t == EventType.CHECKPOINT_RESTORED:
                e = cast(CheckpointRestoredEvent, event)
                data = self.manifest.get_checkpoint(e.checkpoint_id)
                if data:
                    await self._restore_snapshot(data)
                    self.manifest.set_active_checkpoint(e.checkpoint_id)
                else:
                    logger.warning(f"Room {self.room_id}: checkpoint {e.checkpoint_id} missing during replay")

            # Session state (not rewound)
            elif include_session_state and t in (EventType.BUDGET_EXCEEDED, EventType.BUDGET_RESUMED):
                e = cast(BudgetExceededEvent, event)
                resumed = e.reason == "resumed"
                self.budget._state = BudgetState(
                    tokens_used=e.tokens_used,
                    sandbox_runs_used=e.sandbox_runs_used,
                    token_cap=e.token_cap,
                    sandbox_run_cap=e.sandbox_run_cap,
                    paused=not resumed,
                    paused_at=None if resumed else time.monotonic(),
                    paused_reason=None if resumed else e.reason,
                )
                self.budget._warned = self.budget._state.is_at_warning(self.config.budget_warning_threshold)
            elif include_session_state and t == EventType.SITTING_ENDED:
                e = cast(SittingEndedEvent, event)
                self.sitting._active = False
                self.sitting._owner_ended = e.reason == "owner_ended"

            # Presence is ephemeral and messages are transient queue entries: not replayed.
        except Exception:
            logger.exception(f"Room {self.room_id}: failed to apply {t} (seq {event.sequence}) during replay")
            raise

    async def _plan_after_edits(self, edits: list[dict]) -> list[dict]:
        """Apply plan edits to a scratch copy and return the result (atomic: all or nothing)."""
        scratch = Plan(await self.plan.get_items())
        for edit in edits:
            edit_type = edit.get("type")
            if edit_type not in _PLAN_EDIT_TYPES:
                raise ValueError(f"Invalid edit type {edit_type!r}. Must be one of {sorted(_PLAN_EDIT_TYPES)}")
            try:
                if edit_type == "add":
                    await scratch.add_item(edit["item"])
                elif edit_type == "update":
                    await scratch.update_item(edit["item_id"], edit["changes"])
                elif edit_type == "remove":
                    await scratch.remove_item(edit["item_id"])
                elif edit_type == "complete":
                    await scratch.complete_item(edit["item_id"])
            except KeyError as e:
                raise ValueError(f"Plan edit of type {edit_type!r} is missing field {e}") from None
        return await scratch.get_items()

    # ---- Conflict Detection / Resolution ----

    async def detect_conflict(
        self, message_ids: list[str], conflict_type: str, description: str, detected_by: str,
        resolution_deadline: Optional[datetime] = None, *, options: Optional[list[str]] = None, task_id: Optional[str] = None,
    ) -> str:
        """Detect and record a conflict between messages."""
        conflict_id = str(uuid4())
        async with self._lock:
            event = ConflictDetectedEvent(
                id=uuid4(),
                type=EventType.CONFLICT_DETECTED,
                room_id=self.room_id,
                user_id=detected_by,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                conflict_id=conflict_id,
                description=description,
                involved_messages=message_ids,
                conflict_type=conflict_type,
                detected_by=detected_by,
                resolution_deadline=resolution_deadline,
                options=options or [],
                task_id=task_id,
            )
            await self._emit(event)
        return conflict_id

    async def resolve_conflict(self, conflict_id: str, resolution: str, resolved_by: str, resolution_details: Optional[dict] = None) -> bool:
        """Mark a conflict as resolved."""
        async with self._lock:
            event = ConflictResolvedEvent(
                id=uuid4(),
                type=EventType.CONFLICT_RESOLVED,
                room_id=self.room_id,
                user_id=resolved_by,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                conflict_id=conflict_id,
                resolution=resolution,
                resolved_by=resolved_by,
                resolution_details=resolution_details,
            )
            await self._emit(event)
        return True

    # ---- Command Steering / Voting / Override ----

    async def command_steer(self, instructions: str, issued_by: str, parameters: Optional[dict] = None) -> None:
        """Issue a steer command to guide the coder."""
        async with self._lock:
            event = CommandSteerEvent(
                id=uuid4(),
                type=EventType.COMMAND_STEER,
                room_id=self.room_id,
                user_id=issued_by,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                instructions=instructions,
                issued_by=issued_by,
                parameters=parameters,
            )
            await self._emit(event)

    async def command_vote(self, option_id: str, issued_by: str, vote_value: bool | int | str, plan_item_id: Optional[str] = None) -> None:
        """Issue a vote command."""
        async with self._lock:
            event = CommandVoteEvent(
                id=uuid4(),
                type=EventType.COMMAND_VOTE,
                room_id=self.room_id,
                user_id=issued_by,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                option_id=option_id,
                plan_item_id=plan_item_id,
                vote_value=vote_value,
                issued_by=issued_by,
            )
            await self._emit(event)

    async def command_override(self, new_plan: list[dict], issued_by: str, reason: Optional[str] = None) -> None:
        """Override the current plan entirely."""
        async with self._lock:
            await self.plan.replace(new_plan)
            event = CommandOverrideEvent(
                id=uuid4(),
                type=EventType.COMMAND_OVERRIDE,
                room_id=self.room_id,
                user_id=issued_by,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                new_plan=new_plan,
                issued_by=issued_by,
                reason=reason,
            )
            await self._emit(event)

    async def command_edit_plan(self, edits: list[dict], issued_by: str) -> None:
        """Apply a list of edits to the plan."""
        async with self._lock:
            old = {i["id"]: i for i in await self.plan.get_items()}
            bound: list[dict] = []
            for edit in edits:
                if edit.get("type") == "add" and isinstance(edit.get("item"), dict):
                    item = self._bind_requesters(None, edit["item"], issued_by)
                    item["status"] = "draft"
                    edit = {**edit, "item": item}
                elif edit.get("type") == "update" and isinstance(edit.get("changes"), dict):
                    before = old.get(edit.get("item_id", ""))
                    after = self._bind_requesters(before, {**(before or {}), **edit["changes"]}, issued_by)
                    changes = {k: v for k, v in edit["changes"].items() if k != "requested_by"}
                    changed_scope = any(after.get(key) != (before or {}).get(key) for key in ("title", "notes", "owner_role"))
                    if changed_scope:
                        changes["status"] = "draft"
                    else:
                        # Plan editing cannot approve a draft or change task execution state.
                        changes.pop("status", None)
                    if after.get("requested_by") != (before or {}).get("requested_by"):
                        changes["requested_by"] = after.get("requested_by")
                    edit = {**edit, "changes": changes}
                bound.append(edit)
            edits = bound
            # Validate every edit before changing anything, so the log never
            # records a partially-applied edit list
            await self.plan.replace(await self._plan_after_edits(edits))

            event = CommandEditPlanEvent(
                id=uuid4(),
                type=EventType.COMMAND_EDIT_PLAN,
                room_id=self.room_id,
                user_id=issued_by,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                edits=edits,
                issued_by=issued_by,
            )
            await self._emit(event)

    async def command_answer_question(self, question_id: str, answer: str, issued_by: str) -> None:
        """Answer a question in the room."""
        async with self._lock:
            event = CommandAnswerQuestionEvent(
                id=uuid4(),
                type=EventType.COMMAND_ANSWER_QUESTION,
                room_id=self.room_id,
                user_id=issued_by,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                question_id=question_id,
                answer=answer,
                issued_by=issued_by,
            )
            await self._emit(event)

    async def command_end_session(self, issued_by: str, reason: Optional[str] = None, cleanup_data: bool = True) -> bool:
        """End the current session (owner only)."""
        if issued_by != self.owner_id:
            return False

        async with self._lock:
            await self.sitting.end_session(issued_by)
            event = CommandEndSessionEvent(
                id=uuid4(),
                type=EventType.COMMAND_END_SESSION,
                room_id=self.room_id,
                user_id=issued_by,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                issued_by=issued_by,
                reason=reason,
                cleanup_data=cleanup_data,
            )
            await self._emit(event)
        return True

    # ---- Questions ----

    async def ask_question(
        self, question: str, asked_by: str, context: Optional[str] = None, requires_answer: bool = True, *,
        options: Optional[list[str]] = None, default_option: Optional[str] = None, task_id: Optional[str] = None,
        expires_at: Optional[datetime] = None,
    ) -> str:
        """Ask a question in the room."""
        question_id = str(uuid4())
        async with self._lock:
            event = QuestionAskedEvent(
                id=uuid4(),
                type=EventType.QUESTION_ASKED,
                room_id=self.room_id,
                user_id=asked_by,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                question_id=question_id,
                question=question,
                asked_by=asked_by,
                context=context,
                requires_answer=requires_answer,
                options=options or [],
                default_option=default_option,
                task_id=task_id,
                expires_at=expires_at,
            )
            await self._emit(event)
        return question_id

    async def answer_question(self, question_id: str, answer: str, answered_by: str, accepted: bool = False) -> bool:
        """Answer a question."""
        async with self._lock:
            event = QuestionAnsweredEvent(
                id=uuid4(),
                type=EventType.QUESTION_ANSWERED,
                room_id=self.room_id,
                user_id=answered_by,
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                question_id=question_id,
                answer=answer,
                answered_by=answered_by,
                accepted=accepted,
            )
            await self._emit(event)
        return True

    # ---- AI Streaming ----

    async def start_ai_message(self, message_id: str, trigger: str, model_used: str, user_id: Optional[str] = None) -> None:
        """Signal start of AI message generation."""
        async with self._lock:
            event = AIMessageStartedEvent(
                id=uuid4(),
                type=EventType.AI_MESSAGE_STARTED,
                room_id=self.room_id,
                user_id=user_id or "ai",
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                message_id=message_id,
                trigger=trigger,
                model_used=model_used,
            )
            await self._emit(event)

    async def emit_ai_chunk(self, message_id: str, chunk: str, chunk_index: int, user_id: Optional[str] = None) -> None:
        """Emit a chunk of AI-generated content."""
        async with self._lock:
            event = AIMessageChunkEvent(
                id=uuid4(),
                type=EventType.AI_MESSAGE_CHUNK,
                room_id=self.room_id,
                user_id=user_id or "ai",
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                message_id=message_id,
                chunk=chunk,
                chunk_index=chunk_index,
            )
            await self._emit(event)

    async def complete_ai_message(self, message_id: str, full_content: str, usage: Optional[dict] = None, finish_reason: Optional[str] = None, user_id: Optional[str] = None) -> None:
        """Signal completion of AI message generation."""
        async with self._lock:
            event = AIMessageCompletedEvent(
                id=uuid4(),
                type=EventType.AI_MESSAGE_COMPLETED,
                room_id=self.room_id,
                user_id=user_id or "ai",
                timestamp=datetime.now(timezone.utc),
                sequence=self._next_sequence(),
                prev_event_id=None,
                message_id=message_id,
                full_content=full_content,
                usage=usage,
                finish_reason=finish_reason,
            )
            await self._emit(event)

    def notify_turn(self) -> None:
        """Signal the run loop to process a turn immediately."""
        self._turn_event.set()
        self._turn_event.clear()

    # ---- Internal loop ----

    async def _run_loop(self) -> None:
        """Main actor loop - processes inbox at turn boundaries."""
        try:
            while self._state.running:
                # Wait for next turn signal
                try:
                    await asyncio.wait_for(self._turn_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass

                # Check if stopped while waiting
                if not self._state.running:
                    break

                # Drain inbox at turn boundary
                merges, edit_notes, interrupt = await self.drain_inbox()

                if interrupt:
                    logger.info(f"Room {self.room_id}: interrupt received, aborting turn")
                    # Could trigger re-plan here
                    continue

                if merges:
                    logger.debug(f"Room {self.room_id}: injecting {len(merges)} merges into coder context")
                    # Merges would be passed to coder loop here

                # Periodic maintenance
                await self.presence.mark_idle_as_away()
                await self.presence.cleanup_offline()
                await self.locks.release_idle_locks()

        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception(f"RoomActor loop crashed for room {self.room_id}")
        finally:
            self._state.running = False
            self._task = None


async def create_room_actor(
    room_id: str,
    owner_id: str,
    event_log: EventLog,
    config: Optional[ActorConfig] = None,
    on_event: Optional[Callable[[BaseEvent], Any]] = None,
    on_sitting_ended: Optional[Callable[[str, Any], None]] = None,
    on_budget_pause: Optional[Callable[[str, str], None]] = None,
    on_budget_resume: Optional[Callable[[str], None]] = None,
    on_budget_warning: Optional[Callable[[str, float, float], None]] = None,
    on_lock_expire: Optional[Callable[[str, str], None]] = None,
) -> RoomActor:
    """Factory function to create and start a room actor."""
    actor = RoomActor(
        room_id=room_id,
        owner_id=owner_id,
        event_log=event_log,
        config=config,
        on_event=on_event,
        on_sitting_ended=on_sitting_ended,
        on_budget_pause=on_budget_pause,
        on_budget_resume=on_budget_resume,
        on_budget_warning=on_budget_warning,
        on_lock_expire=on_lock_expire,
    )
    await actor.start()
    return actor
