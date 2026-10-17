// MUX Frontend Types - matches the event catalog from architecture.md

export type UserRole = 'owner' | 'editor' | 'viewer';
export type DomainRole = 'pm' | 'design' | 'eng';
export type MessageLabel = 'merge' | 'queue' | 'interrupt' | 'conflict' | 'chat' | 'review';
// agent: instructions for the coordinator (the default). team: notes between people, never sent to the coordinator
export type MessageTo = 'agent' | 'team';
export type PlanItemStatus = 'draft' | 'todo' | 'doing' | 'done' | 'skipped_conflict' | 'skipped_question';
export type ConflictStatus = 'open' | 'voting' | 'closed';
export type QuestionStatus = 'open' | 'answered' | 'defaulted';

export interface User {
  id: string;
  email: string;
  name: string;
  avatar_url?: string;
  initials: string;
  color: string;
}

export interface Membership {
  user_id: string;
  room_id: string;
  permission: UserRole;
  domain_role: DomainRole;
  user: User;
  // Coder role and folders of their own (lib/fileAccess.ts); neither: no folder limits
  coder_role?: string | null;
  folders?: string[];
}

// A change outside the person's folders, waiting for the owner or a coder of that area
export interface ChangeRequest {
  id: string;
  path: string;
  content: string | null; // null: delete the file
  base_version: number | null;
  requested_by: string;
  via: 'person' | 'agent';
  task_id: string | null;
  status: 'pending' | 'approved' | 'rejected' | 'withdrawn' | 'stale';
  decided_by: string | null;
  created_at: string;
  approvers: string[];
}

export interface Room {
  id: string;
  title: string;
  description: string;
  owner_id: string;
  link_access: 'restricted' | 'anyone';
  link_permission: UserRole;
  // A room password lets anyone with the room id join as editor (the password itself never leaves the server)
  has_password?: boolean;
  budget_tokens_cap: number;
  budget_runs_cap: number;
  head_checkpoint_id: string | null;
  created_at: string;
  updated_at?: string;
  members: Membership[];
  coder_roles?: Record<string, string[]>; // role name -> folders
  change_requests?: ChangeRequest[]; // pending, plus recent rejected/stale ones (their requester can copy the edit)
}

// A pending email invite (owner only)
export interface Invite {
  email: string;
  role: 'editor' | 'viewer';
}

export interface InviteResult extends Invite {
  email_sent: boolean;
  email_error?: string | null;
  link: string;
}

// MCP servers whose tools the coder can use in a room (owner manages them in the Tools dialog)
export interface McpTool {
  name: string;
  description: string;
  input_schema?: Record<string, unknown>;
}

export interface McpToolSetting {
  enabled: boolean;
  mode: 'auto' | 'ask';
}

export interface McpServerView {
  name: string;
  url: string;
  header_names: string[];
  tools: McpTool[];
  settings: Record<string, McpToolSetting>;
}

// A server from the MUX server's mcp.json, off in a room until its owner turns it on
export interface McpAdminView {
  name: string;
  kind: 'stdio' | 'http';
  enabled: boolean;
  settings: Record<string, McpToolSetting>;
  tools: McpTool[];
  tools_loaded: boolean;
}

export interface RoomMcp {
  admin: McpAdminView[];
  servers: McpServerView[];
}

// The AI provider a room's agents use: the owner's own key, the MUX server's model, or none
export type AiRole = 'lightning' | 'super' | 'ultra';

// A skill (SKILL.md guidance pack) the room's coder may use; the owner switches them on
export interface RoomSkill {
  name: string;
  description: string;
  source: string;
  compatible: boolean;
  issues: string[];
  enabled: boolean;
  files: number;
  missing: boolean;
}

// The signed-in user's plan (GET /me/plan). room_limit null: no limit
export type PlanName = 'free' | 'pro' | 'enterprise';
export interface UserPlan {
  plan: PlanName;
  label: string;
  room_limit: number | null;
  token_cap: number;
  run_cap: number;
  rooms_owned: number;
}

export interface RoomAi {
  source: 'room' | 'server' | 'none';
  provider: string | null;
  base_url: string | null;
  models: Record<AiRole, string> | null;
  has_key: boolean;
}

export interface PlanItem {
  id: string;
  title: string;
  status: PlanItemStatus;
  owner_role?: DomainRole;
  notes?: string;
  merged_notes?: string[];
  // 'review': a read-only task that posts a written review instead of changing files
  kind?: 'review';
  // People whose requests shaped the task: the agent's edits must suit their folders (set by the server)
  requested_by?: string[];
}

export interface Message {
  id: string;
  room_id: string;
  user_id: string;
  text: string;
  to?: MessageTo;
  // Upload ids of photos in a Team note (GET /rooms/{id}/uploads/{upload_id})
  attachments?: string[];
  // emoji -> ids of the people who reacted with it
  reactions?: Record<string, string[]>;
  // Unset while the coordinator hasn't labeled it yet; team notes never get one
  label?: MessageLabel;
  rationale?: string;
  domain?: 'ui' | 'architecture' | 'scope' | null;
  add_plan_item?: { title: string; after_task_id?: string };
  open_conflict?: {
    with_message_ids: string[];
    summary: string;
    options: string[];
    research_queries: string[];
  };
  reply?: string;
  created_at: string;
  user: User;
}

export interface Conflict {
  id: string;
  room_id: string;
  task_id: string;
  options: string[];
  evidence: { query: string; summary: string; citations: string[] }[];
  domain: 'ui' | 'architecture' | 'scope';
  status: ConflictStatus;
  result?: string;
  resolved_by?: string;
  created_at: string;
  expires_at: string;
  votes: Vote[];
}

export interface Vote {
  conflict_id: string;
  user_id: string;
  option: string;
  weight: number;
}

export interface Question {
  id: string;
  room_id: string;
  task_id: string;
  text: string;
  options: string[];
  default_option: string;
  answer?: string;
  status: QuestionStatus;
  expires_at: string;
  created_at: string;
}

export interface FileChange {
  path: string;
  hash: string;
  version: number;
  diff_summary?: string;
  author_id: string;
  is_manual: boolean;
  created_at: string;
}

export interface Checkpoint {
  id: string;
  room_id: string;
  seq: number;
  manifest_id: string;
  sandbox_snapshot_uuid: string | null;
  plan: PlanItem[];
  task_log_id: string;
  parent_id: string | null;
  created_at: string;
}

export interface Budget {
  tokens_used: number;
  runs_used: number;
  tokens_cap: number;
  runs_cap: number;
}

export interface Presence {
  user_id: string;
  user: User;
  tab: 'feed' | 'preview' | 'code' | 'cards';
  typing?: boolean;
  active: boolean;
  last_seen: string;
}

// One step of the agent's work, shown in the Agent window (from tool.called / tool.result / build.result /
// task.started / task.finished events)
export interface AgentActivity {
  id: string;
  kind: 'task' | 'tool' | 'build' | 'done';
  label: string;
  detail?: string;
  tool?: string;
  ok?: boolean;
  pending?: boolean;
  ts: string;
}

export interface RoomState {
  room: Room;
  plan: PlanItem[];
  messages: Message[];
  activity: AgentActivity[];
  conflicts: Conflict[];
  questions: Question[];
  files: Map<string, { hash: string; version: number }>;
  checkpoints: Checkpoint[];
  budget: Budget;
  presence: Presence[];
  current_user: User;
  current_user_membership: Membership;
}

// Event types for WebSocket
export type EventType =
  | 'room.created'
  | 'member.joined'
  | 'member.role_changed'
  | 'sharing.changed'
  | 'access.roles_changed'
  | 'access.member_changed'
  | 'change.requested'
  | 'change.decided'
  | 'mcp.changed'
  | 'mcp.unavailable'
  | 'ai.changed'
  | 'ai.error'
  | 'kickoff.requested'
  | 'kickoff.step'
  | 'skills.changed'
  | 'skill.used'
  | 'message.posted'
  | 'message.labeled'
  | 'message.reacted'
  | 'plan.drafted'
  | 'plan.edited'
  | 'plan.approved'
  | 'plan.item_added'
  | 'plan.item_updated'
  | 'coordinator.reply'
  | 'conflict.opened'
  | 'conflict.evidence'
  | 'conflict.vote'
  | 'conflict.closed'
  | 'question.opened'
  | 'question.answered'
  | 'question.defaulted'
  | 'task.started'
  | 'agent.text'
  | 'tool.called'
  | 'tool.result'
  | 'build.result'
  | 'test.result'
  | 'task.escalated'
  | 'task.finished'
  | 'turn.interrupted'
  | 'file.changed'
  | 'file.deleted'
  | 'file.locked'
  | 'file.unlocked'
  | 'checkpoint.created'
  | 'room.rewound'
  | 'log.task_written'
  | 'log.day_written'
  | 'budget.updated'
  | 'room.paused'
  | 'room.resumed'
  | 'export.started'
  | 'export.finished'
  | 'presence.join'
  | 'presence.leave'
  | 'presence.typing'
  | 'presence.tab'
  | 'agent.text.delta';

export interface BaseEvent {
  seq: number;
  room_id: string;
  type: EventType;
  actor_id: string;
  ts: string;
}

export interface MessagePostedEvent extends BaseEvent {
  type: 'message.posted';
  payload: Message;
}

export interface MessageLabeledEvent extends BaseEvent {
  type: 'message.labeled';
  payload: { message_id: string; label: MessageLabel; rationale: string; domain?: string };
}

export interface MessageReactedEvent extends BaseEvent {
  type: 'message.reacted';
  payload: { message_id: string; user_id: string; emoji: string; added: boolean };
}

export interface PlanItemAddedEvent extends BaseEvent {
  type: 'plan.item_added';
  payload: PlanItem;
}

export interface PlanItemUpdatedEvent extends BaseEvent {
  type: 'plan.item_updated';
  payload: { id: string; changes: Partial<PlanItem> };
}

export interface ConflictOpenedEvent extends BaseEvent {
  type: 'conflict.opened';
  payload: Conflict;
}

export interface ConflictVoteEvent extends BaseEvent {
  type: 'conflict.vote';
  payload: { conflict_id: string; user_id: string; option: string; weight: number };
}

export interface ConflictClosedEvent extends BaseEvent {
  type: 'conflict.closed';
  payload: { conflict_id: string; result: string; resolved_by: string };
}

export interface QuestionOpenedEvent extends BaseEvent {
  type: 'question.opened';
  payload: Question;
}

export interface QuestionAnsweredEvent extends BaseEvent {
  type: 'question.answered';
  payload: { question_id: string; answer: string; by_user_id: string };
}

export interface TaskStartedEvent extends BaseEvent {
  type: 'task.started';
  payload: { task_id: string };
}

export interface AgentTextEvent extends BaseEvent {
  type: 'agent.text';
  payload: { task_id: string; text: string };
}

export interface ToolCalledEvent extends BaseEvent {
  type: 'tool.called';
  payload: { tool: string; args: Record<string, unknown> };
}

export interface ToolResultEvent extends BaseEvent {
  type: 'tool.result';
  payload: { tool: string; summary: string; ok: boolean };
}

export interface BuildResultEvent extends BaseEvent {
  type: 'build.result';
  payload: { passed: boolean; duration: number; errors: string[]; sandbox_snapshot_uuid?: string };
}

export interface FileChangedEvent extends BaseEvent {
  type: 'file.changed';
  payload: FileChange;
}

export interface FileDeletedEvent extends BaseEvent {
  type: 'file.deleted';
  payload: { path: string; author_id: string; created_at: string };
}

export interface CheckpointCreatedEvent extends BaseEvent {
  type: 'checkpoint.created';
  payload: Checkpoint;
}

export interface RoomRewoundEvent extends BaseEvent {
  type: 'room.rewound';
  payload: { checkpoint_id: string; seq: number };
}

export interface BudgetUpdatedEvent extends BaseEvent {
  type: 'budget.updated';
  payload: Budget;
}

export interface PresenceJoinEvent extends BaseEvent {
  type: 'presence.join';
  payload: Presence;
}

export interface PresenceLeaveEvent extends BaseEvent {
  type: 'presence.leave';
  payload: { user_id: string };
}

export interface PresenceTypingEvent extends BaseEvent {
  type: 'presence.typing';
  payload: { user_id: string; typing: boolean };
}

export interface PresenceTabEvent extends BaseEvent {
  type: 'presence.tab';
  payload: { user_id: string; tab: Presence['tab'] };
}

export interface AgentTextDeltaEvent extends BaseEvent {
  type: 'agent.text.delta';
  payload: { task_id: string; delta: string };
}

type TypedEventType =
  | 'message.posted'
  | 'message.labeled'
  | 'message.reacted'
  | 'plan.item_added'
  | 'plan.item_updated'
  | 'conflict.opened'
  | 'conflict.vote'
  | 'conflict.closed'
  | 'question.opened'
  | 'question.answered'
  | 'task.started'
  | 'agent.text'
  | 'tool.called'
  | 'tool.result'
  | 'build.result'
  | 'file.changed'
  | 'file.deleted'
  | 'checkpoint.created'
  | 'room.rewound'
  | 'budget.updated'
  | 'presence.join'
  | 'presence.leave'
  | 'presence.typing'
  | 'presence.tab'
  | 'agent.text.delta';

// Events without a dedicated payload type yet
export interface GenericEvent extends BaseEvent {
  type: Exclude<EventType, TypedEventType>;
  payload: any;
}

export interface AccessRolesChangedEvent extends BaseEvent {
  type: 'access.roles_changed';
  payload: { coder_roles: Record<string, string[]> };
}

export interface AccessMemberChangedEvent extends BaseEvent {
  type: 'access.member_changed';
  payload: { user_id: string; coder_role: string | null; folders: string[] };
}

export interface ChangeRequestedEvent extends BaseEvent {
  type: 'change.requested';
  payload: ChangeRequest;
}

export interface ChangeDecidedEvent extends BaseEvent {
  type: 'change.decided';
  payload: { id: string; status: ChangeRequest['status']; decided_by: string };
}

export type AppEvent =
  | GenericEvent
  | AccessRolesChangedEvent
  | AccessMemberChangedEvent
  | ChangeRequestedEvent
  | ChangeDecidedEvent
  | MessagePostedEvent
  | MessageLabeledEvent
  | MessageReactedEvent
  | PlanItemAddedEvent
  | PlanItemUpdatedEvent
  | ConflictOpenedEvent
  | ConflictVoteEvent
  | ConflictClosedEvent
  | QuestionOpenedEvent
  | QuestionAnsweredEvent
  | TaskStartedEvent
  | AgentTextEvent
  | ToolCalledEvent
  | ToolResultEvent
  | BuildResultEvent
  | FileChangedEvent
  | FileDeletedEvent
  | CheckpointCreatedEvent
  | RoomRewoundEvent
  | BudgetUpdatedEvent
  | PresenceJoinEvent
  | PresenceLeaveEvent
  | PresenceTypingEvent
  | PresenceTabEvent
  | AgentTextDeltaEvent;