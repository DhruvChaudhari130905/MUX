"""Pydantic event models. Exported to JSON Schema, and TypeScript types are generated from them."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field, ConfigDict, field_serializer
from uuid import UUID, uuid4


class EventType(str, Enum):
    """Types of events in the system."""
    # Room events
    ROOM_CREATED = "room_created"
    ROOM_JOINED = "room_joined"
    ROOM_LEFT = "room_left"
    ROOM_CLOSED = "room_closed"
    ROOM_SHARING_UPDATED = "room_sharing_updated"
    ROOM_INVITE_CREATED = "room_invite_created"
    ROOM_INVITE_REVOKED = "room_invite_revoked"
    ROOM_PASSWORD_SET = "room_password_set"
    ROOM_MCP_SERVER_SAVED = "room_mcp_server_saved"
    ROOM_MCP_SERVER_REMOVED = "room_mcp_server_removed"
    ROOM_MCP_ADMIN_TOGGLED = "room_mcp_admin_toggled"
    ROOM_AI_SETTINGS_SAVED = "room_ai_settings_saved"
    ROOM_AI_SETTINGS_CLEARED = "room_ai_settings_cleared"
    KICKOFF_REQUESTED = "kickoff_requested"
    ROOM_SKILLS_SET = "room_skills_set"
    CODER_ROLES_UPDATED = "coder_roles_updated"
    MEMBER_ACCESS_UPDATED = "member_access_updated"
    CHANGE_REQUESTED = "change_requested"
    CHANGE_REQUEST_DECIDED = "change_request_decided"

    # User presence events
    USER_JOINED = "user_joined"
    USER_LEFT = "user_left"
    USER_TYPING = "user_typing"
    USER_PRESENCE_CHANGED = "user_presence_changed"

    # Frontend-compatible presence event types (aliases)
    PRESENCE_JOIN = "presence.join"
    PRESENCE_LEAVE = "presence.leave"
    PRESENCE_TYPING = "presence.typing"
    PRESENCE_TAB = "presence.tab"

    # Plan events
    PLAN_CREATED = "plan_created"
    PLAN_UPDATED = "plan_updated"
    PLAN_ITEM_ADDED = "plan_item_added"
    PLAN_ITEM_UPDATED = "plan_item_updated"
    PLAN_ITEM_REMOVED = "plan_item_removed"
    PLAN_ITEM_COMPLETED = "plan_item_completed"

    # Frontend-compatible plan event types (aliases)
    PLAN_ITEM_ADDED_ALIAS = "plan.item_added"
    PLAN_ITEM_UPDATED_ALIAS = "plan.item_updated"
    PLAN_APPROVED = "plan.approved"

    # Message events
    USER_MESSAGE_SENT = "user_message_sent"
    MESSAGE_REACTION_TOGGLED = "message_reaction_toggled"
    AI_MESSAGE_STARTED = "ai_message_started"
    AI_MESSAGE_CHUNK = "ai_message_chunk"
    AI_MESSAGE_COMPLETED = "ai_message_completed"

    # Frontend-compatible message event types (aliases)
    MESSAGE_POSTED = "message.posted"
    MESSAGE_LABELED = "message.labeled"

    # Command events (matching commands.py docstring)
    COMMAND_STEER = "command_steer"
    COMMAND_VOTE = "command_vote"
    COMMAND_OVERRIDE = "command_override"
    COMMAND_APPROVE_PLAN = "command_approve_plan"
    COMMAND_EDIT_PLAN = "command_edit_plan"
    COMMAND_ANSWER_QUESTION = "command_answer_question"
    COMMAND_REWIND = "command_rewind"
    COMMAND_END_SESSION = "command_end_session"

    # Frontend-compatible command event types (aliases)
    CONFLICT_VOTE = "conflict.vote"
    CONFLICT_OVERRIDE = "conflict.override"
    QUESTION_ANSWER = "question.answer"

    # Sitting events
    SITTING_ENDED = "sitting_ended"

    # Frontend-compatible sitting event types (aliases)
    TASK_STARTED = "task.started"
    TASK_FINISHED = "task.finished"

    # Budget events
    BUDGET_EXCEEDED = "budget_exceeded"
    BUDGET_RESUMED = "budget_resumed"

    # Frontend-compatible budget event types (aliases)
    BUDGET_UPDATED = "budget.updated"

    # File events
    FILE_CREATED = "file_created"
    FILE_UPDATED = "file_updated"
    FILE_DELETED = "file_deleted"
    FILE_RENAMED = "file_renamed"

    # Frontend-compatible file event types (aliases)
    FILE_CHANGED = "file.changed"

    # Checkpoint events
    CHECKPOINT_CREATED = "checkpoint_created"
    CHECKPOINT_RESTORED = "checkpoint_restored"

    # Frontend-compatible checkpoint event types (aliases)
    ROOM_REWOUND = "room.rewound"

    # Conflict events
    CONFLICT_DETECTED = "conflict_detected"
    CONFLICT_RESOLVED = "conflict_resolved"

    # Frontend-compatible conflict event types (aliases)
    CONFLICT_OPENED = "conflict.opened"
    CONFLICT_CLOSED = "conflict.closed"

    # Question events
    QUESTION_ASKED = "question_asked"
    QUESTION_ANSWERED = "question_answered"

    # Frontend-compatible question event types (aliases)
    QUESTION_OPENED = "question.opened"
    QUESTION_DEFAULTED = "question.defaulted"

    # Integration events
    GITHUB_SYNC_STARTED = "github_sync_started"
    GITHUB_SYNC_COMPLETED = "github_sync_completed"
    TAVILY_SEARCH_COMPLETED = "tavily_search_completed"

    # System events
    SYSTEM_ERROR = "system_error"
    SYSTEM_WARNING = "system_warning"
    # Agent output with no state of its own (message labels, coordinator replies, research, tool calls)
    AGENT_NOTICE = "agent_notice"


def _utcnow() -> datetime:
    """Timezone-aware UTC now (datetime.utcnow is deprecated and naive)."""
    return datetime.now(timezone.utc)


# Base event model
class BaseEvent(BaseModel):
    """Base model for all events."""
    id: UUID = Field(default_factory=uuid4, description="Unique event identifier")
    type: EventType = Field(..., description="Event type")
    room_id: str = Field(..., description="Room identifier")
    user_id: Optional[str] = Field(default=None, description="User who triggered the event (if applicable)")
    timestamp: datetime = Field(default_factory=_utcnow, description="Event timestamp")
    sequence: int = Field(..., description="Monotonically increasing sequence number within room")
    prev_event_id: Optional[UUID] = Field(default=None, description="ID of previous event in the room")

    model_config = ConfigDict(
        serialize_by_alias=True,
    )

    @field_serializer('timestamp')
    def serialize_timestamp(self, value: datetime) -> str:
        return value.isoformat()

    @field_serializer('id', 'prev_event_id')
    def serialize_uuid(self, value: Optional[UUID]) -> Optional[str]:
        return str(value) if value else None


# Room events
class RoomCreatedEvent(BaseEvent):
    """Room was created."""
    type: EventType = EventType.ROOM_CREATED
    room_name: str = Field(..., description="Name of the room")
    room_description: Optional[str] = Field(default=None, description="Description of the room")
    created_by: str = Field(..., description="User ID of creator")
    initial_plan: Optional[List[Dict[str, Any]]] = Field(default=None, description="Initial plan items")
    domain_role: Optional[str] = Field(default=None, description="Owner's domain role (pm, design, eng)")


class RoomJoinedEvent(BaseEvent):
    """User became a member of the room (persistent membership, unlike presence)."""
    type: EventType = EventType.ROOM_JOINED
    user_id: str = Field(..., description="User who joined")  # pyright: ignore[reportIncompatibleVariableOverride]
    user_name: Optional[str] = Field(default=None, description="Display name of user")
    role: str = Field("editor", description="Membership role granted (editor or viewer)")
    granted_by: Optional[str] = Field(default=None, description="User who granted membership")
    domain_role: Optional[str] = Field(default=None, description="Member's domain role (pm, design, eng)")


class RoomLeftEvent(BaseEvent):
    """User left the room."""
    type: EventType = EventType.ROOM_LEFT
    user_id: str = Field(..., description="User who left")  # pyright: ignore[reportIncompatibleVariableOverride]


class RoomClosedEvent(BaseEvent):
    """Room was closed."""
    type: EventType = EventType.ROOM_CLOSED
    closed_by: str = Field(..., description="User who closed the room")
    reason: Optional[str] = Field(default=None, description="Reason for closing")


class RoomSharingUpdatedEvent(BaseEvent):
    """Room sharing settings were changed."""
    type: EventType = EventType.ROOM_SHARING_UPDATED
    public: bool = Field(..., description="Whether any authenticated user can view and join the room")
    allow_anonymous: bool = Field(False, description="Allow anonymous access")
    updated_by: str = Field(..., description="User who changed the settings")
    # Events written before link permissions existed replay as "editor", which is what joining granted then
    link_permission: str = Field("editor", description="Role granted by joining through the link (editor or viewer)")


class CoderRolesUpdatedEvent(BaseEvent):
    """The owner replaced the room's coder roles (mux/rooms/file_access.py)."""
    type: EventType = EventType.CODER_ROLES_UPDATED
    roles: Dict[str, List[str]] = Field(..., description="Role name -> folders")
    updated_by: str = Field(..., description="User who changed the roles")


class MemberAccessUpdatedEvent(BaseEvent):
    """The owner set an editor's coder role and personal folders."""
    type: EventType = EventType.MEMBER_ACCESS_UPDATED
    member_id: str = Field(..., description="Editor whose access changed")
    coder_role: Optional[str] = Field(default=None, description="Coder role name, or None")
    folders: List[str] = Field(default_factory=list, description="Folders of their own, on top of the role's")
    updated_by: str = Field(..., description="User who changed the access")


class ChangeRequestedEvent(BaseEvent):
    """A change outside the person's folders was held for approval instead of saved."""
    type: EventType = EventType.CHANGE_REQUESTED
    request_id: str = Field(..., description="Change request id")
    path: str = Field(..., description="File the change is to")
    content: Optional[str] = Field(default=None, description="New content; None deletes the file")
    base_version: Optional[int] = Field(default=None, description="Version the change was based on")
    requested_by: str = Field(..., description="Person the change is for")
    via: str = Field("person", description="person (their own edit) or agent (the coder, for them)")
    task_id: Optional[str] = Field(default=None, description="Plan task the agent was on, for agent changes")
    approvers: List[str] = Field(default_factory=list, description="Who could approve it when it was made")


class ChangeRequestDecidedEvent(BaseEvent):
    """A change request was approved, rejected, withdrawn or found stale."""
    type: EventType = EventType.CHANGE_REQUEST_DECIDED
    request_id: str = Field(..., description="Change request id")
    status: str = Field(..., description="approved, rejected, withdrawn or stale")
    decided_by: str = Field(..., description="Who decided")


class RoomInviteCreatedEvent(BaseEvent):
    """The owner invited an email address; whoever signs in with it and joins gets the role."""
    type: EventType = EventType.ROOM_INVITE_CREATED
    email: str = Field(..., description="Invited email address, lowercased")
    role: str = Field("editor", description="Role granted on joining (editor or viewer)")
    invited_by: str = Field(..., description="User who sent the invite")


class RoomInviteRevokedEvent(BaseEvent):
    """A pending invite ended: the owner removed it, or the invited person joined."""
    type: EventType = EventType.ROOM_INVITE_REVOKED
    email: str = Field(..., description="Invited email address, lowercased")
    accepted: bool = Field(False, description="True when the invite ended because the person joined")


class RoomPasswordSetEvent(BaseEvent):
    """The owner set, changed or removed the room password. Never sent to clients (mux/events/wire.py)."""
    type: EventType = EventType.ROOM_PASSWORD_SET
    password_hash: Optional[str] = Field(default=None, description="scrypt hash (mux/rooms/access.py); None removes it")


class RoomMcpServerSavedEvent(BaseEvent):
    """The owner added or changed one of the room's own MCP servers. Header values are encrypted."""
    type: EventType = EventType.ROOM_MCP_SERVER_SAVED
    name: str = Field(..., description="Server name, unique in the room")
    url: str = Field(..., description="https URL of the server")
    headers: Dict[str, str] = Field(default_factory=dict, description="Header name -> encrypted value (mux/secrets.py)")
    tools: List[Dict[str, Any]] = Field(default_factory=list, description="Tools listed when it was added or refreshed")
    settings: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description="Tool -> {enabled, mode}")


class RoomMcpServerRemovedEvent(BaseEvent):
    """The owner removed one of the room's MCP servers."""
    type: EventType = EventType.ROOM_MCP_SERVER_REMOVED
    name: str = Field(..., description="Server name")


class RoomMcpAdminToggledEvent(BaseEvent):
    """The owner turned a server-wide (mcp.json) server on or off for this room, or changed its tool settings."""
    type: EventType = EventType.ROOM_MCP_ADMIN_TOGGLED
    name: str = Field(..., description="Server name in mcp.json")
    enabled: bool = Field(..., description="Whether the coder gets this server's tools in this room")
    settings: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description="Tool -> {enabled, mode}")


class RoomAiSettingsSavedEvent(BaseEvent):
    """The owner set the room's AI provider. The key is encrypted (mux/secrets.py) and never sent to clients."""
    type: EventType = EventType.ROOM_AI_SETTINGS_SAVED
    provider: str = Field(..., description="Preset label (mux/agents/room_llm.py PROVIDERS)")
    base_url: str = Field(..., description="OpenAI-compatible API base URL")
    api_key: str = Field(..., description="Encrypted API key")
    models: Dict[str, str] = Field(..., description="Role (lightning, super, ultra) -> model id")


class RoomAiSettingsClearedEvent(BaseEvent):
    """The owner switched the room back to the server's model."""
    type: EventType = EventType.ROOM_AI_SETTINGS_CLEARED


class KickoffRequestedEvent(BaseEvent):
    """The owner asked MUX to plan the room with the team ("Plan it with me")."""
    type: EventType = EventType.KICKOFF_REQUESTED
    requested_by: str = Field(..., description="User who asked")
    brief: Optional[str] = Field(default=None, max_length=2000, description="What the owner wants the project to do")


class RoomSkillsSetEvent(BaseEvent):
    """The owner chose which skills (mux/skills) the room's coder may use."""
    type: EventType = EventType.ROOM_SKILLS_SET
    enabled: List[str] = Field(default_factory=list, description="Skill names, sorted")


# Presence events
class UserJoinedEvent(BaseEvent):
    """User joined the room session."""
    type: EventType = EventType.USER_JOINED
    user_id: str = Field(..., description="User who joined")  # pyright: ignore[reportIncompatibleVariableOverride]
    user_name: Optional[str] = Field(default=None, description="Display name")


class UserLeftEvent(BaseEvent):
    """User left the room session."""
    type: EventType = EventType.USER_LEFT
    user_id: str = Field(..., description="User who left")  # pyright: ignore[reportIncompatibleVariableOverride]


class UserTypingEvent(BaseEvent):
    """User is typing."""
    type: EventType = EventType.USER_TYPING
    user_id: str = Field(..., description="User who is typing")  # pyright: ignore[reportIncompatibleVariableOverride]
    is_typing: bool = Field(True, description="Typing status")


class UserPresenceChangedEvent(BaseEvent):
    """User presence changed (away/online/etc)."""
    type: EventType = EventType.USER_PRESENCE_CHANGED
    user_id: str = Field(..., description="User whose presence changed")  # pyright: ignore[reportIncompatibleVariableOverride]
    presence: str = Field(..., description="New presence state (online, away, offline)")


# Plan events
class PlanCreatedEvent(BaseEvent):
    """Initial plan was created."""
    type: EventType = EventType.PLAN_CREATED
    plan: List[Dict[str, Any]] = Field(..., description="Initial plan items")
    created_by: str = Field(..., description="User who created the plan")


class PlanUpdatedEvent(BaseEvent):
    """Plan was updated (replaced)."""
    type: EventType = EventType.PLAN_UPDATED
    plan: List[Dict[str, Any]] = Field(..., description="New plan items")
    updated_by: str = Field(..., description="User who made the update")


class PlanItemAddedEvent(BaseEvent):
    """New item was added to the plan."""
    type: EventType = EventType.PLAN_ITEM_ADDED
    item_id: str = Field(..., description="Unique ID of the plan item")
    title: str = Field(..., description="Title of the plan item")
    description: Optional[str] = Field(default=None, description="Description of the plan item")
    position: int = Field(..., description="Position in the plan (0-based)")
    added_by: str = Field(..., description="User who added the item")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional metadata")


class PlanItemUpdatedEvent(BaseEvent):
    """Existing plan item was updated."""
    type: EventType = EventType.PLAN_ITEM_UPDATED
    item_id: str = Field(..., description="ID of the plan item to update")
    updates: Dict[str, Any] = Field(..., description="Fields to update")
    updated_by: str = Field(..., description="User who made the update")


class PlanItemRemovedEvent(BaseEvent):
    """Plan item was removed."""
    type: EventType = EventType.PLAN_ITEM_REMOVED
    item_id: str = Field(..., description="ID of the removed plan item")
    removed_by: str = Field(..., description="User who removed the item")


class PlanItemCompletedEvent(BaseEvent):
    """Plan item was marked as completed."""
    type: EventType = EventType.PLAN_ITEM_COMPLETED
    item_id: str = Field(..., description="ID of the completed plan item")
    completed_by: str = Field(..., description="User who completed the item")
    completed_at: datetime = Field(default_factory=_utcnow, description="Completion timestamp")


# Message events
class UserMessageSentEvent(BaseEvent):
    """User sent a message."""
    type: EventType = EventType.USER_MESSAGE_SENT
    message_id: str = Field(..., description="Unique message identifier")
    content: str = Field(..., description="Message content")
    user_id: str = Field(..., description="User who sent the message")  # pyright: ignore[reportIncompatibleVariableOverride]
    user_name: Optional[str] = Field(default=None, description="Display name of sender")
    reply_to: Optional[str] = Field(default=None, description="ID of message being replied to")
    to: Optional[str] = Field(default=None, description="'agent' (steers the coordinator) or 'team' (a note between people)")
    attachments: list[str] = Field(default_factory=list, description="Upload ids of photos (Team notes only)")


class MessageReactionToggledEvent(BaseEvent):
    """A person added or removed an emoji reaction on a person's message."""
    type: EventType = EventType.MESSAGE_REACTION_TOGGLED
    message_id: str = Field(..., description="The reacted-to message (a user_message_sent message_id)")
    user_id: str = Field(..., description="Who reacted")  # pyright: ignore[reportIncompatibleVariableOverride]
    emoji: str = Field(..., description="The emoji")
    added: bool = Field(..., description="True when added, False when removed")


class AIMessageStartedEvent(BaseEvent):
    """AI started generating a message."""
    type: EventType = EventType.AI_MESSAGE_STARTED
    message_id: str = Field(..., description="Unique message identifier")
    trigger: str = Field(..., description="What triggered the AI response")
    model_used: str = Field(..., description="LLM model being used")


class AIMessageChunkEvent(BaseEvent):
    """Chunk of AI-generated message content."""
    type: EventType = EventType.AI_MESSAGE_CHUNK
    message_id: str = Field(..., description="Unique message identifier")
    chunk: str = Field(..., description="Text chunk")
    chunk_index: int = Field(..., description="Sequence number of this chunk")


class AIMessageCompletedEvent(BaseEvent):
    """AI finished generating a message."""
    type: EventType = EventType.AI_MESSAGE_COMPLETED
    message_id: str = Field(..., description="Unique message identifier")
    full_content: str = Field(..., description="Complete message content")
    usage: Optional[Dict[str, Any]] = Field(default=None, description="Token usage statistics")
    finish_reason: Optional[str] = Field(default=None, description="Why generation stopped")


# Command events (matching commands.py docstring)
class CommandSteerEvent(BaseEvent):
    """Steer command issued."""
    type: EventType = EventType.COMMAND_STEER
    instructions: str = Field(..., description="Natural language steering instructions")
    issued_by: str = Field(..., description="User who issued the command")
    parameters: Optional[Dict[str, Any]] = Field(default=None, description="Additional parameters")


class CommandVoteEvent(BaseEvent):
    """Vote command issued."""
    type: EventType = EventType.COMMAND_VOTE
    option_id: str = Field(..., description="ID of option being voted for")
    plan_item_id: Optional[str] = Field(default=None, description="Specific plan item being voted on")
    vote_value: Union[bool, int, str] = Field(..., description="Vote value (yes/no, score, etc.)")
    issued_by: str = Field(..., description="User who issued the command")


class CommandOverrideEvent(BaseEvent):
    """Override plan command issued."""
    type: EventType = EventType.COMMAND_OVERRIDE
    new_plan: List[Dict[str, Any]] = Field(..., description="New plan to replace current one")
    issued_by: str = Field(..., description="User who issued the command")
    reason: Optional[str] = Field(default=None, description="Reason for override")


class CommandApprovePlanEvent(BaseEvent):
    """Approve plan command issued."""
    type: EventType = EventType.COMMAND_APPROVE_PLAN
    plan_item_ids: List[str] = Field(..., description="IDs of plan items to approve")
    issued_by: str = Field(..., description="User who issued the command")
    approval_note: Optional[str] = Field(default=None, description="Optional note about approval")


class CommandEditPlanEvent(BaseEvent):
    """Edit plan command issued."""
    type: EventType = EventType.COMMAND_EDIT_PLAN
    edits: List[Dict[str, Any]] = Field(..., description="List of edits to apply")
    issued_by: str = Field(..., description="User who issued the command")


class CommandAnswerQuestionEvent(BaseEvent):
    """Answer question command issued."""
    type: EventType = EventType.COMMAND_ANSWER_QUESTION
    question_id: str = Field(..., description="ID of the question being answered")
    answer: str = Field(..., description="The answer text")
    issued_by: str = Field(..., description="User who issued the command")


class CommandRewindEvent(BaseEvent):
    """Rewind command issued."""
    type: EventType = EventType.COMMAND_REWIND
    target_sequence: int = Field(..., description="Event sequence number to rewind to")
    issued_by: str = Field(..., description="User who issued the command")
    reason: Optional[str] = Field(default=None, description="Reason for rewinding")
    preserve_checkpoint: bool = Field(False, description="Whether to create checkpoint before rewind")
    target_checkpoint_id: Optional[str] = Field(default=None, description="Checkpoint selected on the timeline")
    return_checkpoint_id: Optional[str] = Field(default=None, description="Saved live state from before this rewind")
    plan: List[Dict[str, Any]] = Field(default_factory=list, description="Restored plan for live clients")
    files: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description="Restored file index for live clients")


class CommandEndSessionEvent(BaseEvent):
    """End session command issued."""
    type: EventType = EventType.COMMAND_END_SESSION
    issued_by: str = Field(..., description="User who issued the command")
    reason: Optional[str] = Field(default=None, description="Reason for ending session")
    cleanup_data: bool = Field(True, description="Whether to cleanup temporary data")


# Task events
class TaskStartedEvent(BaseEvent):
    """Task was started (plan item status changed to 'doing')."""
    type: EventType = EventType.TASK_STARTED
    item_id: str = Field(..., description="ID of the plan item that was started")
    title: str = Field(..., description="Title of the plan item")
    started_by: str = Field(..., description="User who started the task")


class TaskFinishedEvent(BaseEvent):
    """Task was finished (plan item status changed to 'done')."""
    type: EventType = EventType.TASK_FINISHED
    item_id: str = Field(..., description="ID of the plan item that was finished")
    title: str = Field(..., description="Title of the plan item")
    finished_by: str = Field(..., description="User who finished the task")


# Sitting events
class SittingEndedEvent(BaseEvent):
    """Sitting ended (all members idle or owner ended session)."""
    type: EventType = EventType.SITTING_ENDED
    reason: str = Field(..., description="Reason for ending: 'idle_timeout' or 'owner_ended'")
    duration_seconds: float = Field(..., description="Total sitting duration in seconds")
    participant_count: int = Field(..., description="Number of participants in the sitting")


# Budget events
class BudgetExceededEvent(BaseEvent):
    """Budget cap exceeded or budget resumed."""
    type: EventType = EventType.BUDGET_EXCEEDED
    reason: str = Field(..., description="Reason: 'token_cap_exceeded', 'sandbox_run_cap_exceeded', or 'resumed'")
    tokens_used: int = Field(..., description="Current tokens used")
    token_cap: int = Field(..., description="Token cap")
    sandbox_runs_used: int = Field(..., description="Current sandbox runs used")
    sandbox_run_cap: int = Field(..., description="Sandbox run cap")


# File events
class FileCreatedEvent(BaseEvent):
    """File was created."""
    type: EventType = EventType.FILE_CREATED
    file_id: str = Field(..., description="Unique file identifier")
    path: str = Field(..., description="File path")
    name: str = Field(..., description="File name")
    content: Optional[str] = Field(default=None, description="Initial file content")
    file_type: Optional[str] = Field(default=None, description="MIME type or file extension")
    size: int = Field(0, description="File size in bytes")
    created_by: str = Field(..., description="User who created the file")
    hash: Optional[str] = Field(default=None, description="Content hash")
    version: int = Field(0, description="Per-path version after this change (0: not recorded)")


class FileUpdatedEvent(BaseEvent):
    """File was updated."""
    type: EventType = EventType.FILE_UPDATED
    file_id: str = Field(..., description="Unique file identifier")
    path: str = Field(..., description="File path")
    content: Optional[str] = Field(default=None, description="New file content")
    content_delta: Optional[Dict[str, Any]] = Field(default=None, description="Changes made to content")
    size: int = Field(..., description="New file size in bytes")
    updated_by: str = Field(..., description="User who updated the file")
    hash: Optional[str] = Field(default=None, description="Content hash")
    version: int = Field(0, description="Per-path version after this change (0: not recorded)")


class FileDeletedEvent(BaseEvent):
    """File was deleted."""
    type: EventType = EventType.FILE_DELETED
    file_id: str = Field(..., description="Unique file identifier")
    path: str = Field(..., description="File path")
    deleted_by: str = Field(..., description="User who deleted the file")


class FileRenamedEvent(BaseEvent):
    """File was renamed."""
    type: EventType = EventType.FILE_RENAMED
    file_id: str = Field(..., description="Unique file identifier")
    old_path: str = Field(..., description="Original file path")
    new_path: str = Field(..., description="New file path")
    old_name: str = Field(..., description="Original file name")
    new_name: str = Field(..., description="New file name")
    renamed_by: str = Field(..., description="User who renamed the file")


# Checkpoint events
class CheckpointCreatedEvent(BaseEvent):
    """Checkpoint was created."""
    type: EventType = EventType.CHECKPOINT_CREATED
    checkpoint_id: str = Field(..., description="Unique checkpoint identifier")
    description: Optional[str] = Field(default=None, description="Description of what the checkpoint saves")
    created_by: str = Field(..., description="User or system that created the checkpoint")
    includes_files: bool = Field(True, description="Whether file states are included")
    includes_plan: bool = Field(True, description="Whether plan state is included")
    parent_id: Optional[str] = Field(default=None, description="Checkpoint this checkpoint branches from")


class CheckpointRestoredEvent(BaseEvent):
    """Checkpoint was restored."""
    type: EventType = EventType.CHECKPOINT_RESTORED
    checkpoint_id: str = Field(..., description="ID of the checkpoint that was restored")
    restored_by: str = Field(..., description="User or system that initiated the restore")
    restore_point: int = Field(..., description="Event sequence number restored to")
    plan: List[Dict[str, Any]] = Field(default_factory=list, description="Restored plan for live clients")
    files: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description="Restored file index for live clients")


# Conflict events
class ConflictDetectedEvent(BaseEvent):
    """Conflict was detected between messages or proposals."""
    type: EventType = EventType.CONFLICT_DETECTED
    conflict_id: str = Field(..., description="Unique conflict identifier")
    description: str = Field(..., description="Description of the conflict")
    involved_messages: List[str] = Field(..., description="IDs of messages involved in conflict")
    conflict_type: str = Field(..., description="Type of conflict (content, plan, etc.)")
    detected_by: str = Field(..., description="User or system that detected the conflict")
    resolution_deadline: Optional[datetime] = Field(default=None, description="When conflict should be resolved by")
    options: List[str] = Field(default_factory=list, description="Choices the room votes on")
    task_id: Optional[str] = Field(default=None, description="Plan task the conflict blocks")


class ConflictResolvedEvent(BaseEvent):
    """Conflict was resolved."""
    type: EventType = EventType.CONFLICT_RESOLVED
    conflict_id: str = Field(..., description="ID of the resolved conflict")
    resolution: str = Field(..., description="How the conflict was resolved")
    resolved_by: str = Field(..., description="User who resolved the conflict")
    resolution_details: Optional[Dict[str, Any]] = Field(default=None, description="Additional resolution details")


# Question events
class QuestionAskedEvent(BaseEvent):
    """Question was asked in the room."""
    type: EventType = EventType.QUESTION_ASKED
    question_id: str = Field(..., description="Unique question identifier")
    question: str = Field(..., description="The question text")
    asked_by: str = Field(..., description="User who asked the question")
    context: Optional[str] = Field(default=None, description="Context in which question was asked")
    requires_answer: bool = Field(True, description="Whether an answer is expected")
    options: List[str] = Field(default_factory=list, description="Choices offered")
    default_option: Optional[str] = Field(default=None, description="Used if nobody answers in time")
    task_id: Optional[str] = Field(default=None, description="Plan task waiting on the answer")
    expires_at: Optional[datetime] = Field(default=None, description="When the default applies")


class QuestionAnsweredEvent(BaseEvent):
    """Question was answered."""
    type: EventType = EventType.QUESTION_ANSWERED
    question_id: str = Field(..., description="ID of the question that was answered")
    answer: str = Field(..., description="The answer text")
    answered_by: str = Field(..., description="User who provided the answer")
    accepted: bool = Field(False, description="Whether the answer was accepted as correct")


# Integration events
class GithubSyncStartedEvent(BaseEvent):
    """GitHub synchronization started."""
    type: EventType = EventType.GITHUB_SYNC_STARTED
    repository: str = Field(..., description="Repository being synced")
    branch: str = Field(..., description="Branch being synced")
    initiated_by: str = Field(..., description="User or system that initiated the sync")


class GithubSyncCompletedEvent(BaseEvent):
    """GitHub synchronization completed."""
    type: EventType = EventType.GITHUB_SYNC_COMPLETED
    repository: str = Field(..., description="Repository that was synced")
    branch: str = Field(..., description="Branch that was synced")
    changes_applied: int = Field(..., description="Number of changes applied")
    initiated_by: str = Field(..., description="User or system that initiated the sync")
    success: bool = Field(..., description="Whether the sync was successful")
    error_message: Optional[str] = Field(default=None, description="Error message if sync failed")


class TavilySearchCompletedEvent(BaseEvent):
    """Tavily web search completed."""
    type: EventType = EventType.TAVILY_SEARCH_COMPLETED
    query: str = Field(..., description="Search query that was executed")
    results: List[Dict[str, Any]] = Field(..., description="Search results")
    initiated_by: str = Field(..., description="User or system that initiated the search")
    result_count: int = Field(..., description="Number of results returned")
    search_time: float = Field(..., description="Time taken to execute search (seconds)")


# System events
class SystemErrorEvent(BaseEvent):
    """System error occurred."""
    type: EventType = EventType.SYSTEM_ERROR
    error_code: str = Field(..., description="Error code identifier")
    error_message: str = Field(..., description="Human-readable error message")
    severity: str = Field(..., description="Error severity (low, medium, high, critical)")
    component: str = Field(..., description="System component where error occurred")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Additional error details")


class AgentNoticeEvent(BaseEvent):
    """Agent output stored for the feed, with no effect on room state.

    `kind` is its event-catalog type (message.labeled, coordinator.reply, conflict.evidence,
    tool.called, tool.result, build.result, question.defaulted); `data` is the catalog payload.
    """
    type: EventType = EventType.AGENT_NOTICE
    kind: str = Field(..., description="Event-catalog type")
    data: Dict[str, Any] = Field(default_factory=dict, description="Event-catalog payload")


class SystemWarningEvent(BaseEvent):
    """System warning occurred."""
    type: EventType = EventType.SYSTEM_WARNING
    warning_code: str = Field(..., description="Warning code identifier")
    warning_message: str = Field(..., description="Human-readable warning message")
    severity: str = Field(..., description="Warning severity (low, medium, high)")
    component: str = Field(..., description="System component where warning occurred")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Additional warning details")


# Union type for all events (useful for type hints)
Event = Union[
    RoomCreatedEvent,
    RoomJoinedEvent,
    RoomLeftEvent,
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
    AIMessageStartedEvent,
    AIMessageChunkEvent,
    AIMessageCompletedEvent,
    CommandSteerEvent,
    CommandVoteEvent,
    CommandOverrideEvent,
    CommandApprovePlanEvent,
    CommandEditPlanEvent,
    CommandAnswerQuestionEvent,
    CommandRewindEvent,
    CommandEndSessionEvent,
    FileCreatedEvent,
    FileUpdatedEvent,
    FileDeletedEvent,
    FileRenamedEvent,
    CheckpointCreatedEvent,
    CheckpointRestoredEvent,
    ConflictDetectedEvent,
    ConflictResolvedEvent,
    QuestionAskedEvent,
    QuestionAnsweredEvent,
    GithubSyncStartedEvent,
    GithubSyncCompletedEvent,
    TavilySearchCompletedEvent,
    SystemErrorEvent,
    SystemWarningEvent,
    SittingEndedEvent,
    BudgetExceededEvent,
    TaskStartedEvent,
    TaskFinishedEvent
]


# Export all models for schema generation
__all__ = [
    "AgentNoticeEvent",
    "EventType",
    "BaseEvent",
    "RoomCreatedEvent",
    "RoomJoinedEvent",
    "RoomLeftEvent",
    "RoomClosedEvent",
    "RoomSharingUpdatedEvent",
    "CoderRolesUpdatedEvent",
    "MemberAccessUpdatedEvent",
    "ChangeRequestedEvent",
    "ChangeRequestDecidedEvent",
    "RoomInviteCreatedEvent",
    "RoomInviteRevokedEvent",
    "RoomPasswordSetEvent",
    "RoomMcpServerSavedEvent",
    "RoomMcpServerRemovedEvent",
    "RoomMcpAdminToggledEvent",
    "RoomAiSettingsSavedEvent",
    "RoomAiSettingsClearedEvent",
    "KickoffRequestedEvent",
    "RoomSkillsSetEvent",
    "UserJoinedEvent",
    "UserLeftEvent",
    "UserTypingEvent",
    "UserPresenceChangedEvent",
    "PlanCreatedEvent",
    "PlanUpdatedEvent",
    "PlanItemAddedEvent",
    "PlanItemUpdatedEvent",
    "PlanItemRemovedEvent",
    "PlanItemCompletedEvent",
    "UserMessageSentEvent",
    "AIMessageStartedEvent",
    "AIMessageChunkEvent",
    "AIMessageCompletedEvent",
    "CommandSteerEvent",
    "CommandVoteEvent",
    "CommandOverrideEvent",
    "CommandApprovePlanEvent",
    "CommandEditPlanEvent",
    "CommandAnswerQuestionEvent",
    "CommandRewindEvent",
    "CommandEndSessionEvent",
    "FileCreatedEvent",
    "FileUpdatedEvent",
    "FileDeletedEvent",
    "FileRenamedEvent",
    "CheckpointCreatedEvent",
    "CheckpointRestoredEvent",
    "ConflictDetectedEvent",
    "ConflictResolvedEvent",
    "QuestionAskedEvent",
    "QuestionAnsweredEvent",
    "GithubSyncStartedEvent",
    "GithubSyncCompletedEvent",
    "TavilySearchCompletedEvent",
    "SystemErrorEvent",
    "SystemWarningEvent",
    "SittingEndedEvent",
    "BudgetExceededEvent",
    "TaskStartedEvent",
    "TaskFinishedEvent",
    "Event",
    "RoomId",
    "EXEMPT_TYPES",
    "EXEMPT_PREFIXES",
    "is_exempt",
    "EventEnvelope",
    "Payload",
    "FileChanged",
    "CheckpointCreated",
    "RoomRewound",
]


# --- Postgres event envelope and typed payloads (checkpoints, rewind, room files) ---

RoomId = UUID

EXEMPT_TYPES: frozenset[str] = frozenset({
    "room.created", "sharing.changed", "budget.updated", "room.paused", "room.resumed",
    "room.rewound", "checkpoint.created", "message.posted",
})

EXEMPT_PREFIXES = ("member.", "export.")

def is_exempt(type: str) -> bool:
    """True if events of this type are never greyed out."""
    return type in EXEMPT_TYPES or type.startswith(EXEMPT_PREFIXES)

class EventEnvelope(BaseModel):
    """Wire/storage shape of every event."""
    seq: int
    type: str
    ts: datetime
    room_id: RoomId
    actor: str
    payload: dict

class Payload(BaseModel):
    """Payload contract: unknown fields are an error, so the Python and TS shapes can't drift silently."""
    model_config = ConfigDict(extra="forbid")

class FileChanged(Payload):
    """Payload of 'file.changed'."""

    path: str
    hash: str | None
    version: int
    base_version: int | None
    deleted: bool
    actor: str
    diff_summary: str

class CheckpointCreated(Payload):
    """Payload of 'checkpoint.created'. The checkpoint's own seq is the envelope seq (R4)."""
    
    checkpoint_id: UUID
    parent_id: UUID | None
    start_seq: int
    manifest_id: UUID
    sandbox_snapshot_uuid: str | None

class RoomRewound(Payload):
    """Payload of 'room.rewound' (R2): the new version of every path whose content changed, applied as given.
       Paths missing from the target checkpoint's manifest are removed; they keep their high-water marks."""
    checkpoint_id: UUID
    versions: dict[str, int]
