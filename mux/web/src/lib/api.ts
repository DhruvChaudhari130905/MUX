// API client for REST commands
import type { ChangeRequest, PlanName, UserPlan, AiRole, Invite, InviteResult, McpToolSetting, RoomAi, RoomMcp, RoomSkill, Room, Message, MessageTo, PlanItem, Conflict, Question, Budget, Checkpoint, User, Membership } from '@/types';

import { createDemoRoom, deleteDemoRoom, demoMessageEvent, demoPlan, demoPlanApprovedEvent, demoPlanEditedEvent, demoPlanItemAddedEvent, demoPlanItemRemovedEvent, demoPlanItemUpdatedEvent, getDemoRoom, isDemoMode, listDemoRooms, nextDemoSeq, rememberDemoEvent, setDemoPlan } from './demo';
import { getSocket } from './socket';
import { getAccessToken } from './supabase';

export type FileSaveResult = { accepted: true; seq: number; version: number | null; held?: boolean; request_id?: string | null };

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// Answers API calls locally in demo mode; room events are pushed through the room socket
async function demoFetch<T>(path: string, options: RequestInit): Promise<T> {
  const method = options.method || 'GET';
  const body = options.body ? JSON.parse(options.body as string) : {};
  // Query strings (`/close?reason=…`) don't change which call this is
  const roomMatch = path.split('?')[0].match(/^\/rooms\/([^/]+)(\/.*)?$/);

  if (path === '/me/plan') return (method === 'PUT' ? setDemoPlan(body.plan) : demoPlan()) as T;
  if (path === '/rooms' && method === 'GET') return listDemoRooms() as T;
  if (path === '/rooms' && method === 'POST') return createDemoRoom(body.description) as T;
  if (roomMatch && !roomMatch[2] && method === 'GET') return getDemoRoom(roomMatch[1]) as T;
  if (roomMatch && roomMatch[2] === '/close') {
    deleteDemoRoom(roomMatch[1]);
    return { room_id: roomMatch[1], closed: true } as T;
  }
  if (roomMatch && roomMatch[2] === '/sharing') return Object.assign(getDemoRoom(roomMatch[1]), body) as T;
  if (roomMatch && roomMatch[2] === '/invites' && method === 'GET') return [] as T;
  if (roomMatch && roomMatch[2] === '/invites') {
    return { email: body.email, role: body.role, email_sent: false, email_error: 'Demo mode sends no email', link: `${window.location.origin}/room/${roomMatch[1]}` } as T;
  }
  if (roomMatch && roomMatch[2] === '/password') return Object.assign(getDemoRoom(roomMatch[1]), { has_password: Boolean(body.password) }) as T;
  if (roomMatch && roomMatch[2] === '/messages') {
    getSocket(roomMatch[1]).injectEvent(demoMessageEvent(roomMatch[1], body.text, body.to));
  }
  if (roomMatch && roomMatch[2] === '/plan/reorder') {
    const socket = getSocket(roomMatch[1]);
    const current = socket.getState()?.plan ?? [];
    const movable = current.filter(item => item.status === 'draft' || item.status === 'todo');
    const ids = Array.isArray(body.item_ids) ? body.item_ids as string[] : [];
    if (ids.length !== movable.length || new Set(ids).size !== ids.length || ids.some(id => !movable.some(item => item.id === id))) {
      throw new ApiError('The task list changed. Refresh the plan and try again', 409);
    }
    const byId = new Map<string, typeof movable[number]>(movable.map(item => [item.id, item]));
    let index = 0;
    const items = current.map(item => item.status === 'draft' || item.status === 'todo' ? byId.get(ids[index++])! : item);
    const event = {
      seq: nextDemoSeq(), room_id: roomMatch[1], type: 'plan.edited', actor_id: 'demo-you', ts: new Date().toISOString(),
      payload: { items },
    } as import('@/types').AppEvent;
    rememberDemoEvent(event);
    socket.injectEvent(event);
  }
  if (roomMatch && roomMatch[2]?.startsWith('/skills')) return [] as T;
  if (roomMatch && roomMatch[2] === '/ai/models') return { models: [] } as T;
  if (roomMatch && roomMatch[2] === '/ai') return { source: 'server', provider: null, base_url: null, models: null, has_key: false } as T;
  if (roomMatch && roomMatch[2]?.startsWith('/mcp')) return { admin: [], servers: [] } as T;
  if (roomMatch && roomMatch[2] === '/kickoff') return { accepted: true } as T;
  if (roomMatch && roomMatch[2] === '/plan/run') return { accepted: true, retried: 0 } as T;
  if (roomMatch && roomMatch[2] === '/errors') return { accepted: true, task_id: null } as T;
  if (path === '/github/connect' || path.endsWith('/export')) return { url: 'https://github.com' } as T;
  return { accepted: true, seq: nextDemoSeq(), version: (body.base_version ?? 0) + 1 } as T;
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function fetchWithAuth<T>(path: string, options: RequestInit = {}): Promise<T> {
  if (isDemoMode()) return demoFetch<T>(path, options);
  const token = await getAccessToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ message: 'Request failed' }));
    // FastAPI puts the reason in `detail` (a list of field errors for a 422)
    const detail = typeof error.detail === 'string' ? error.detail : error.detail && JSON.stringify(error.detail);
    throw new ApiError(error.message || detail || `HTTP ${res.status}`, res.status);
  }

  if (res.status === 204) return {} as T;
  return res.json();
}

export const api = {
  // Rooms
  listRooms: () => fetchWithAuth<Room[]>('/rooms'),

  // Plan: no payment step yet, switching applies straight away
  getPlan: () => fetchWithAuth<UserPlan>('/me/plan'),
  setPlan: (plan: PlanName) => fetchWithAuth<UserPlan>('/me/plan', { method: 'PUT', body: JSON.stringify({ plan }) }),
  getRoom: (id: string) => fetchWithAuth<Room>(`/rooms/${id}`),
  createRoom: (description: string, domain_role: 'pm' | 'design' | 'eng', password?: string) =>
    fetchWithAuth<Room>('/rooms', {
      method: 'POST',
      body: JSON.stringify({ description, domain_role, ...(password ? { password } : {}) }),
    }),
  // Members, invited emails, a password or a public link let you in (403 otherwise; 429 after too many wrong passwords)
  joinRoom: (id: string, data: { password?: string; user_name?: string } = {}) =>
    fetchWithAuth<{ user_id: string }>(`/rooms/${id}/join`, { method: 'POST', body: JSON.stringify(data) }),
  // Owner only. The room stops and disappears for everyone; the server keeps its history but never reopens it
  closeRoom: (id: string, reason?: string) =>
    fetchWithAuth<{ room_id: string; closed: boolean }>(
      `/rooms/${id}/close${reason ? `?reason=${encodeURIComponent(reason)}` : ''}`,
      { method: 'POST' },
    ),
  updateSharing: (id: string, data: { link_access: 'restricted' | 'anyone'; link_permission: 'editor' | 'viewer' }) =>
    fetchWithAuth<Room>(`/rooms/${id}/sharing`, { method: 'PATCH', body: JSON.stringify(data) }),
  // Owner only. The invite is saved even when the email can't be sent (email_sent false, email_error says why)
  listInvites: (id: string) => fetchWithAuth<Invite[]>(`/rooms/${id}/invites`),
  createInvite: (id: string, email: string, role: 'editor' | 'viewer') =>
    fetchWithAuth<InviteResult>(`/rooms/${id}/invites`, { method: 'POST', body: JSON.stringify({ email, role }) }),
  revokeInvite: (id: string, email: string) =>
    fetchWithAuth<void>(`/rooms/${id}/invites/${encodeURIComponent(email)}`, { method: 'DELETE' }),
  // Owner only; null removes the password
  setRoomPassword: (id: string, password: string | null) =>
    fetchWithAuth<Room>(`/rooms/${id}/password`, { method: 'PUT', body: JSON.stringify({ password }) }),

  // MCP servers for the coder: anyone in the room reads them, only the owner changes them
  getMcp: (id: string) => fetchWithAuth<RoomMcp>(`/rooms/${id}/mcp`),
  addMcpServer: (id: string, data: { name: string; url: string; headers: Record<string, string> }) =>
    fetchWithAuth<RoomMcp>(`/rooms/${id}/mcp/servers`, { method: 'POST', body: JSON.stringify(data) }),
  refreshMcpServer: (id: string, name: string) =>
    fetchWithAuth<RoomMcp>(`/rooms/${id}/mcp/servers/${name}/refresh`, { method: 'POST' }),
  updateMcpServer: (id: string, name: string, data: { headers?: Record<string, string>; settings?: Record<string, McpToolSetting> }) =>
    fetchWithAuth<RoomMcp>(`/rooms/${id}/mcp/servers/${name}`, { method: 'PATCH', body: JSON.stringify(data) }),
  removeMcpServer: (id: string, name: string) =>
    fetchWithAuth<RoomMcp>(`/rooms/${id}/mcp/servers/${name}`, { method: 'DELETE' }),
  updateMcpAdmin: (id: string, name: string, data: { enabled: boolean; settings?: Record<string, McpToolSetting> }) =>
    fetchWithAuth<RoomMcp>(`/rooms/${id}/mcp/admin/${name}`, { method: 'PATCH', body: JSON.stringify(data) }),
  refreshMcpAdmin: (id: string, name: string) =>
    fetchWithAuth<RoomMcp>(`/rooms/${id}/mcp/admin/${name}/refresh`, { method: 'POST' }),

  // The room's AI provider: anyone in the room reads it, only the owner sets it (the key is never returned)
  getAi: (id: string) => fetchWithAuth<RoomAi>(`/rooms/${id}/ai`),
  setAi: (id: string, data: { provider: string; base_url: string; api_key: string; models: Record<AiRole, string> }) =>
    fetchWithAuth<RoomAi>(`/rooms/${id}/ai`, { method: 'PUT', body: JSON.stringify(data) }),
  // The model ids a key can use at a provider (empty key: the room's saved one)
  listAiModels: (id: string, data: { base_url: string; api_key: string }) =>
    fetchWithAuth<{ models: string[] }>(`/rooms/${id}/ai/models`, { method: 'POST', body: JSON.stringify(data) }),
  clearAi: (id: string) => fetchWithAuth<RoomAi>(`/rooms/${id}/ai`, { method: 'DELETE' }),

  // Skills the room's coder may use: anyone reads, the owner switches them on
  getSkills: (id: string) => fetchWithAuth<RoomSkill[]>(`/rooms/${id}/skills`),
  setSkills: (id: string, enabled: string[]) =>
    fetchWithAuth<RoomSkill[]>(`/rooms/${id}/skills`, { method: 'PUT', body: JSON.stringify({ enabled }) }),
  reloadSkills: (id: string) => fetchWithAuth<RoomSkill[]>(`/rooms/${id}/skills/reload`, { method: 'POST' }),

  // Messages
  sendMessage: (roomId: string, text: string, to: MessageTo = 'agent', attachments: string[] = []) =>
    fetchWithAuth<{ accepted: true; seq: number }>(`/rooms/${roomId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ text, to, ...(attachments.length ? { attachments } : {}) }),
    }),
  // Toggles the caller's reaction (editors only; only people's messages)
  toggleReaction: (roomId: string, messageId: string, emoji: string) =>
    fetchWithAuth<{ accepted: true; seq: number }>(`/rooms/${roomId}/messages/${messageId}/reactions`, {
      method: 'POST',
      body: JSON.stringify({ emoji }),
    }),
  // PNG, JPEG, GIF or WebP up to 5 MB; send the id with a Team message
  uploadImage: async (roomId: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    const token = await getAccessToken();
    const res = await fetch(`${API_BASE}/rooms/${roomId}/uploads`, {
      method: 'POST',
      body: form, // the browser sets the multipart Content-Type with its boundary
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) {
      const error = await res.json().catch(() => ({}));
      throw new ApiError(typeof error.detail === 'string' ? error.detail : `HTTP ${res.status}`, res.status);
    }
    return res.json() as Promise<{ id: string; content_type: string }>;
  },
  // <img> can't send the token, so images are fetched here and shown from an object URL
  fetchUpload: async (roomId: string, uploadId: string) => {
    const token = await getAccessToken();
    const res = await fetch(`${API_BASE}/rooms/${roomId}/uploads/${uploadId}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) throw new ApiError(`HTTP ${res.status}`, res.status);
    return res.blob();
  },

  // Plan (targeted ops: each call touches one thing through the server's own plan, so
  // concurrent editors adding / renaming / removing tasks can't overwrite each other)
  addPlanItem: (roomId: string, title: string) =>
    fetchWithAuth<{ accepted: true; seq: number }>(`/rooms/${roomId}/plan/items`, {
      method: 'POST',
      body: JSON.stringify({ title }),
    }),
  updatePlanItem: (roomId: string, itemId: string, title: string) =>
    fetchWithAuth<{ accepted: true; seq: number }>(`/rooms/${roomId}/plan/items/${itemId}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    }),
  removePlanItem: (roomId: string, itemId: string) =>
    fetchWithAuth<{ accepted: true; seq: number }>(`/rooms/${roomId}/plan/items/${itemId}`, {
      method: 'DELETE',
    }),
  reorderPlan: (roomId: string, itemIds: string[]) =>
    fetchWithAuth<{ accepted: true; seq: number }>(`/rooms/${roomId}/plan/reorder`, {
      method: 'POST',
      body: JSON.stringify({ item_ids: itemIds }),
    }),
  approvePlan: (roomId: string) =>
    fetchWithAuth<{ accepted: true; seq: number }>(`/rooms/${roomId}/plan/approve`, { method: 'POST' }),
  // "Run plan": the coder retries every task it stopped on. 409 without an AI model
  runPlan: (roomId: string) =>
    fetchWithAuth<{ accepted: true; retried: number }>(`/rooms/${roomId}/plan/run`, { method: 'POST' }),
  // Stop: the coder's task is interrupted and goes back in the plan queued. 409 without an AI model
  stopCoder: (roomId: string) =>
    fetchWithAuth<{ accepted: true }>(`/rooms/${roomId}/coder/stop`, { method: 'POST' }),
  // Start / Resume: the coder runs the plan's queued tasks again. 409 without an AI model
  startCoder: (roomId: string) =>
    fetchWithAuth<{ accepted: true }>(`/rooms/${roomId}/coder/start`, { method: 'POST' }),
  // Errors the running app threw: the coder gets a "Fix errors" task (task_id null when one is already open)
  reportErrors: (roomId: string, errors: string[]) =>
    fetchWithAuth<{ accepted: true; task_id: string | null }>(`/rooms/${roomId}/errors`, {
      method: 'POST',
      body: JSON.stringify({ errors, source: 'preview' }),
    }),
  // Owner only: "Plan it with me" (read the project, ask the team, draft a plan). 409 without an AI model
  kickoff: (roomId: string, brief: string) =>
    fetchWithAuth<{ accepted: true }>(`/rooms/${roomId}/kickoff`, {
      method: 'POST',
      body: JSON.stringify({ brief }),
    }),

  // Conflicts
  voteConflict: (roomId: string, conflictId: string, option: string) =>
    fetchWithAuth<{ accepted: true; seq: number }>(`/rooms/${roomId}/conflicts/${conflictId}/vote`, {
      method: 'POST',
      body: JSON.stringify({ option }),
    }),
  overrideConflict: (roomId: string, conflictId: string, option: string) =>
    fetchWithAuth<{ accepted: true; seq: number }>(`/rooms/${roomId}/conflicts/${conflictId}/override`, {
      method: 'POST',
      body: JSON.stringify({ option }),
    }),

  // Questions
  answerQuestion: (roomId: string, questionId: string, answer: string) =>
    fetchWithAuth<{ accepted: true; seq: number }>(`/rooms/${roomId}/questions/${questionId}/answer`, {
      method: 'POST',
      body: JSON.stringify({ answer }),
    }),

  // Coder roles and folder access (owner only), and change requests
  // renames: old role name -> new one, so its coders keep the role
  setCoderRoles: (roomId: string, roles: Record<string, string[]>, renames: Record<string, string> = {}) =>
    fetchWithAuth<Room>(`/rooms/${roomId}/access/roles`, { method: 'PUT', body: JSON.stringify({ roles, renames }) }),
  setMemberAccess: (roomId: string, userId: string, coderRole: string | null, folders: string[]) =>
    fetchWithAuth<Room>(`/rooms/${roomId}/members/${encodeURIComponent(userId)}/access`, {
      method: 'PUT',
      body: JSON.stringify({ coder_role: coderRole, folders }),
    }),
  decideChange: (roomId: string, requestId: string, action: 'approve' | 'reject' | 'withdraw') =>
    fetchWithAuth<ChangeRequest>(`/rooms/${roomId}/change-requests/${requestId}/${action}`, { method: 'POST' }),

  // Files
  lockFile: (roomId: string, path: string) =>
    fetchWithAuth<{ accepted: true; seq: number }>(`/rooms/${roomId}/files/lock`, {
      method: 'POST',
      body: JSON.stringify({ path }),
    }),
  unlockFile: (roomId: string, path: string) =>
    fetchWithAuth<{ accepted: true; seq: number }>(`/rooms/${roomId}/files/unlock`, {
      method: 'POST',
      body: JSON.stringify({ path }),
    }),
  readFile: (roomId: string, path: string) =>
    fetchWithAuth<{ path: string; content: string }>(
      `/api/files/${roomId}/files/${path.split('/').map(encodeURIComponent).join('/')}`,
    ),
  // baseVersion: the version the edit started from (0 for a new file); null overwrites whatever is there.
  // held: outside your folders, so it went for approval as change request request_id (version is then null)
  saveFile: (roomId: string, path: string, content: string, baseVersion: number | null) =>
    fetchWithAuth<FileSaveResult>(`/rooms/${roomId}/files`, {
      method: 'PUT',
      body: JSON.stringify({ path, content, base_version: baseVersion }),
    }),

  // A file that only exists in this browser (never saved to the room) is already gone on the server
  deleteFile: (roomId: string, path: string) =>
    fetchWithAuth<{ accepted: true; seq: number; held?: boolean; request_id?: string | null }>(`/rooms/${roomId}/files?path=${encodeURIComponent(path)}`, { method: 'DELETE' })
      .catch(error => {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }),

  // Rewind
  rewind: (roomId: string, checkpointId: string) =>
    fetchWithAuth<{ accepted: true; seq: number; present_checkpoint_id: string }>(`/rooms/${roomId}/rewind`, {
      method: 'POST',
      body: JSON.stringify({ checkpoint_id: checkpointId }),
    }),
  restoreCheckpoint: (roomId: string, checkpointId: string) =>
    fetchWithAuth<{ accepted: true; seq: number }>(`/rooms/${roomId}/restore-checkpoint`, {
      method: 'POST',
      body: JSON.stringify({ checkpoint_id: checkpointId }),
    }),

  // Budget
  updateBudget: (roomId: string, tokensCap?: number, runsCap?: number) =>
    fetchWithAuth<Budget>(`/rooms/${roomId}/budget`, {
      method: 'PATCH',
      body: JSON.stringify({ tokens_cap: tokensCap, runs_cap: runsCap }),
    }),

  // GitHub
  // `next`: the app page GitHub returns to after connecting (the profile page by default)
  connectGitHub: (next?: string) =>
    fetchWithAuth<{ url: string }>(`/github/connect${next ? `?next=${encodeURIComponent(next)}` : ''}`),
  githubStatus: () => fetchWithAuth<{ connected: boolean; username: string | null }>('/api/export/github/status'),
  exportToGitHub: (roomId: string, repoName: string, isPrivate: boolean) =>
    fetchWithAuth<{ url: string }>(`/rooms/${roomId}/export`, {
      method: 'POST',
      body: JSON.stringify({ repo_name: repoName, private: isPrivate }),
    }),

  // Session
  endSession: (roomId: string) =>
    fetchWithAuth<{ accepted: true; seq: number }>(`/rooms/${roomId}/end-session`, { method: 'POST' }),
};
