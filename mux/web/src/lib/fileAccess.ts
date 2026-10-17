// Folder access for coders, the same rule as the server's mux/rooms/file_access.py. Only for hints in the
// UI (lock icons, "needs approval"): the server decides, and holds a change outside your folders for approval.
import type { Room } from '@/types';

export const covers = (folder: string, path: string) => path === folder || path.startsWith(`${folder}/`);

// Folders the person may change directly, or null when they have no limits (owner, or no role and no folders)
export function areaOf(room: Room, userId: string): string[] | null {
  if (userId === room.owner_id) return null;
  const member = room.members.find(m => m.user_id === userId);
  if (!member?.coder_role && !member?.folders?.length) return null;
  return [...(member.coder_role ? room.coder_roles?.[member.coder_role] ?? [] : []), ...(member.folders ?? [])];
}

export function canChange(room: Room, userId: string, path: string): boolean {
  const area = areaOf(room, userId);
  return area === null || area.some(f => covers(f, path));
}

// "web/src" -> "web/src"; "./web/" -> "web"; empty or ".." parts -> null
export function normalizeFolder(folder: string): string | null {
  const f = folder.trim().replace(/^(\.\/)+/, '').replace(/^\/+|\/+$/g, '');
  return f && f.split('/').every(s => s && s !== '.' && s !== '..') ? f : null;
}

// Whether changes at `path` would go for approval (a folder: everything in it would)
export function needsApproval(room: Room, userId: string, path: string, isDirectory = false): boolean {
  const area = areaOf(room, userId);
  if (area === null) return false;
  return isDirectory
    ? !area.some(f => covers(f, path) || f.startsWith(`${path}/`))
    : !area.some(f => covers(f, path));
}
