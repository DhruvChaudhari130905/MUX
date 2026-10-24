'use client';

import React from 'react';
import { Check, Circle, Clock3, ListChecks, LoaderCircle } from 'lucide-react';
import type { PlanItem } from '@/types';

const statusLabel: Record<PlanItem['status'], string> = {
  draft: 'Needs approval',
  todo: 'Up next',
  doing: 'In progress',
  done: 'Done',
  skipped_conflict: 'Waiting on a decision',
  skipped_question: 'Waiting for an answer',
};

function StatusIcon({ status }: { status: PlanItem['status'] }) {
  if (status === 'done') return <Check className="h-4 w-4" aria-hidden="true" />;
  if (status === 'doing') return <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />;
  if (status === 'skipped_conflict' || status === 'skipped_question') return <Clock3 className="h-4 w-4" aria-hidden="true" />;
  return <Circle className="h-4 w-4" aria-hidden="true" />;
}

export function PlanPreview({ plan, title, description }: { plan: PlanItem[]; title: string; description: string }) {
  const done = plan.filter(item => item.status === 'done').length;
  const awaitingApproval = plan.some(item => item.status === 'draft');

  return (
    <div className="flex min-h-[400px] flex-col items-center justify-center overflow-y-auto px-5 py-10 sm:px-10">
      <div className="w-full max-w-3xl">
        <div className="mb-7 flex items-center gap-4">
          <div className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-[var(--coord)]/10 text-[var(--coord)] ring-1 ring-[var(--coord)]/20">
            <ListChecks className="h-6 w-6" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <p className="mono mb-1 text-[10px] uppercase tracking-[.16em] text-[var(--coord)]">Plan preview</p>
            <h2 className="truncate text-xl font-semibold text-[var(--ink)]">{title || 'Project plan'}</h2>
          </div>
        </div>

        {description && <p className="mb-6 max-w-2xl text-sm leading-6 text-[var(--muted)]">{description}</p>}

        <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[var(--line)] bg-[var(--panel)] px-4 py-3">
          <div>
            <p className="text-sm font-medium text-[var(--ink)]">{awaitingApproval ? 'Draft · ready for owner review' : 'Plan in progress'}</p>
            <p className="mt-0.5 text-xs text-[var(--muted)]">{done} of {plan.length} tasks complete</p>
          </div>
          {awaitingApproval && <span className="rounded-full border border-[var(--coord)]/30 bg-[var(--coord)]/10 px-2.5 py-1 text-[11px] font-medium text-[var(--coord)]">Approval needed</span>}
        </div>

        <ol className="divide-y divide-[var(--line)] overflow-hidden rounded-lg border border-[var(--line)] bg-[var(--panel)]" aria-label="Planned tasks">
          {plan.map((item, index) => (
            <li key={item.id} className="flex items-start gap-3 px-4 py-3.5">
              <span className={`mt-0.5 ${item.status === 'done' ? 'text-[var(--coder)]' : item.status === 'doing' ? 'text-[var(--coord)]' : 'text-[var(--faint)]'}`}>
                <StatusIcon status={item.status} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                  <p className="text-sm font-medium text-[var(--ink)]"><span className="mr-2 font-mono text-xs text-[var(--faint)]">{String(index + 1).padStart(2, '0')}</span>{item.title}</p>
                  <span className="text-[11px] text-[var(--muted)]">{statusLabel[item.status]}</span>
                </div>
                {item.notes && <p className="mt-1 text-xs leading-5 text-[var(--muted)]">{item.notes}</p>}
              </div>
            </li>
          ))}
        </ol>

        {awaitingApproval && <p className="mt-4 text-center text-xs text-[var(--muted)]">The owner can edit the task order and approve the plan in Decisions &amp; plan.</p>}
      </div>
    </div>
  );
}
