'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { formatDistanceToNow } from 'date-fns';
import type { ChangeRequest } from '@/types';
import { api, ApiError } from '@/lib/api';
import { hunks, lineDiff } from '@/lib/lineDiff';

interface ChangeRequestCardProps {
  roomId: string;
  request: ChangeRequest;
  currentUserId: string;
  nameOf: (userId: string) => string;
  onDismiss: (requestId: string) => void;
}

// A change held because it was outside the requester's folders: approvers see the diff and decide, the
// requester can take it back. The first approval applies it (the server marks it stale if the file moved on).
export function ChangeRequestCard({ roomId, request, currentUserId, nameOf, onDismiss }: ChangeRequestCardProps) {
  const [current, setCurrent] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const open = request.status === 'pending';
  const canDecide = open && request.approvers.includes(currentUserId) && request.requested_by !== currentUserId;
  const mine = request.requested_by === currentUserId;
  const [copied, setCopied] = useState(false);
  const copyMine = async () => {
    await navigator.clipboard.writeText(request.content ?? '');
    setCopied(true);
  };

  useEffect(() => {
    let live = true;
    api.readFile(roomId, request.path)
      .then(f => { if (live) setCurrent(f.content); })
      .catch(error => { if (live) setCurrent(error instanceof ApiError && error.status === 404 ? '' : null); });
    return () => { live = false; };
  }, [roomId, request.path, request.base_version]);

  const diff = useMemo(
    () => (current === null ? null : hunks(lineDiff(current, request.content ?? ''))),
    [current, request.content],
  );

  const decide = async (action: 'approve' | 'reject' | 'withdraw') => {
    setBusy(true);
    setNotice(null);
    try {
      const result = await api.decideChange(roomId, request.id, action);
      if (result.status === 'stale') setNotice('The file changed after this was asked for, so nothing was applied.');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Something went wrong');
    } finally {
      setBusy(false);
    }
  };

  const who = nameOf(request.requested_by);
  const what = request.content === null ? 'delete' : request.base_version === null ? 'create' : 'change';

  return (
    <div className="card ask">
      <div className="card-head">
        <span className="card-kind">
          {request.via === 'agent' ? `Agent, for ${who}` : who} ·{' '}
          {open ? 'needs approval' : request.status === 'stale' ? 'not applied' : 'rejected'}
        </span>
        {request.created_at && (
          <span className="text-[11px] text-[var(--faint)]">{formatDistanceToNow(new Date(request.created_at), { addSuffix: true })}</span>
        )}
      </div>
      <h4 className="break-all">
        {what === 'delete' ? 'Delete' : what === 'create' ? 'Create' : 'Change'} <code className="font-mono text-[12.5px]">{request.path}</code>
      </h4>
      <p className="why">
        {open ? (
          <>Outside {mine ? 'your' : `${who}'s`} folders{request.task_id ? ` · task ${request.task_id}` : ''}. Waiting on{' '}
            {request.approvers.map(nameOf).join(' or ')}.</>
        ) : request.status === 'stale' ? (
          'The file changed before this was approved, so nothing was applied. Copy your version and redo the change.'
        ) : (
          `${request.decided_by ? nameOf(request.decided_by) : 'Someone'} rejected it. Your version is below if you need it.`
        )}
      </p>

      {diff && diff.length > 0 && (
        <pre className="max-h-56 overflow-auto rounded border border-[var(--line)] bg-[var(--bg)] p-2 font-mono text-[11.5px] leading-snug">
          {diff.map((line, i) =>
            line === null ? (
              <div key={i} className="text-[var(--faint)]">…</div>
            ) : (
              <div
                key={i}
                className={line.kind === '+' ? 'bg-[#3fb950]/15 text-[#56d364]' : line.kind === '-' ? 'bg-[var(--conflict)]/15 text-[var(--conflict)]' : 'text-[var(--muted)]'}
              >
                {line.kind} {line.text}
              </div>
            ),
          )}
        </pre>
      )}

      {canDecide && (
        <div className="flex gap-2">
          <button type="button" className="btn primary" disabled={busy} onClick={() => void decide('approve')}>Approve</button>
          <button type="button" className="btn" disabled={busy} onClick={() => void decide('reject')}>Reject</button>
        </div>
      )}
      {mine && open && (
        <button type="button" className="btn self-start" disabled={busy} onClick={() => void decide('withdraw')}>Withdraw</button>
      )}
      {mine && !open && (
        <div className="flex gap-2">
          {request.content !== null && (
            <button type="button" className="btn" onClick={() => void copyMine()}>{copied ? 'Copied' : 'Copy my version'}</button>
          )}
          <button type="button" className="btn" onClick={() => onDismiss(request.id)}>Dismiss</button>
        </div>
      )}
      {notice && <p className="text-sm text-[var(--conflict)]">{notice}</p>}
    </div>
  );
}
