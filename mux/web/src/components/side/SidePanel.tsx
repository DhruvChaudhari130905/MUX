'use client';

import React, { useState } from 'react';
import type { RoomState, Conflict, Question, PlanItem } from '@/types';
import { ConflictCard } from './ConflictCard';
import { QuestionCard } from './QuestionCard';
import { ChangeRequestCard } from './ChangeRequestCard';
import { PlanList } from './PlanList';
import { CollapseButton } from '@/components/room/PanelRail';

// Turned-down changes the requester has dismissed (this browser only)
const DISMISSED_KEY = 'mux:dismissed-change-requests';

function readDismissed(): string[] {
  try {
    const raw = localStorage.getItem(DISMISSED_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function writeDismissed(ids: string[]) {
  try {
    localStorage.setItem(DISMISSED_KEY, JSON.stringify(ids.slice(-200)));
  } catch {
    // storage unavailable: dismissals last until reload
  }
}

interface SidePanelProps {
  state: RoomState;
  roomId: string;
  nameOf: (userId: string) => string;
  currentUserRole: 'owner' | 'editor' | 'viewer';
  currentUserDomainRole: 'pm' | 'design' | 'eng';
  onVote: (conflictId: string, option: string) => void;
  onOverride: (conflictId: string, option: string) => void;
  onAnswer: (questionId: string, answer: string) => void;
  onPlanUpdate: (items: PlanItem[]) => void;
  onPlanReorder: (itemIds: string[]) => Promise<void> | void;
  onPlanApprove: () => void;
  onPlanRun: () => void;
  roomDescription: string;
  onKickoff: (brief: string) => Promise<void> | void;
  // Hidden (still mounted) while collapsed to its rail
  collapsed?: boolean;
  onCollapse?: () => void;
}

export function SidePanel({
  state,
  roomId,
  nameOf,
  currentUserRole,
  currentUserDomainRole,
  onVote,
  onOverride,
  onAnswer,
  onPlanUpdate,
  onPlanReorder,
  onPlanApprove,
  onPlanRun,
  onKickoff,
  roomDescription,
  collapsed,
  onCollapse,
}: SidePanelProps) {
  const openConflicts = state.conflicts.filter(c => c.status === 'open' || c.status === 'voting');
  const openQuestions = state.questions.filter(q => q.status === 'open');
  // Held changes this person can decide, and their own (pending, or turned down: they can copy their edit back)
  const me = state.current_user.id;
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set(readDismissed()));
  const changes = (state.room.change_requests ?? []).filter(r =>
    r.status === 'pending' ? r.approvers.includes(me) || r.requested_by === me : r.requested_by === me && !dismissed.has(r.id));
  const dismiss = (id: string) => setDismissed(prev => {
    const next = new Set(prev).add(id);
    writeDismissed([...next]);
    return next;
  });
  const openCount = changes.filter(r => r.status === 'pending').length;

  return (
    <aside className="col flex flex-col" aria-label="Decisions and plan" hidden={collapsed}>
      <div className="col-head">
        <span>Decisions & plan</span>
        <span className="flex items-center gap-2">
          <span>{openConflicts.length + openQuestions.length + openCount} open</span>
          {onCollapse && <CollapseButton side="right" label="decisions and plan" onCollapse={onCollapse} />}
        </span>
      </div>
      <div className="scroll">
        <div className="side">
          {openConflicts.map(conflict => (
            <ConflictCard
              key={conflict.id}
              conflict={conflict}
              currentUser={state.current_user}
              currentUserRole={currentUserRole}
              currentUserDomainRole={currentUserDomainRole}
              onVote={onVote}
              onOverride={onOverride}
            />
          ))}

          {changes.map(request => (
            <ChangeRequestCard key={request.id} roomId={roomId} request={request} currentUserId={me} nameOf={nameOf} onDismiss={dismiss} />
          ))}

          {openQuestions.map(question => (
            <QuestionCard
              key={question.id}
              question={question}
              currentUser={state.current_user}
              currentUserRole={currentUserRole}
              onAnswer={onAnswer}
            />
          ))}

          <PlanList
            plan={state.plan}
            canEdit={currentUserRole !== 'viewer'}
            canApprove={currentUserRole === 'owner'}
            onUpdate={onPlanUpdate}
            onReorder={onPlanReorder}
            onApprove={onPlanApprove}
            onRun={onPlanRun}
            onKickoff={onKickoff}
            initialBrief={roomDescription}
          />
        </div>
      </div>
    </aside>
  );
}
