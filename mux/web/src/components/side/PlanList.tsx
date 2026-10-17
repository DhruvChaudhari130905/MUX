'use client';

import React, { useState } from 'react';
import { Play, Plus, Trash2 } from 'lucide-react';
import type { PlanItem } from '@/types';

interface PlanListProps {
  plan: PlanItem[];
  // Owner and editors can rename, remove and add tasks; only the owner approves the draft
  canEdit: boolean;
  canApprove: boolean;
  onUpdate: (items: PlanItem[]) => void;
  onApprove: () => void;
  // "Run plan": the coder retries the tasks it stopped on
  onRun?: () => Promise<void> | void;
  onKickoff?: () => void;
}

const statusStyles: Record<string, string> = {
  done: 'plan-item done',
  doing: 'plan-item doing',
  todo: 'plan-item',
  draft: 'plan-item',
  skipped_conflict: 'plan-item blocked',
  skipped_question: 'plan-item waiting',
};

// Shown under the title when the item has no notes of its own
const statusLabels: Record<string, string> = {
  doing: 'coder working',
  skipped_conflict: 'skipped · vote open',
  skipped_question: 'skipped · waiting on answer',
};

// Work the coder hasn't started can still change; done and in-progress tasks stay as they are
const isChangeable = (item: PlanItem) => item.status !== 'done' && item.status !== 'doing';

// Each edit sends the whole plan, built from the latest plan the room has seen
export function PlanList({ plan, canEdit, canApprove, onUpdate, onApprove, onRun, onKickoff }: PlanListProps) {
  const [newTitle, setNewTitle] = useState('');
  const [running, setRunning] = useState(false);
  const hasDrafts = plan.some(p => p.status === 'draft');
  const stopped = plan.filter(p => p.status === 'skipped_question').length;
  const done = plan.filter(p => p.status === 'done').length;

  const rename = (id: string, title: string) => {
    const clean = title.trim();
    const item = plan.find(p => p.id === id);
    if (!item || !clean || clean === item.title) return;
    onUpdate(plan.map(p => (p.id === id ? { ...p, title: clean } : p)));
  };

  const remove = (id: string) => onUpdate(plan.filter(p => p.id !== id));

  const add = (e: React.FormEvent) => {
    e.preventDefault();
    const title = newTitle.trim();
    if (!title) return;
    // Ready for the coder straight away, unless a draft is waiting for approval: then it joins the draft
    const item: PlanItem = { id: `u${Date.now().toString(36)}`, title, status: hasDrafts ? 'draft' : 'todo' };
    onUpdate([...plan, item]);
    setNewTitle('');
  };

  return (
    <div className="plan">
      <div className="plan-head">
        <span>Plan</span>
        <span>
          {plan.length === 0 ? 'empty' : hasDrafts ? 'draft' : `${done} of ${plan.length} done`}
          {canEdit && plan.length > 0 ? ' · click a task to edit' : ''}
        </span>
      </div>

      {plan.map(item => {
        const editable = canEdit && isChangeable(item);
        const note = item.notes || statusLabels[item.status];
        return (
          <div key={item.id} className={`${statusStyles[item.status] ?? 'plan-item'} group`} style={{ gridTemplateColumns: '18px 1fr auto' }}>
            <span className="ic" />
            <div className="min-w-0">
              {editable ? (
                <input
                  // Remount when the title changes elsewhere so the field shows the latest text
                  key={item.title}
                  className="t w-full text-ellipsis rounded bg-transparent px-1 -mx-1 outline-none hover:bg-[var(--raised)] focus:bg-[var(--raised)] focus:ring-1 focus:ring-[var(--line)]"
                  defaultValue={item.title}
                  title={item.title}
                  aria-label={`Task: ${item.title}`}
                  maxLength={200}
                  onBlur={e => rename(item.id, e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter') e.currentTarget.blur();
                    if (e.key === 'Escape') {
                      e.currentTarget.value = item.title;
                      e.currentTarget.blur();
                    }
                  }}
                />
              ) : (
                <span className="t">{item.title}</span>
              )}
              {note && (
                <span className={`m ${item.status === 'skipped_conflict' ? 'c' : item.status === 'skipped_question' ? 'a' : item.status === 'todo' && item.notes?.includes('queued') ? 'q' : ''}`}>
                  {note}
                </span>
              )}
            </div>
            {editable ? (
              <button
                className="rounded p-1 text-[var(--faint)] opacity-0 transition-opacity hover:text-[var(--conflict)] focus:opacity-100 group-hover:opacity-100"
                onClick={() => remove(item.id)}
                type="button"
                aria-label={`Remove task: ${item.title}`}
                title="Remove task"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            ) : <span />}
          </div>
        );
      })}

      {plan.length === 0 && (
        <div className="space-y-2">
          <p className="text-[12px] text-[var(--faint)]">Tell the agent what to build in the chat. Each request shows up here as a task.</p>
          {canApprove && onKickoff && (
            <button className="btn w-full" onClick={onKickoff} type="button">Plan it with me</button>
          )}
        </div>
      )}

      {canEdit && (
        <form className="mt-2 flex gap-2" onSubmit={add}>
          <input
            className="min-w-0 flex-1 rounded border border-[var(--line)] bg-[var(--bg)] px-2.5 py-1.5 text-[13px] text-[var(--ink)] outline-none placeholder:text-[var(--faint)] focus:border-[var(--coord)]"
            value={newTitle}
            onChange={e => setNewTitle(e.target.value)}
            placeholder={hasDrafts ? 'Add a task…' : 'Add a task (the coder picks it up next)…'}
            maxLength={200}
            aria-label="New task"
          />
          <button className="btn" type="submit" disabled={!newTitle.trim()} aria-label="Add task">
            <Plus className="h-4 w-4" />
          </button>
        </form>
      )}

      {stopped > 0 && canEdit && onRun && (
        <button
          className="btn mt-2 flex w-full items-center justify-center gap-1.5"
          type="button"
          disabled={running}
          onClick={async () => {
            setRunning(true);
            try {
              await onRun();
            } finally {
              setRunning(false);
            }
          }}
          title="The coder retries every task it stopped on"
        >
          <Play className="h-3.5 w-3.5" />
          {running ? 'Starting…' : `Run plan · retry ${stopped} stopped ${stopped === 1 ? 'task' : 'tasks'}`}
        </button>
      )}

      {hasDrafts && canApprove && (
        <button className="btn primary mt-2 w-full" onClick={onApprove} type="button">
          Approve plan · start building
        </button>
      )}
      {hasDrafts && !canApprove && canEdit && (
        <p className="mt-2 text-[11.5px] text-[var(--faint)]">The owner approves the plan to start building.</p>
      )}
    </div>
  );
}
