"""The room REST API from source-of-truth/architecture.md, mounted at /rooms (web/src/lib/api.ts calls it).

Commands answer {"accepted": true, "seq": <room seq>}; the change itself arrives on the room socket as an
event envelope (mux/events/wire.py). Rooms and members are returned in the web app's Room shape.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator, model_validator

from mux.api.account import owned_room_actors
from mux.plans import get_plan
from mux.api.deps import get_room_actor_dep, get_current_user, require_editor, require_owner, require_viewer, User
from mux.events.file_log import stored_room_ids
from mux.events.wire import membership_view
from mux.integrations.github import get_github_integration
from mux.integrations.invite_email import room_link, send_invite_email
from mux.rooms.access import hash_password, password_attempts, verify_password
from mux.rooms.actor import PathConflictError, RoomActor
from mux.rooms.registry import get_registry
from mux.rooms.uploads import MAX_ATTACHMENTS, room_uploads

logger = logging.getLogger(__name__)

router = APIRouter()

# GET /github/connect lives at the root next to /rooms (architecture.md); main.py mounts it
github_router = APIRouter()

DomainRole = Literal["pm", "design", "eng"]
MemberRole = Literal["editor", "viewer"]
# Loose on purpose: Supabase is the real check when the email is sent
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# =============================================================================
# Request/Response Models
# =============================================================================

class RoomCreateRequest(BaseModel):
    """`POST /rooms`: a room from a description. `name` defaults to the description's first line."""
    description: Optional[str] = Field(None, max_length=2000, description="What to build")
    domain_role: Optional[DomainRole] = Field(None, description="The creator's domain role")
    name: Optional[str] = Field(None, min_length=1, max_length=200, description="Room name")
    initial_plan: Optional[list[dict]] = Field(None, description="Initial plan items")
    password: Optional[str] = Field(None, min_length=4, max_length=128,
                                    description="Lets anyone with the room id and this password join as editor")

    @model_validator(mode="after")
    def _needs_name_or_description(self) -> "RoomCreateRequest":
        if not (self.name or (self.description or "").strip()):
            raise ValueError("Give a description (or a name)")
        return self

    def room_name(self) -> str:
        if self.name:
            return self.name
        return (self.description or "").strip().splitlines()[0][:200]


class RoomJoinRequest(BaseModel):
    """Request to join a room."""
    user_name: Optional[str] = Field(None, max_length=100, description="Display name")
    avatar_url: Optional[str] = Field(None, description="Avatar URL")
    domain_role: Optional[DomainRole] = Field(None, description="Domain role when joining a public room")
    password: Optional[str] = Field(None, max_length=128, description="The room password, when joining with the room id")


class RoomJoinResponse(BaseModel):
    """Response after joining a room."""
    user_id: str
    user_name: Optional[str]
    avatar_url: Optional[str]
    status: str
    joined_at: str


class RoomStatusResponse(BaseModel):
    """Current room status snapshot (GET /rooms/{id}/status)."""
    room_id: str
    owner_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    active_members: int
    plan_items: int
    budget: dict
    sitting_active: bool
    sitting_idle_seconds: Optional[float]
    locks_held: int
    files_count: int
    running: bool


class RoomLeaveResponse(BaseModel):
    """Response after leaving a room."""
    user_id: str
    left: bool


class RoomCloseResponse(BaseModel):
    """Response after closing a room."""
    room_id: str
    closed: bool
    reason: Optional[str]


class MemberAddRequest(BaseModel):
    """Request to grant a user membership in a room (owner only)."""
    user_id: str = Field(..., min_length=1, max_length=200, description="User to add")
    role: Literal["editor", "viewer"] = Field("editor", description="Role to grant")
    domain_role: Optional[DomainRole] = Field(None, description="Domain role (pm, design, eng)")


class MemberResponse(BaseModel):
    """Response after granting membership."""
    room_id: str
    user_id: str
    role: str
    sequence: int


class SharingUpdateRequest(BaseModel):
    """`PATCH /rooms/{id}/sharing`."""
    link_access: Literal["restricted", "anyone"]
    link_permission: MemberRole = Field("editor", description="Role granted by joining through the link")


class InviteRequest(BaseModel):
    """`POST /rooms/{id}/invites`."""
    email: str = Field(..., min_length=3, max_length=320)
    role: MemberRole = "editor"


class InviteView(BaseModel):
    email: str
    role: str


class InviteResponse(InviteView):
    """The invite is saved even when the email couldn't be sent; `link` is what to send instead."""
    email_sent: bool
    email_error: Optional[str] = None
    link: str


class PasswordRequest(BaseModel):
    """`PUT /rooms/{id}/password`. null removes the password."""
    password: Optional[str] = Field(None, min_length=4, max_length=128)


class MessageCreateRequest(BaseModel):
    """`POST /rooms/{id}/messages`. Text, photos (Team notes only), or both."""
    text: str = Field("", max_length=10000)
    to: Literal["agent", "team"] = "agent"
    attachments: list[str] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)

    @model_validator(mode="after")
    def _has_content(self) -> "MessageCreateRequest":
        if not self.text and not self.attachments:
            raise ValueError("A message needs text or a photo")
        if self.attachments and self.to != "team":
            raise ValueError("Photos can only be sent to the team")
        return self


def is_emoji(value: str) -> bool:
    """One emoji (or a short ZWJ/skin-tone sequence): no ASCII letters, digits or spaces, not plain ASCII."""
    if not 1 <= len(value) <= 16 or value.isascii():
        return False
    return not any(c.isspace() or (c.isascii() and c.isalnum()) for c in value)


class ReactionRequest(BaseModel):
    """`POST /rooms/{id}/messages/{message_id}/reactions`: toggles the caller's reaction."""
    emoji: str

    @field_validator("emoji")
    @classmethod
    def _one_emoji(cls, value: str) -> str:
        if not is_emoji(value):
            raise ValueError("emoji must be a single emoji")
        return value


class PlanUpdateRequest(BaseModel):
    """`PATCH /rooms/{id}/plan`: the whole draft plan."""
    items: list[dict] = Field(..., description="Plan items")


class OptionRequest(BaseModel):
    """`POST .../conflicts/{cid}/vote` and `.../override`."""
    option: str = Field(..., min_length=1, max_length=500)


class AnswerRequest(BaseModel):
    """`POST .../questions/{qid}/answer`."""
    answer: str = Field(..., min_length=1, max_length=10000)


class FilePathRequest(BaseModel):
    """`POST /rooms/{id}/files/lock` and `/unlock`."""
    path: str = Field(..., min_length=1, max_length=500)


class FileSaveRequest(BaseModel):
    """`PUT /rooms/{id}/files`: a manual edit, checked against the version it was based on."""
    path: str = Field(..., min_length=1, max_length=500)
    content: str
    base_version: Optional[int] = Field(None, ge=0, description="Version the edit started from (0: a new file)")


class RewindRequest(BaseModel):
    checkpoint_id: str


class BudgetUpdateRequest(BaseModel):
    """`PATCH /rooms/{id}/budget`."""
    tokens_cap: Optional[int] = Field(None, ge=1)
    runs_cap: Optional[int] = Field(None, ge=1)


class ExportRequest(BaseModel):
    """`POST /rooms/{id}/export`: push the latest checkpoint to <your GitHub account>/<repo_name>."""
    repo_name: str = Field(..., min_length=1, max_length=100)
    private: bool = Field(True, description="Visibility when the repo doesn't exist yet and is created")


class Accepted(BaseModel):
    """Every command's answer; the effect arrives on the socket."""
    accepted: Literal[True] = True
    seq: int


class FileSaved(Accepted):
    version: int


class BudgetView(BaseModel):
    tokens_used: int
    runs_used: int
    tokens_cap: int
    runs_cap: int


class UrlResponse(BaseModel):
    url: str


# Shared dependency: looks up (or rehydrates) an existing room; never creates one.
get_room_actor = get_room_actor_dep


async def _holder_name(actor: RoomActor, path: str) -> str:
    """Who holds a file's lock, by name (the raw user id meant nothing to the person reading the error)."""
    holder = await actor.locked_by(path)
    return actor.member_names.get(holder, "a teammate") if holder else "a teammate"


def accepted(actor: RoomActor) -> Accepted:
    return Accepted(seq=actor.sequence)


async def room_view(actor: RoomActor) -> dict[str, Any]:
    """The web app's `Room` shape."""
    meta = actor.manifest.get_room_metadata()
    budget = await actor.budget.get_status()
    checkpoints = actor.manifest.list_checkpoints()
    head = max(checkpoints.items(), key=lambda kv: kv[1].get("created_at", 0))[0] if checkpoints else None
    members = [membership_view(actor.room_id, actor.owner_id, "owner", actor.domain_roles.get(actor.owner_id),
                               actor.member_names.get(actor.owner_id))]
    members += [
        membership_view(actor.room_id, uid, role, actor.domain_roles.get(uid), actor.member_names.get(uid))
        for uid, role in actor.members.items()
    ]
    created = (actor.created_at or datetime.now(timezone.utc)).isoformat()
    return {
        "id": actor.room_id,
        "title": meta.get("name") or actor.room_id,
        "description": meta.get("description") or "",
        "owner_id": actor.owner_id,
        "link_access": "anyone" if actor.public else "restricted",
        "link_permission": actor.link_permission,
        "has_password": actor.password_hash is not None,
        "budget_tokens_cap": budget["token_cap"],
        "budget_runs_cap": budget["sandbox_run_cap"],
        "head_checkpoint_id": head,
        "created_at": created,
        "members": members,
    }


# =============================================================================
# Rooms
# =============================================================================

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_room(
    request: RoomCreateRequest,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Create a room with the current user as owner, within their plan's room limit."""
    plan = get_plan(current_user.id)
    if plan.room_limit is not None and len(await owned_room_actors(current_user.id)) >= plan.room_limit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your {plan.label} plan includes {plan.room_limit} rooms you own. "
                   "Upgrade on the Pricing page to create more, or close a room you no longer need.",
        )
    registry = get_registry()
    room_id = f"room_{uuid.uuid4().hex[:12]}"

    # Records ROOM_CREATED with owner, metadata and the owner's domain role
    actor = await registry.create_room(
        room_id, current_user.id, name=request.room_name(), description=request.description,
        domain_role=request.domain_role,
    )

    if request.initial_plan:
        try:
            await actor.draft_plan(request.initial_plan, current_user.id)
        except ValueError:
            # Don't leave a half-created room behind
            await actor.close_room(current_user.id, "invalid initial plan")
            await registry.stop_room(room_id, reason="invalid initial plan")
            raise
    if request.password:
        await actor.set_password(await asyncio.to_thread(hash_password, request.password), current_user.id)

    return await room_view(actor)


@router.get("")
async def list_rooms(current_user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    """Rooms the current user owns or has joined: running rooms and rooms saved on disk (rebuilt on first use).

    Public rooms they haven't joined are left out, so a link-only room stays findable only through its link.
    """
    registry = get_registry()
    rooms = []
    for room_id in sorted(set(await registry.list_rooms()) | set(stored_room_ids())):
        actor = await registry.get_room_or_rehydrate(room_id)
        if actor and (current_user.id == actor.owner_id or current_user.id in actor.members):
            rooms.append(await room_view(actor))
    return rooms


@router.get("/{room_id}")
async def get_room_endpoint(
    room_id: str,
    current_user: User = Depends(require_viewer),
    actor: RoomActor = Depends(get_room_actor),
) -> dict[str, Any]:
    """One room, in the Room shape. Requires viewer permission."""
    return await room_view(actor)


@router.get("/{room_id}/status", response_model=RoomStatusResponse)
async def get_room_status_endpoint(
    room_id: str,
    current_user: User = Depends(require_viewer),
    actor: RoomActor = Depends(get_room_actor),
) -> RoomStatusResponse:
    """Operational snapshot: presence, locks, sitting, budget."""
    return RoomStatusResponse(**await get_room_status(actor))


@router.post("/{room_id}/join", response_model=RoomJoinResponse)
async def join_room(
    room_id: str,
    request: RoomJoinRequest,
    current_user: User = Depends(get_current_user),
    actor: RoomActor = Depends(get_room_actor),
) -> RoomJoinResponse:
    """
    Join a room as a participant.

    Owners and members can always join. Anyone else needs one of, in this order:
    an invite for the email they signed in with (grants the invited role), the room
    password (grants editor), or a public link (grants the link's permission).
    """
    user_id = current_user.id
    # Viewers go through this too: an invite or the password can raise them to editor
    if user_id != actor.owner_id and actor.members.get(user_id) != "editor":
        email = (current_user.email or "").strip().lower()
        invited_role = actor.invites.get(email) if email else None
        if invited_role:
            await actor.add_member(user_id, invited_role, granted_by=user_id, user_name=request.user_name,
                                   domain_role=request.domain_role)
            await actor.revoke_invite(email, user_id, accepted=True)
        elif request.password is not None:
            await _check_password(actor, user_id, request.password)
            await actor.add_member(user_id, "editor", granted_by=user_id, user_name=request.user_name,
                                   domain_role=request.domain_role)
        elif user_id in actor.members:
            pass  # already a viewer
        elif actor.public:
            await actor.add_member(user_id, actor.link_permission, granted_by=user_id, user_name=request.user_name,
                                   domain_role=request.domain_role)
        else:
            detail = ("This room needs a password, or an invite from the owner" if actor.password_hash
                      else "This room is private; ask the owner to invite you")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

    presence = await actor.user_join(
        user_id=current_user.id,
        user_name=request.user_name,
        avatar_url=request.avatar_url,
    )

    return RoomJoinResponse(
        user_id=current_user.id,
        user_name=presence.user_name,
        avatar_url=presence.avatar_url,
        status=presence.status,
        # presence.joined_at is a monotonic clock value, not wall time
        joined_at=datetime.now(timezone.utc).isoformat(),
    )


async def _check_password(actor: RoomActor, user_id: str, password: str) -> None:
    """403 on a wrong password; 429 after too many wrong ones."""
    if actor.password_hash is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This room doesn't have a password")
    wait = password_attempts.retry_after(actor.room_id, user_id)
    if wait > 0:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail=f"Too many wrong passwords; try again in {int(wait // 60) + 1} min",
                            headers={"Retry-After": str(int(wait) + 1)})
    if not await asyncio.to_thread(verify_password, password, actor.password_hash):
        password_attempts.record_failure(actor.room_id, user_id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Wrong room id or password")
    password_attempts.reset(actor.room_id, user_id)


@router.post("/{room_id}/kickoff")
async def kickoff(
    room_id: str,
    current_user: User = Depends(require_owner),
    actor: RoomActor = Depends(get_room_actor),
) -> dict[str, Any]:
    """Owner only: "Plan it with me". The room's runtime reads the project, asks the team and drafts a plan."""
    runtime = get_registry().runtime(room_id)
    if runtime is None or not runtime.model_available():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This room has no AI model, so planning can't start")
    if runtime.kickoff_running:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Planning is already running")
    await actor.request_kickoff(current_user.id)
    return {"accepted": True}


@router.post("/{room_id}/leave", response_model=RoomLeaveResponse)
async def leave_room(
    room_id: str,
    current_user: User = Depends(get_current_user),
    actor: RoomActor = Depends(get_room_actor),
) -> RoomLeaveResponse:
    """
    Leave a room.
    """
    await actor.user_leave(current_user.id)

    return RoomLeaveResponse(
        user_id=current_user.id,
        left=True,
    )


@router.post("/{room_id}/close", response_model=RoomCloseResponse)
async def close_room(
    room_id: str,
    reason: Optional[str] = Query(None, description="Reason for closing"),
    current_user: User = Depends(require_owner),
    actor: RoomActor = Depends(get_room_actor),
) -> RoomCloseResponse:
    """
    Close a room (owner only).
    Stops the room actor and cleans up.
    """
    registry = get_registry()
    # Record the close so the room is not rehydrated (and re-claimed) later
    await actor.close_room(current_user.id, reason)
    await registry.stop_room(room_id, reason=reason or "closed_by_owner")

    return RoomCloseResponse(
        room_id=room_id,
        closed=True,
        reason=reason,
    )


# =============================================================================
# Room Status Helpers
# =============================================================================

async def get_room_status(actor: RoomActor) -> dict:
    """Get a comprehensive status snapshot of the room."""
    # Get plan info
    plan_items = await actor.plan.get_items()

    # Get budget status
    budget_status = await actor.budget.get_status()

    # Get sitting status
    sitting_status = await actor.sitting.get_status()

    # Get presence info
    members = await actor.presence.get_all()

    # Get locks info
    locks = await actor.locks.get_all_locks()

    # Get files count
    files_count = len(actor.manifest.entries)

    # Get room metadata
    room_meta = actor.manifest.get_room_metadata()

    return {
        "room_id": actor.room_id,
        "owner_id": actor.owner_id,
        "name": room_meta.get("name"),
        "description": room_meta.get("description"),
        "active_members": len(members),
        "plan_items": len(plan_items),
        "budget": budget_status,
        "sitting_active": sitting_status.get("active", False),
        "sitting_idle_seconds": sitting_status.get("idle_seconds"),
        "locks_held": len(locks),
        "files_count": files_count,
        "running": actor.is_running(),
    }


# =============================================================================
# Sharing and membership
# =============================================================================

@router.patch("/{room_id}/sharing")
async def update_room_sharing(
    room_id: str,
    request: SharingUpdateRequest,
    current_user: User = Depends(require_owner),
    actor: RoomActor = Depends(get_room_actor),
) -> dict[str, Any]:
    """Owner only. "anyone": any signed-in user with the link can view, and joining grants link_permission."""
    await actor.set_sharing(request.link_access == "anyone", False, current_user.id, request.link_permission)
    return await room_view(actor)


@router.get("/{room_id}/invites", response_model=list[InviteView])
async def list_invites(
    room_id: str,
    current_user: User = Depends(require_owner),
    actor: RoomActor = Depends(get_room_actor),
) -> list[InviteView]:
    """Pending invites (owner only: they're other people's email addresses)."""
    return [InviteView(email=e, role=r) for e, r in sorted(actor.invites.items())]


@router.post("/{room_id}/invites", response_model=InviteResponse)
async def create_invite(
    room_id: str,
    request: InviteRequest,
    current_user: User = Depends(require_owner),
    actor: RoomActor = Depends(get_room_actor),
) -> InviteResponse:
    """Invite an email address and email them a sign-in link into the room.

    Whoever signs in with that address and opens the room joins with `role`, even in a private room.
    """
    email = request.email.strip().lower()
    if not EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="That doesn't look like an email address")
    if current_user.email and email == current_user.email.strip().lower():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You're already in this room")
    await actor.create_invite(email, request.role, current_user.id)
    title = actor.manifest.get_room_metadata().get("name") or actor.room_id
    result = await send_invite_email(email, actor.room_id, title, current_user.name)
    return InviteResponse(email=email, role=request.role, email_sent=result.sent, email_error=result.error,
                          link=room_link(actor.room_id))


@router.delete("/{room_id}/invites/{email}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    room_id: str,
    email: str,
    current_user: User = Depends(require_owner),
    actor: RoomActor = Depends(get_room_actor),
) -> None:
    if not await actor.revoke_invite(email, current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No pending invite for that email")


@router.put("/{room_id}/password")
async def set_room_password(
    room_id: str,
    request: PasswordRequest,
    current_user: User = Depends(require_owner),
    actor: RoomActor = Depends(get_room_actor),
) -> dict[str, Any]:
    """Owner only. With the room id and this password anyone signed in can join as editor; null removes it."""
    password_hash = await asyncio.to_thread(hash_password, request.password) if request.password else None
    await actor.set_password(password_hash, current_user.id)
    return await room_view(actor)


@router.post("/{room_id}/members", response_model=MemberResponse)
async def add_member(
    room_id: str,
    request: MemberAddRequest,
    current_user: User = Depends(require_owner),
    actor: RoomActor = Depends(get_room_actor),
) -> MemberResponse:
    """Grant a user editor or viewer membership (owner only)."""
    await actor.add_member(request.user_id, request.role, granted_by=current_user.id, domain_role=request.domain_role)
    return MemberResponse(room_id=room_id, user_id=request.user_id, role=request.role, sequence=actor.sequence)


# =============================================================================
# Messages and plan
# =============================================================================

@router.post("/{room_id}/messages", response_model=Accepted)
async def send_message(
    room_id: str,
    request: MessageCreateRequest,
    current_user: User = Depends(require_editor),
    actor: RoomActor = Depends(get_room_actor),
) -> Accepted:
    """A steering message (to the agent) or a note between people (to the team)."""
    uploads = room_uploads(actor.room_id)
    missing = [a for a in request.attachments if uploads.find(a) is None]
    if missing:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Unknown photo: {missing[0]}")
    await actor.add_message(
        label="chat", content=request.text, message_id=str(uuid.uuid4()), user_id=current_user.id, to=request.to,
        user_name=current_user.name, attachments=request.attachments,
        enqueue=False,  # the room runtime labels it with the coordinator, then enqueues merges/interrupts
    )
    return accepted(actor)


@router.post("/{room_id}/messages/{message_id}/reactions", response_model=Accepted)
async def toggle_reaction(
    room_id: str,
    message_id: str,
    request: ReactionRequest,
    current_user: User = Depends(require_editor),
    actor: RoomActor = Depends(get_room_actor),
) -> Accepted:
    """Add the caller's emoji to a person's message, or remove it if it's already there."""
    try:
        await actor.toggle_reaction(message_id, current_user.id, request.emoji)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    return accepted(actor)


@router.patch("/{room_id}/plan", response_model=Accepted)
async def update_plan(
    room_id: str,
    request: PlanUpdateRequest,
    current_user: User = Depends(require_editor),
    actor: RoomActor = Depends(get_room_actor),
) -> Accepted:
    """Replace the draft plan with the edited one (emits plan.edited with the items)."""
    await actor.replace_plan(request.items, current_user.id)
    return accepted(actor)


@router.post("/{room_id}/plan/approve", response_model=Accepted)
async def approve_plan(
    room_id: str,
    current_user: User = Depends(require_owner),
    actor: RoomActor = Depends(get_room_actor),
) -> Accepted:
    """Approve the plan: every draft item becomes todo."""
    drafts = [item["id"] for item in await actor.get_plan() if item.get("status") == "draft"]
    await actor.approve_plan_items(drafts, current_user.id)
    return accepted(actor)


# =============================================================================
# Conflicts and questions
# =============================================================================

@router.post("/{room_id}/conflicts/{conflict_id}/vote", response_model=Accepted)
async def vote_on_conflict(
    room_id: str,
    conflict_id: str,
    request: OptionRequest,
    current_user: User = Depends(require_editor),
    actor: RoomActor = Depends(get_room_actor),
) -> Accepted:
    await actor.command_vote(option_id=request.option, issued_by=current_user.id, vote_value=True, plan_item_id=conflict_id)
    return accepted(actor)


@router.post("/{room_id}/conflicts/{conflict_id}/override", response_model=Accepted)
async def override_conflict(
    room_id: str,
    conflict_id: str,
    request: OptionRequest,
    current_user: User = Depends(require_owner),
    actor: RoomActor = Depends(get_room_actor),
) -> Accepted:
    """The owner picks the result and closes the conflict."""
    if not await actor.resolve_conflict(conflict_id, request.option, current_user.id, {"override": True}):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Conflict not found: {conflict_id}")
    return accepted(actor)


@router.post("/{room_id}/questions/{question_id}/answer", response_model=Accepted)
async def answer_question(
    room_id: str,
    question_id: str,
    request: AnswerRequest,
    current_user: User = Depends(require_editor),
    actor: RoomActor = Depends(get_room_actor),
) -> Accepted:
    await actor.command_answer_question(question_id=question_id, answer=request.answer, issued_by=current_user.id)
    return accepted(actor)


# =============================================================================
# Files (soft locks, version-checked saves)
# =============================================================================

@router.post("/{room_id}/files/lock", response_model=Accepted)
async def lock_file(
    room_id: str,
    request: FilePathRequest,
    current_user: User = Depends(require_editor),
    actor: RoomActor = Depends(get_room_actor),
) -> Accepted:
    path = request.path.lstrip("/")
    if not await actor.lock_file(path, current_user.id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{path} is being edited by {await _holder_name(actor, path)}")
    return accepted(actor)


@router.post("/{room_id}/files/unlock", response_model=Accepted)
async def unlock_file(
    room_id: str,
    request: FilePathRequest,
    current_user: User = Depends(require_editor),
    actor: RoomActor = Depends(get_room_actor),
) -> Accepted:
    """The lock holder (or the owner) releases a lock."""
    path = request.path.lstrip("/")
    holder = await actor.locked_by(path)
    if holder and holder != current_user.id and current_user.id != actor.owner_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the lock holder or room owner can unlock")
    await actor.unlock_file(path, holder or current_user.id)
    return accepted(actor)


@router.put("/{room_id}/files", response_model=FileSaved)
async def save_file(
    room_id: str,
    request: FileSaveRequest,
    current_user: User = Depends(require_editor),
    actor: RoomActor = Depends(get_room_actor),
) -> FileSaved:
    """Save a manual edit. 409 if someone else holds the lock or the file moved past `base_version`."""
    path = request.path.lstrip("/")
    current = await actor.file_version(path)
    # No base_version: overwrite whatever is there (still refused while someone else holds the lock)
    base = request.base_version if request.base_version is not None else current
    try:
        outcome, version = await actor.save_checked(path, request.content, base, current_user.id)
    except PathConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if outcome == "locked":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{path} is being edited by {await _holder_name(actor, path)}")
    if outcome == "stale":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{path} changed since version {request.base_version} (now {version}); reload it",
        )
    return FileSaved(seq=actor.sequence, version=version or 0)


async def _delete(actor: RoomActor, path: str, user_id: str) -> Accepted:
    path = path.lstrip("/")
    if await actor.get_file(path) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File not found: {path}")
    await actor.delete_file(path, user_id)
    return accepted(actor)


@router.delete("/{room_id}/files", response_model=Accepted)
async def delete_file(
    room_id: str,
    path: str = Query(..., min_length=1),
    current_user: User = Depends(require_editor),
    actor: RoomActor = Depends(get_room_actor),
) -> Accepted:
    return await _delete(actor, path, current_user.id)


@router.delete("/{room_id}/files/{file_path:path}", response_model=Accepted)
async def delete_file_by_path(
    room_id: str,
    file_path: str,
    current_user: User = Depends(require_editor),
    actor: RoomActor = Depends(get_room_actor),
) -> Accepted:
    return await _delete(actor, file_path, current_user.id)


# =============================================================================
# Rewind, budget, session, export
# =============================================================================

@router.post("/{room_id}/rewind", response_model=Accepted)
async def rewind(
    room_id: str,
    request: RewindRequest,
    current_user: User = Depends(require_editor),
    actor: RoomActor = Depends(get_room_actor),
) -> Accepted:
    """Rewind plan and files to a checkpoint (a checkpoint of the current state is saved first)."""
    checkpoint_data = actor.manifest.get_checkpoint(request.checkpoint_id)
    if not checkpoint_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Checkpoint not found: {request.checkpoint_id}")
    target_sequence = checkpoint_data.get("sequence", 0)
    ok = await actor.rewind_to_sequence(
        target_sequence=target_sequence,
        user_id=current_user.id,
        reason=f"Rewind to checkpoint {request.checkpoint_id}",
        preserve_checkpoint=True,
    )
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Rewind failed: invalid target sequence")
    return accepted(actor)


@router.patch("/{room_id}/budget", response_model=BudgetView)
async def update_budget(
    room_id: str,
    request: BudgetUpdateRequest,
    current_user: User = Depends(require_owner),
    actor: RoomActor = Depends(get_room_actor),
) -> BudgetView:
    """Raise the caps (owner only); a paused room resumes once it is under them."""
    if not await actor.raise_budget_caps(user_id=current_user.id, token_cap=request.tokens_cap, sandbox_run_cap=request.runs_cap):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New caps must be higher than the current caps")
    b = await actor.budget.get_status()
    return BudgetView(tokens_used=b["tokens_used"], runs_used=b["sandbox_runs_used"], tokens_cap=b["token_cap"], runs_cap=b["sandbox_run_cap"])


@router.post("/{room_id}/end-session", response_model=Accepted)
async def end_session(
    room_id: str,
    current_user: User = Depends(require_owner),
    actor: RoomActor = Depends(get_room_actor),
) -> Accepted:
    """End the sitting (owner only)."""
    await actor.command_end_session(current_user.id, reason="owner_ended")
    return accepted(actor)


@router.post("/{room_id}/export", response_model=UrlResponse)
async def export_room(
    room_id: str,
    request: ExportRequest,
    current_user: User = Depends(require_owner),
    actor: RoomActor = Depends(get_room_actor),
) -> UrlResponse:
    """Push the room's current code to <connected GitHub user>/<repo_name>, creating the repo if needed."""
    github = get_github_integration()
    username = github.get_connection_status(current_user.id).get("username")
    if not username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Connect GitHub first (GET /github/connect)")

    checkpoint_id = await actor.create_checkpoint(current_user.id, "Export to GitHub")
    checkpoint_data = actor.manifest.get_checkpoint(checkpoint_id) or {}
    try:
        await github.ensure_repository(current_user.id, username, request.repo_name, request.private)
        result = await github.export_checkpoint(
            user_id=current_user.id, checkpoint_data=checkpoint_data, github_owner=username,
            github_repo=request.repo_name, branch="main", commit_message=None, path_prefix="",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except httpx.HTTPStatusError as e:
        detail = f"GitHub returned {e.response.status_code} for {e.request.method} {e.request.url.path}"
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)
    actor.manifest.add_export_record({
        "checkpoint_id": checkpoint_id, "github_owner": username, "github_repo": request.repo_name,
        "branch": "main", "commit_sha": result.commit_sha, "html_url": result.html_url,
        "files_exported": result.files_pushed, "exported_at": datetime.now(timezone.utc).timestamp(),
        "exported_by": current_user.id,
    })
    return UrlResponse(url=result.html_url)


def _safe_app_path(path: Optional[str]) -> Optional[str]:
    """An in-app path such as /room/abc?export=1; anything that could leave the app is dropped."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return None
    return path


@github_router.get("/github/connect", response_model=UrlResponse)
async def github_connect(
    next: Optional[str] = Query(None, max_length=500, description="App path to return to after connecting"),
    current_user: User = Depends(get_current_user),
) -> UrlResponse:
    """The GitHub authorization URL to send the user to (repo scope, asked for only at export)."""
    try:
        auth_url, _state = get_github_integration().get_authorization_url(current_user.id, return_to=_safe_app_path(next))
    except RuntimeError as e:  # GITHUB_CLIENT_ID / SECRET not set
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    return UrlResponse(url=auth_url)
