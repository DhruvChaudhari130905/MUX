'use client';

import React, { useState } from 'react';
import { ChevronDown, ChevronUp, GripVertical, Play, Plus, Trash2, X } from 'lucide-react';
import type { PlanItem } from '@/types';

interface PlanListProps {
  plan: PlanItem[];
  // Owner and editors can rename, remove and add tasks; only the owner approves the draft
  canEdit: boolean;
  canApprove: boolean;
  onUpdate: (items: PlanItem[]) => void;
  onReorder: (itemIds: string[]) => Promise<void> | void;
  onApprove: () => void;
  // "Run plan": the coder retries the tasks it stopped on
  onRun?: () => Promise<void> | void;
  initialBrief: string;
  onKickoff?: (brief: string) => Promise<void> | void;
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
const isReorderable = (item: PlanItem) => item.status === 'draft' || item.status === 'todo';

// Each edit sends the whole plan, built from the latest plan the room has seen
export function PlanList({ plan, canEdit, canApprove, onUpdate, onReorder, onApprove, onRun, initialBrief, onKickoff }: PlanListProps) {
  const [newTitle, setNewTitle] = useState('');
  const [running, setRunning] = useState(false);
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [savingOrder, setSavingOrder] = useState(false);
  const [kickoffOpen, setKickoffOpen] = useState(false);
  const [kickoffBrief, setKickoffBrief] = useState(initialBrief);
  const [kickoffBusy, setKickoffBusy] = useState(false);
  const [kickoffError, setKickoffError] = useState<string | null>(null);
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

  const movableIds = plan.filter(item => canEdit && isReorderable(item)).map(item => item.id);
  const reorder = async (sourceId: string, targetId: string) => {
    const ids = movableIds.slice();
    const from = ids.indexOf(sourceId);
    const to = ids.indexOf(targetId);
    if (from < 0 || to < 0 || from === to || savingOrder) return;
    ids.splice(from, 1);
    ids.splice(to, 0, sourceId);
    setSavingOrder(true);
    try {
      await onReorder(ids);
    } catch {
      // The parent reports the save error; keep the server's order as the source of truth.
    } finally {
      setSavingOrder(false);
      setDraggedId(null);
    }
  };

  const moveBy = (id: string, offset: number) => {
    const index = movableIds.indexOf(id);
    const target = movableIds[index + offset];
    if (target) void reorder(id, target);
  };

  const submitKickoff = async (event: React.FormEvent) => {
    event.preventDefault();
    const brief = kickoffBrief.trim();
    if (!brief || !onKickoff || kickoffBusy) return;
    setKickoffBusy(true);
    setKickoffError(null);
    try {
      await onKickoff(brief);
      setKickoffOpen(false);
    } catch (error) {
      setKickoffError(error instanceof Error ? error.message : 'Planning could not start. Try again.');
    } finally {
      setKickoffBusy(false);
    }
  };

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
        const reorderable = canEdit && isReorderable(item);
        const moveIndex = movableIds.indexOf(item.id);
        const note = item.notes || statusLabels[item.status];
        return (
          <div
            key={item.id}
            className={`${statusStyles[item.status] ?? 'plan-item'} group ${draggedId === item.id ? 'opacity-50' : ''}`}
            style={{ gridTemplateColumns: reorderable ? '30px 1fr auto' : '18px 1fr auto' }}
            onDragOver={event => { if (reorderable && draggedId) event.preventDefault(); }}
            onDrop={event => {
              if (!reorderable || !draggedId) return;
              event.preventDefault();
              void reorder(draggedId, item.id);
            }}
          >
            {reorderable ? (
              <div className="flex items-center gap-0.5 text-[var(--faint)]">
                <button
                  type="button"
                  draggable={!savingOrder}
                  onDragStart={event => { event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', item.id); setDraggedId(item.id); }}
                  onDragEnd={() => setDraggedId(null)}
                  className="grid min-h-9 min-w-9 cursor-grab touch-none place-items-center rounded p-0.5 hover:text-[var(--ink)] focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--coord)]"
                  aria-label={`Drag to reorder ${item.title}`}
                  title="Drag to reorder"
                  disabled={savingOrder}
                ><GripVertical className="h-4 w-4" aria-hidden="true" /></button>
                <span className="ic" />
              </div>
            ) : <span className="ic" />}
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
              <div className="flex items-center gap-0.5">
                {reorderable && (
                  <>
                    <button type="button" className="grid min-h-9 min-w-9 place-items-center rounded p-1 text-[var(--faint)] hover:text-[var(--ink)] disabled:opacity-30" onClick={() => moveBy(item.id, -1)} disabled={savingOrder || moveIndex <= 0} aria-label={`Move ${item.title} up`} title="Move up">
                      <ChevronUp className="h-3.5 w-3.5" />
                    </button>
                    <button type="button" className="grid min-h-9 min-w-9 place-items-center rounded p-1 text-[var(--faint)] hover:text-[var(--ink)] disabled:opacity-30" onClick={() => moveBy(item.id, 1)} disabled={savingOrder || moveIndex === movableIds.length - 1} aria-label={`Move ${item.title} down`} title="Move down">
                      <ChevronDown className="h-3.5 w-3.5" />
                    </button>
                  </>
                )}
                <button
                  className="rounded p-1 text-[var(--faint)] opacity-0 transition-opacity hover:text-[var(--conflict)] focus:opacity-100 group-hover:opacity-100"
                  onClick={() => remove(item.id)}
                  type="button"
                  aria-label={`Remove task: ${item.title}`}
                  title="Remove task"
                ><Trash2 className="h-3.5 w-3.5" /></button>
              </div>
            ) : <span />}
          </div>
        );
      })}

      {plan.length === 0 && (
        <div className="space-y-2">
          <p className="text-[12px] text-[var(--faint)]">Tell the agent what to build in the chat. Each request shows up here as a task.</p>
          {canApprove && onKickoff && (
            <button className="btn w-full" onClick={() => { setKickoffBrief(initialBrief); setKickoffError(null); setKickoffOpen(true); }} type="button">Plan it with me</button>
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

      {kickoffOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4" onMouseDown={event => { if (event.target === event.currentTarget && !kickoffBusy) setKickoffOpen(false); }}>
          <form role="dialog" aria-modal="true" aria-labelledby="kickoff-title" onSubmit={submitKickoff} className="w-full max-w-lg rounded-xl border border-[var(--line)] bg-[var(--panel)] p-5 shadow-2xl">
            <div className="mb-4 flex items-start justify-between gap-4">
              <div>
                <p className="mono mb-1 text-[10px] uppercase tracking-[.16em] text-[var(--coord)]">Plan it with me · 1 of 2</p>
                <h2 id="kickoff-title" className="text-base font-semibold text-[var(--ink)]">What should we build?</h2>
                <p className="mt-1 text-sm text-[var(--muted)]">Start with your brief. The agent will ask follow-up questions before drafting the plan.</p>
              </div>
              <button type="button" className="rounded p-1 text-[var(--muted)] hover:text-[var(--ink)]" onClick={() => setKickoffOpen(false)} disabled={kickoffBusy} aria-label="Close brief dialog"><X className="h-4 w-4" /></button>
            </div>
            <label htmlFor="kickoff-brief" className="mb-1.5 block text-xs font-medium text-[var(--muted)]">Project brief</label>
            <textarea id="kickoff-brief" autoFocus required maxLength={2000} rows={6} value={kickoffBrief} onChange={event => setKickoffBrief(event.target.value)} placeholder="Describe what you want the agent to build or improve…" className="w-full resize-y rounded-lg border border-[var(--line)] bg-[var(--bg)] px-3 py-2.5 text-sm text-[var(--ink)] outline-none placeholder:text-[var(--faint)] focus:border-[var(--coord)]" />
            <div className="mt-1 flex justify-between text-[11px] text-[var(--faint)]"><span>Up to 2,000 characters</span><span>{kickoffBrief.length}/2000</span></div>
            {kickoffError && <p role="alert" className="mt-2 text-sm text-[var(--conflict)]">{kickoffError}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className="btn" onClick={() => setKickoffOpen(false)} disabled={kickoffBusy}>Cancel</button>
              <button type="submit" className="btn primary" disabled={!kickoffBrief.trim() || kickoffBusy}>{kickoffBusy ? 'Starting…' : 'Continue to questions'}</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
