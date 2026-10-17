'use client';

import React, { useEffect, useState } from 'react';
import { FolderLock, Plus, Trash2 } from 'lucide-react';
import type { Room } from '@/types';
import { api } from '@/lib/api';
import { normalizeFolder } from '@/lib/fileAccess';

interface AccessSectionProps {
  room: Room;
  onRoom: (room: Room) => void;
}

type Notice = { ok: boolean; text: string } | null;

// "server/, docs/api" -> ["server", "docs/api"]; null when one of them isn't a folder path
function parseFolders(text: string): string[] | null {
  const parts = text.split(',').map(p => p.trim()).filter(Boolean);
  const folders = parts.map(normalizeFolder);
  return folders.every((f): f is string => f !== null) ? folders : null;
}

const joinFolders = (folders: string[] | undefined) => (folders ?? []).join(', ');

// Owner only: coder roles with their folders, and each editor's role and folders of their own. Coders
// change files in their folders directly; anything else goes to the owner or that area's coders for approval.
export function AccessSection({ room, onRoom }: AccessSectionProps) {
  // `saved`: the role's name on the server, so a rename moves its coders instead of dropping their limits
  const [roles, setRoles] = useState<{ saved: string | null; name: string; folders: string }[]>([]);
  const [memberFolders, setMemberFolders] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  // Reset the drafts only when the saved values change, so saving one coder doesn't wipe a role being typed
  const savedRoles = JSON.stringify(room.coder_roles ?? {});
  const savedFolders = JSON.stringify(room.members.map(m => [m.user_id, m.folders ?? []]));
  useEffect(() => {
    const parsed = JSON.parse(savedRoles) as Record<string, string[]>;
    setRoles(Object.entries(parsed).map(([name, folders]) => ({ saved: name, name, folders: joinFolders(folders) })));
  }, [savedRoles]);
  useEffect(() => {
    const parsed = JSON.parse(savedFolders) as [string, string[]][];
    setMemberFolders(Object.fromEntries(parsed.map(([id, folders]) => [id, joinFolders(folders)])));
  }, [savedFolders]);

  const editors = room.members.filter(m => m.permission === 'editor');
  const field = 'bg-[var(--bg)] border border-[var(--line)] rounded px-2 py-1.5 text-sm min-w-0';

  const run = async (action: () => Promise<Room>, ok: string) => {
    setBusy(true);
    setNotice(null);
    try {
      onRoom(await action());
      setNotice({ ok: true, text: ok });
    } catch (error) {
      setNotice({ ok: false, text: error instanceof Error ? error.message : 'Something went wrong' });
    } finally {
      setBusy(false);
    }
  };

  const saveRoles = () => {
    const out: Record<string, string[]> = {};
    const renames: Record<string, string> = {};
    for (const role of roles) {
      const name = role.name.trim();
      const folders = parseFolders(role.folders);
      if (!name) return setNotice({ ok: false, text: 'Every role needs a name.' });
      if (name in out) return setNotice({ ok: false, text: `Two roles are called "${name}".` });
      if (!folders) return setNotice({ ok: false, text: `"${name}" has a folder that isn't a path in the project.` });
      out[name] = folders;
      if (role.saved && role.saved !== name) renames[role.saved] = name;
    }
    const dropped = Object.keys(room.coder_roles ?? {}).filter(n => !roles.some(r => r.saved === n));
    const affected = room.members.filter(m => m.coder_role && dropped.includes(m.coder_role)).map(m => m.user.name);
    if (affected.length && !window.confirm(`${affected.join(', ')} will lose their role and can then change only their own folders. Remove it?`)) return;
    void run(() => api.setCoderRoles(room.id, out, renames), 'Roles saved.');
  };

  const saveMember = (userId: string, coderRole: string | null) => {
    const folders = parseFolders(memberFolders[userId] ?? '');
    if (!folders) return setNotice({ ok: false, text: "That isn't a folder path in the project." });
    void run(() => api.setMemberAccess(room.id, userId, coderRole, folders), 'Access saved.');
  };

  return (
    <div className="space-y-3 mb-4 p-4 bg-[var(--raised)] rounded-lg">
      <div>
        <p className="font-medium flex items-center gap-2"><FolderLock className="w-4 h-4" /> Coder roles &amp; folders</p>
        <p className="text-sm text-[var(--muted)]">
          Coders change files in their folders directly. Changes anywhere else, by them or the agent working for them, wait for
          you or a coder of that area to approve. Editors with no role and no folders can change everything.
        </p>
      </div>

      <div className="space-y-2">
        {roles.map((role, i) => (
          <div key={i} className="flex items-center gap-2">
            <input
              value={role.name}
              onChange={e => setRoles(prev => prev.map((r, j) => (j === i ? { ...r, name: e.target.value } : r)))}
              placeholder="Backend engineer"
              maxLength={40}
              className={`w-36 ${field}`}
              aria-label="Role name"
            />
            <input
              value={role.folders}
              onChange={e => setRoles(prev => prev.map((r, j) => (j === i ? { ...r, folders: e.target.value } : r)))}
              placeholder="server, docs/api"
              className={`flex-1 font-mono ${field}`}
              aria-label={`Folders for ${role.name || 'this role'}`}
            />
            <button
              type="button"
              className="btn p-1"
              onClick={() => setRoles(prev => prev.filter((_, j) => j !== i))}
              disabled={busy}
              aria-label={`Remove role ${role.name}`}
              title="Remove role"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
        <div className="flex gap-2">
          <button type="button" className="btn flex items-center gap-1.5" onClick={() => setRoles(prev => [...prev, { saved: null, name: '', folders: '' }])} disabled={busy}>
            <Plus className="w-3.5 h-3.5" /> Add role
          </button>
          <button type="button" className="btn primary" onClick={saveRoles} disabled={busy}>Save roles</button>
        </div>
      </div>

      {editors.length > 0 && (
        <div className="space-y-2 pt-2 border-t border-[var(--line)]">
          <p className="text-sm text-[var(--muted)]">Each coder</p>
          {editors.map(member => (
            <div key={member.user_id} className="space-y-1">
              <div className="flex items-center gap-2">
                <span className="flex-1 truncate text-sm">{member.user.name}</span>
                <select
                  value={member.coder_role ?? ''}
                  onChange={e => saveMember(member.user_id, e.target.value || null)}
                  disabled={busy}
                  className={field}
                  aria-label={`Role for ${member.user.name}`}
                >
                  <option value="">No role</option>
                  {Object.keys(room.coder_roles ?? {}).map(name => <option key={name} value={name}>{name}</option>)}
                </select>
              </div>
              <div className="flex items-center gap-2">
                <input
                  value={memberFolders[member.user_id] ?? ''}
                  onChange={e => setMemberFolders(prev => ({ ...prev, [member.user_id]: e.target.value }))}
                  placeholder="Extra folders, e.g. infra"
                  className={`flex-1 font-mono ${field}`}
                  aria-label={`Extra folders for ${member.user.name}`}
                />
                <button type="button" className="btn" onClick={() => saveMember(member.user_id, member.coder_role ?? null)} disabled={busy}>
                  Save
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {notice && <p className={`text-sm ${notice.ok ? 'text-[var(--ok)]' : 'text-[var(--conflict)]'}`}>{notice.text}</p>}
    </div>
  );
}
