"""The wire format: room actor events -> the envelope in docs/06-event-catalog.md.

    {"seq", "room_id", "type", "actor", "actor_id", "ts", "payload"}

The web app's reducer (web/src/lib/socket.ts, reducer.ts) consumes only this shape. Actor events
with no counterpart in the catalog return None and are not sent.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any, Optional, cast

from mux.events.models import (
    MessageReactionToggledEvent,
    AgentNoticeEvent,
    AIMessageChunkEvent,
    AIMessageCompletedEvent,
    BaseEvent,
    BudgetExceededEvent,
    CheckpointCreatedEvent,
    CheckpointRestoredEvent,
    CommandAnswerQuestionEvent,
    CommandEditPlanEvent,
    CommandOverrideEvent,
    CommandRewindEvent,
    CommandVoteEvent,
    ConflictDetectedEvent,
    ConflictResolvedEvent,
    EventType,
    FileCreatedEvent,
    FileDeletedEvent,
    FileUpdatedEvent,
    PlanCreatedEvent,
    PlanItemAddedEvent,
    PlanItemCompletedEvent,
    PlanItemUpdatedEvent,
    PlanUpdatedEvent,
    QuestionAnsweredEvent,
    QuestionAskedEvent,
    RoomCreatedEvent,
    RoomAiSettingsSavedEvent,
    RoomJoinedEvent,
    RoomMcpAdminToggledEvent,
    RoomMcpServerRemovedEvent,
    RoomMcpServerSavedEvent,
    RoomPasswordSetEvent,
    RoomSharingUpdatedEvent,
    CoderRolesUpdatedEvent,
    MemberAccessUpdatedEvent,
    ChangeRequestedEvent,
    ChangeRequestDecidedEvent,
    RoomSkillsSetEvent,
    TaskFinishedEvent,
    TaskStartedEvent,
    UserJoinedEvent,
    UserMessageSentEvent,
    UserTypingEvent,
)
from mux.mcp.views import server_view

DEFAULT_DOMAIN_ROLE = "eng"


def user_view(user_id: str, name: Optional[str] = None) -> dict[str, Any]:
    """The `User` shape the web app renders (we keep no profiles, so name/colour are derived)."""
    display = name or user_id
    hue = int(hashlib.sha256(user_id.encode()).hexdigest()[:4], 16) % 360
    return {
        "id": user_id,
        "email": "",
        "name": display,
        "initials": display[:2].upper(),
        "color": f"hsl({hue}, 70%, 60%)",
    }


def membership_view(room_id: str, user_id: str, permission: str, domain_role: Optional[str], name: Optional[str] = None,
                    coder_role: Optional[str] = None, folders: Iterable[str] = ()) -> dict[str, Any]:
    """The `Membership` shape."""
    return {
        "user_id": user_id,
        "room_id": room_id,
        "permission": permission,
        "domain_role": domain_role or DEFAULT_DOMAIN_ROLE,
        "user": user_view(user_id, name),
        "coder_role": coder_role,
        "folders": list(folders),
    }


def plan_item_view(item: dict[str, Any]) -> dict[str, Any]:
    """The `PlanItem` shape: id, title, status, plus optional notes/owner_role."""
    out: dict[str, Any] = {
        "id": item.get("id"),
        "title": item.get("title", ""),
        "status": item.get("status", "draft"),
    }
    for key in ("owner_role", "notes", "merged_notes", "kind", "requested_by"):
        if item.get(key) is not None:
            out[key] = item[key]
    return out


def _ts(event: BaseEvent) -> str:
    return event.timestamp.isoformat()


def _payload(event: BaseEvent) -> Optional[tuple[str, dict[str, Any]]]:
    """(catalog type, payload) for an actor event, or None if the catalog has no such event."""
    t = event.type
    room = event.room_id
    ts = _ts(event)

    if t == EventType.AGENT_NOTICE:
        e = cast(AgentNoticeEvent, event)
        return e.kind, e.data
    if t == EventType.ROOM_CREATED:
        e = cast(RoomCreatedEvent, event)
        return "room.created", {
            "id": room, "title": e.room_name, "description": e.room_description or "",
            "owner_id": e.created_by, "created_at": ts,
        }
    if t == EventType.ROOM_JOINED:
        e = cast(RoomJoinedEvent, event)
        return "member.joined", membership_view(room, e.user_id, e.role, e.domain_role, e.user_name)
    if t == EventType.ROOM_SHARING_UPDATED:
        e = cast(RoomSharingUpdatedEvent, event)
        # A public room is viewable by anyone signed in, and joining it grants link_permission (api/rooms.py)
        return "sharing.changed", {"link_access": "anyone" if e.public else "restricted", "link_permission": e.link_permission}
    if t == EventType.CODER_ROLES_UPDATED:
        return "access.roles_changed", {"coder_roles": cast(CoderRolesUpdatedEvent, event).roles}
    if t == EventType.MEMBER_ACCESS_UPDATED:
        e = cast(MemberAccessUpdatedEvent, event)
        return "access.member_changed", {"user_id": e.member_id, "coder_role": e.coder_role, "folders": e.folders}
    if t == EventType.CHANGE_REQUESTED:
        e = cast(ChangeRequestedEvent, event)
        return "change.requested", {
            "id": e.request_id, "path": e.path, "content": e.content, "base_version": e.base_version,
            "requested_by": e.requested_by, "via": e.via, "task_id": e.task_id, "status": "pending",
            "decided_by": None, "created_at": ts, "approvers": e.approvers,
        }
    if t == EventType.CHANGE_REQUEST_DECIDED:
        e = cast(ChangeRequestDecidedEvent, event)
        return "change.decided", {"id": e.request_id, "status": e.status, "decided_by": e.decided_by}
    if t == EventType.ROOM_PASSWORD_SET:
        # Only whether there is one: the hash stays on the server
        return "sharing.changed", {"has_password": cast(RoomPasswordSetEvent, event).password_hash is not None}
    if t == EventType.ROOM_MCP_SERVER_SAVED:
        e = cast(RoomMcpServerSavedEvent, event)
        return "mcp.changed", {"server": server_view(e.name, {"url": e.url, "headers": e.headers, "tools": e.tools,
                                                              "settings": e.settings})}
    if t == EventType.ROOM_MCP_SERVER_REMOVED:
        return "mcp.changed", {"removed": cast(RoomMcpServerRemovedEvent, event).name}
    if t == EventType.ROOM_MCP_ADMIN_TOGGLED:
        e = cast(RoomMcpAdminToggledEvent, event)
        return "mcp.changed", {"admin": e.name, "enabled": e.enabled, "settings": e.settings}
    if t == EventType.ROOM_AI_SETTINGS_SAVED:
        e = cast(RoomAiSettingsSavedEvent, event)
        return "ai.changed", {"provider": e.provider, "base_url": e.base_url, "models": e.models, "has_key": True}
    if t == EventType.ROOM_AI_SETTINGS_CLEARED:
        return "ai.changed", {"cleared": True}
    if t == EventType.ROOM_SKILLS_SET:
        return "skills.changed", {"enabled": cast(RoomSkillsSetEvent, event).enabled}
    if t == EventType.KICKOFF_REQUESTED:
        return "kickoff.requested", {}
    # Invites (room_invite_*) aren't sent: the emails are for the owner only (GET /rooms/{id}/invites)

    if t == EventType.USER_JOINED:
        e = cast(UserJoinedEvent, event)
        return "presence.join", {
            "user_id": e.user_id, "user": user_view(e.user_id, e.user_name),
            "tab": "feed", "active": True, "last_seen": ts,
        }
    if t == EventType.USER_LEFT:
        return "presence.leave", {"user_id": event.user_id}
    if t == EventType.USER_TYPING:
        e = cast(UserTypingEvent, event)
        return "presence.typing", {"user_id": e.user_id, "typing": e.is_typing}

    if t == EventType.USER_MESSAGE_SENT:
        e = cast(UserMessageSentEvent, event)
        return "message.posted", {
            "id": e.message_id, "room_id": room, "user_id": e.user_id, "text": e.content,
            "to": e.to or "agent", "attachments": e.attachments, "created_at": ts, "user": user_view(e.user_id, e.user_name),
        }
    if t == EventType.MESSAGE_REACTION_TOGGLED:
        e = cast(MessageReactionToggledEvent, event)
        return "message.reacted", {"message_id": e.message_id, "user_id": e.user_id, "emoji": e.emoji, "added": e.added}
    if t == EventType.AI_MESSAGE_CHUNK:
        e = cast(AIMessageChunkEvent, event)
        return "agent.text.delta", {"task_id": e.message_id, "delta": e.chunk}
    if t == EventType.AI_MESSAGE_COMPLETED:
        e = cast(AIMessageCompletedEvent, event)
        return "agent.text", {"task_id": e.message_id, "text": e.full_content}

    if t == EventType.PLAN_CREATED:
        return "plan.drafted", {"items": [plan_item_view(i) for i in cast(PlanCreatedEvent, event).plan]}
    if t == EventType.PLAN_UPDATED:
        return "plan.edited", {"items": [plan_item_view(i) for i in cast(PlanUpdatedEvent, event).plan]}
    if t == EventType.COMMAND_OVERRIDE:
        return "plan.edited", {"items": [plan_item_view(i) for i in cast(CommandOverrideEvent, event).new_plan]}
    if t == EventType.COMMAND_EDIT_PLAN:
        # Edits are ops, not the resulting plan; clients pick the result up from plan.item_* or a reload
        e = cast(CommandEditPlanEvent, event)
        return "plan.edited", {"edits": e.edits}
    if t == EventType.COMMAND_APPROVE_PLAN:
        return "plan.approved", {}
    if t == EventType.PLAN_ITEM_ADDED:
        e = cast(PlanItemAddedEvent, event)
        item = (e.metadata or {}).get("item") or {"id": e.item_id, "title": e.title, "notes": e.description}
        return "plan.item_added", plan_item_view(item)
    if t == EventType.PLAN_ITEM_UPDATED:
        e = cast(PlanItemUpdatedEvent, event)
        return "plan.item_updated", {"id": e.item_id, "changes": e.updates}
    if t == EventType.PLAN_ITEM_COMPLETED:
        return "task.finished", {"task_id": cast(PlanItemCompletedEvent, event).item_id}
    if t == EventType.TASK_STARTED:
        return "task.started", {"task_id": cast(TaskStartedEvent, event).item_id}
    if t == EventType.TASK_FINISHED:
        return "task.finished", {"task_id": cast(TaskFinishedEvent, event).item_id}

    if t == EventType.CONFLICT_DETECTED:
        e = cast(ConflictDetectedEvent, event)
        return "conflict.opened", {
            "id": e.conflict_id, "room_id": room, "task_id": e.task_id or "", "options": e.options, "evidence": [],
            "domain": e.conflict_type if e.conflict_type in ("ui", "architecture", "scope") else "scope",
            "status": "open", "created_at": ts,
            "expires_at": e.resolution_deadline.isoformat() if e.resolution_deadline else ts,
            "votes": [], "summary": e.description,
        }
    if t == EventType.COMMAND_VOTE:
        e = cast(CommandVoteEvent, event)
        # Role weighting (2x in your domain) belongs to the coordinator's conflicts module; the actor records 1
        return "conflict.vote", {"conflict_id": e.plan_item_id or "", "user_id": e.issued_by, "option": e.option_id, "weight": 1}
    if t == EventType.CONFLICT_RESOLVED:
        e = cast(ConflictResolvedEvent, event)
        return "conflict.closed", {"conflict_id": e.conflict_id, "result": e.resolution, "resolved_by": e.resolved_by}

    if t == EventType.QUESTION_ASKED:
        e = cast(QuestionAskedEvent, event)
        return "question.opened", {
            "id": e.question_id, "room_id": room, "task_id": e.task_id or "", "text": e.question, "options": e.options,
            "default_option": e.default_option or "", "status": "open",
            "expires_at": e.expires_at.isoformat() if e.expires_at else ts, "created_at": ts,
        }
    if t == EventType.QUESTION_ANSWERED:
        e = cast(QuestionAnsweredEvent, event)
        return "question.answered", {"question_id": e.question_id, "answer": e.answer, "by_user_id": e.answered_by}
    if t == EventType.COMMAND_ANSWER_QUESTION:
        e = cast(CommandAnswerQuestionEvent, event)
        return "question.answered", {"question_id": e.question_id, "answer": e.answer, "by_user_id": e.issued_by}

    if t in (EventType.FILE_CREATED, EventType.FILE_UPDATED):
        e = cast(FileCreatedEvent | FileUpdatedEvent, event)
        author = e.created_by if isinstance(e, FileCreatedEvent) else e.updated_by
        return "file.changed", {
            "path": e.path, "hash": e.hash or "", "version": e.version, "author_id": author,
            "is_manual": True, "created_at": ts,
        }

    if t == EventType.FILE_DELETED:
        e = cast(FileDeletedEvent, event)
        return "file.deleted", {"path": e.path, "author_id": e.deleted_by, "created_at": ts}

    if t == EventType.CHECKPOINT_CREATED:
        e = cast(CheckpointCreatedEvent, event)
        return "checkpoint.created", {
            # The checkpoint captures the state up to the event before it; rewinding to it keeps it (seq <= target)
            "id": e.checkpoint_id, "room_id": room, "seq": e.sequence - 1, "manifest_id": "",
            "sandbox_snapshot_uuid": None, "plan": [], "task_log_id": "", "parent_id": e.parent_id,
            "created_at": ts, "description": e.description,
        }
    if t == EventType.COMMAND_REWIND:
        e = cast(CommandRewindEvent, event)
        return "room.rewound", {
            "checkpoint_id": e.target_checkpoint_id, "seq": e.target_sequence,
            "return_checkpoint_id": e.return_checkpoint_id,
            "plan": e.plan, "files": e.files,
        }
    if t == EventType.CHECKPOINT_RESTORED:
        e = cast(CheckpointRestoredEvent, event)
        return "room.rewound", {
            "checkpoint_id": e.checkpoint_id, "seq": e.restore_point,
            "plan": e.plan, "files": e.files,
        }

    if t in (EventType.BUDGET_EXCEEDED, EventType.BUDGET_RESUMED):
        e = cast(BudgetExceededEvent, event)
        return "budget.updated", {
            "tokens_used": e.tokens_used, "runs_used": e.sandbox_runs_used,
            "tokens_cap": e.token_cap, "runs_cap": e.sandbox_run_cap,
        }

    return None


def to_envelope(event: BaseEvent) -> Optional[dict[str, Any]]:
    """The catalog envelope for an actor event, or None if it has no catalog counterpart."""
    mapped = _payload(event)
    if mapped is None:
        return None
    type_, payload = mapped
    actor = event.user_id or "system"
    return {
        "seq": event.sequence,
        "room_id": event.room_id,
        "type": type_,
        "actor": actor,
        "actor_id": actor,  # the web types use actor_id; the catalog says actor
        "ts": _ts(event),
        "payload": payload,
    }


def ephemeral(room_id: str, seq: int, type_: str, actor: str, payload: dict[str, Any], ts: str) -> dict[str, Any]:
    """An unstored socket message (presence.tab, ...).

    Pass the room's current seq: clients resume from the last seq they saw, so an ephemeral
    message must neither skip ahead nor reset it.
    """
    return {"seq": seq, "room_id": room_id, "type": type_, "actor": actor, "actor_id": actor, "ts": ts, "payload": payload}
