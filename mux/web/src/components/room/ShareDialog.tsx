'use client';

import React, { useEffect, useState } from 'react';
import { X, Copy, UserPlus, UserCheck, MessageCircle, Trash2, KeyRound, Mail } from 'lucide-react';
import type { Invite, Room } from '@/types';
import { api } from '@/lib/api';
import { useEscapeClose } from '@/lib/useEscapeClose';
import { AccessSection } from './AccessSection';

interface ShareDialogProps {
  isOpen: boolean;
  onClose: () => void;
  room: Room;
  isOwner: boolean;
}

type Role = 'editor' | 'viewer';

// Feedback under a section: green when it worked, red when it didn't
type Notice = { ok: boolean; text: string } | null;

function NoticeLine({ notice }: { notice: Notice }) {
  if (!notice) return null;
  return <p className={`text-sm ${notice.ok ? 'text-[var(--ok)]' : 'text-[var(--conflict)]'}`}>{notice.text}</p>;
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button className="btn" onClick={copy} type="button" aria-label={label} title={label}>
      {copied ? <UserCheck className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
    </button>
  );
}

export function ShareDialog({ isOpen, onClose, room: initialRoom, isOwner }: ShareDialogProps) {
  // Each save returns the updated room; keep it so reopening shows the current settings
  const [room, setRoom] = useState(initialRoom);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<Role>('editor');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [linkNotice, setLinkNotice] = useState<Notice>(null);
  const [inviteNotice, setInviteNotice] = useState<Notice>(null);
  const [passwordNotice, setPasswordNotice] = useState<Notice>(null);

  useEffect(() => setRoom(initialRoom), [initialRoom]);

  useEffect(() => {
    if (!isOpen || !isOwner) return;
    setLinkNotice(null);
    setInviteNotice(null);
    setPasswordNotice(null);
    api.listInvites(room.id).then(setInvites).catch(error => console.error('Failed to load invites:', error));
  }, [isOpen, isOwner, room.id]);

  useEscapeClose(isOpen, onClose);

  if (!isOpen) return null;

  const shareUrl = `${window.location.origin}/room/${room.id}`;
  const linkPermission: Role = room.link_permission === 'viewer' ? 'viewer' : 'editor';
  const whatsappText = `Join my MUX room "${room.title}": ${shareUrl}\nRoom ID: ${room.id}`;

  const run = async (action: () => Promise<void>, onError: (message: string) => void) => {
    setBusy(true);
    try {
      await action();
    } catch (error) {
      onError(error instanceof Error ? error.message : 'Something went wrong');
    } finally {
      setBusy(false);
    }
  };

  const saveSharing = (link_access: Room['link_access'], link_permission: Role) =>
    run(async () => {
      setRoom(await api.updateSharing(room.id, { link_access, link_permission }));
      setLinkNotice({ ok: true, text: 'Saved' });
    }, text => setLinkNotice({ ok: false, text }));

  const sendInvite = (e: React.FormEvent) => {
    e.preventDefault();
    const email = inviteEmail.trim();
    if (!email) return;
    run(async () => {
      const result = await api.createInvite(room.id, email, inviteRole);
      setInvites(prev => [...prev.filter(i => i.email !== result.email), { email: result.email, role: result.role }]);
      setInviteEmail('');
      setInviteNotice(result.email_sent
        ? { ok: true, text: `Invite emailed to ${result.email}.` }
        : { ok: false, text: `Invite saved, but the email wasn't sent: ${result.email_error ?? 'unknown error'}. Copy the link and send it yourself.` });
    }, text => setInviteNotice({ ok: false, text }));
  };

  const removeInvite = (email: string) =>
    run(async () => {
      await api.revokeInvite(room.id, email);
      setInvites(prev => prev.filter(i => i.email !== email));
    }, text => setInviteNotice({ ok: false, text }));

  const savePassword = (value: string | null) =>
    run(async () => {
      setRoom(await api.setRoomPassword(room.id, value));
      setPassword('');
      setPasswordNotice({ ok: true, text: value ? 'Password saved. Share it with the Room ID.' : 'Password removed.' });
    }, text => setPasswordNotice({ ok: false, text }));

  const section = 'space-y-3 mb-4 p-4 bg-[var(--raised)] rounded-lg';
  const field = 'bg-[var(--bg)] border border-[var(--line)] rounded px-3 py-2 text-sm';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center glass-overlay p-4" onClick={onClose}>
      <div
        className="glass-modal rounded-2xl p-6 w-full max-w-md max-h-[90vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="share-title"
      >
        <div className="flex items-center justify-between mb-5">
          <h2 id="share-title" className="text-lg font-semibold">Share &ldquo;{room.title}&rdquo;</h2>
          <button className="btn p-2" onClick={onClose} type="button" aria-label="Close">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Link and room id: anyone in the room can pass these on */}
        <div className={section}>
          <p className="font-medium">Send the link</p>
          <div className="flex items-center gap-2">
            <input type="text" value={shareUrl} readOnly className={`flex-1 min-w-0 font-mono ${field}`} aria-label="Room link" />
            <CopyButton value={shareUrl} label="Copy link" />
            <a
              className="btn"
              href={`https://wa.me/?text=${encodeURIComponent(whatsappText)}`}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Share on WhatsApp"
              title="Share on WhatsApp"
            >
              <MessageCircle className="w-4 h-4" />
            </a>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm text-[var(--muted)]">Room ID</span>
            <code className="flex-1 min-w-0 truncate font-mono text-sm">{room.id}</code>
            <CopyButton value={room.id} label="Copy room ID" />
          </div>
          {!isOwner && (
            <p className="text-sm text-[var(--muted)]">Only the owner can change who can join.</p>
          )}
        </div>

        {isOwner && (
          <>
            {/* Link access */}
            <div className={section}>
              <div>
                <p className="font-medium">Who can join with the link</p>
                <p className="text-sm text-[var(--muted)]">Invited people and anyone with the password can always join.</p>
              </div>
              {(['restricted', 'anyone'] as const).map(access => (
                <label key={access} className="flex items-center gap-3 p-3 bg-[var(--bg)] rounded-lg border border-[var(--line)] cursor-pointer">
                  <input
                    type="radio"
                    name="linkAccess"
                    checked={room.link_access === access}
                    onChange={() => saveSharing(access, linkPermission)}
                    disabled={busy}
                    className="accent-[var(--coord)]"
                  />
                  <div>
                    <p className="font-medium">{access === 'restricted' ? 'Only invited people' : 'Anyone with the link'}</p>
                    <p className="text-sm text-[var(--muted)]">
                      {access === 'restricted' ? 'The link alone doesn’t let anyone in' : 'Anyone signed in who opens it joins'}
                    </p>
                  </div>
                </label>
              ))}
              {room.link_access === 'anyone' && (
                <label className="flex items-center justify-between gap-3 text-sm">
                  <span>People who join with the link can</span>
                  <select
                    value={linkPermission}
                    onChange={e => saveSharing('anyone', e.target.value as Role)}
                    disabled={busy}
                    className={field}
                  >
                    <option value="editor">Steer and edit</option>
                    <option value="viewer">Only watch</option>
                  </select>
                </label>
              )}
              <NoticeLine notice={linkNotice} />
            </div>

            {/* Email invites */}
            <form onSubmit={sendInvite} className={section}>
              <p className="font-medium flex items-center gap-2"><Mail className="w-4 h-4" /> Invite by email</p>
              <div className="flex gap-2">
                <input
                  type="email"
                  value={inviteEmail}
                  onChange={e => setInviteEmail(e.target.value)}
                  placeholder="teammate@example.com"
                  className={`flex-1 min-w-0 ${field}`}
                  aria-label="Email to invite"
                />
                <select value={inviteRole} onChange={e => setInviteRole(e.target.value as Role)} className={field} aria-label="Role">
                  <option value="editor">Editor</option>
                  <option value="viewer">Viewer</option>
                </select>
                <button type="submit" className="btn primary" disabled={busy || !inviteEmail.trim()} aria-label="Send invite" title="Send invite">
                  <UserPlus className="w-4 h-4" />
                </button>
              </div>
              <NoticeLine notice={inviteNotice} />
              {invites.length > 0 && (
                <div className="space-y-1 pt-2 border-t border-[var(--line)]">
                  <p className="text-sm text-[var(--muted)]">Waiting to join</p>
                  {invites.map(invite => (
                    <div key={invite.email} className="flex items-center justify-between gap-2 text-sm">
                      <span className="truncate">{invite.email}</span>
                      <span className="flex items-center gap-2">
                        <span className="text-[var(--muted)] capitalize">{invite.role}</span>
                        <button
                          type="button"
                          className="btn p-1"
                          onClick={() => removeInvite(invite.email)}
                          disabled={busy}
                          aria-label={`Remove invite for ${invite.email}`}
                          title="Remove invite"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </form>

            {/* Room password */}
            <form
              onSubmit={e => { e.preventDefault(); if (password) savePassword(password); }}
              className={section}
            >
              <div>
                <p className="font-medium flex items-center gap-2"><KeyRound className="w-4 h-4" /> Room password</p>
                <p className="text-sm text-[var(--muted)]">
                  {room.has_password
                    ? 'On: anyone with the Room ID and password can join as an editor.'
                    : 'Off. Set one so people can join from the dashboard with the Room ID and password.'}
                </p>
              </div>
              <div className="flex gap-2">
                <input
                  type="password"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder={room.has_password ? 'New password' : 'At least 4 characters'}
                  minLength={4}
                  maxLength={128}
                  autoComplete="new-password"
                  className={`flex-1 min-w-0 ${field}`}
                  aria-label="Room password"
                />
                <button type="submit" className="btn primary" disabled={busy || password.length < 4}>
                  {room.has_password ? 'Change' : 'Set'}
                </button>
                {room.has_password && (
                  <button type="button" className="btn" onClick={() => savePassword(null)} disabled={busy}>
                    Remove
                  </button>
                )}
              </div>
              <NoticeLine notice={passwordNotice} />
            </form>

            <AccessSection room={room} onRoom={setRoom} />
          </>
        )}

        {/* Current members */}
        <div className="pt-2">
          <h3 className="font-medium mb-3">In this room</h3>
          <div className="space-y-2 max-h-40 overflow-y-auto">
            {room.members.map(member => (
              <div key={member.user_id} className="flex items-center justify-between p-2 bg-[var(--raised)] rounded">
                <div className="flex items-center gap-3">
                  <span className="av" style={{ background: member.user.color }}>
                    {member.user.initials}
                  </span>
                  <div>
                    <p className="font-medium">{member.user.name}</p>
                    <p className="text-sm text-[var(--muted)]">
                      <span className="capitalize">{member.permission}</span>
                      {member.coder_role && <> · {member.coder_role}</>}
                    </p>
                  </div>
                </div>
                <span className="text-xs text-[var(--muted)] uppercase">{member.domain_role}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="flex justify-end mt-5">
          <button className="btn" onClick={onClose} type="button">Done</button>
        </div>
      </div>
    </div>
  );
}
