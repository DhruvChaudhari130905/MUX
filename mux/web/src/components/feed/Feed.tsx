'use client';

import React from 'react';
import { Bot, Users, Check, X, Hammer, FileText, Flag, ChevronDown } from 'lucide-react';
import type { AgentActivity, Message, MessageTo, User } from '@/types';
import { FeedItem } from './FeedItem';
import { ConflictBanner } from './ConflictBanner';
import { Composer } from './Composer';
import { CollapseButton } from '@/components/room/PanelRail';

// The left panel has two windows so conversations don't mix:
// - Agent: what you ask the agent, its replies, and its live work (files read and edited, builds)
// - Team: notes between people, which the agent never sees

interface FeedProps {
  messages: Message[];
  activity?: AgentActivity[];
  currentUser: User;
  onSendMessage: (text: string, to: MessageTo) => void;
  canPostTeam?: boolean;
  activeConflict?: { id: string; taskId: string; options: string[] };
  activeQuestion?: { id: string; taskId: string };
  // Hidden (still mounted, so the draft and scroll position survive) while collapsed to its rail
  collapsed?: boolean;
  onCollapse?: () => void;
  roomId: string;
  canReact: boolean;
  memberNames?: Record<string, string>;
}

type Window = 'agent' | 'team';

// Agent window rows: messages, and runs of consecutive work steps grouped into one card
type Row =
  | { type: 'message'; key: string; message: Message }
  | { type: 'work'; key: string; steps: AgentActivity[] };

const STEPS_SHOWN = 5;

function time(value: string): number {
  const t = new Date(value).getTime();
  return Number.isNaN(t) ? 0 : t;
}

function agentRows(messages: Message[], activity: AgentActivity[]): Row[] {
  const items = [
    ...messages.map(m => ({ at: time(m.created_at), row: { type: 'message' as const, key: m.id, message: m } })),
    ...activity.map(a => ({ at: time(a.ts), step: a })),
  ].sort((a, b) => a.at - b.at);

  const rows: Row[] = [];
  for (const item of items) {
    if ('row' in item) {
      rows.push(item.row);
      continue;
    }
    const last = rows[rows.length - 1];
    if (last?.type === 'work') last.steps.push(item.step);
    else rows.push({ type: 'work', key: `w-${item.step.id}`, steps: [item.step] });
  }
  return rows;
}

function StepIcon({ step }: { step: AgentActivity }) {
  if (step.pending) return <span className="h-3 w-3 flex-none animate-spin rounded-full border-[1.5px] border-[var(--coder)] border-t-transparent" aria-label="Running" />;
  if (step.ok === false) return <X className="h-3.5 w-3.5 flex-none text-[var(--conflict)]" aria-label="Failed" />;
  if (step.kind === 'build') return <Hammer className="h-3.5 w-3.5 flex-none text-[#56d364]" aria-hidden="true" />;
  if (step.kind === 'task') return <Flag className="h-3.5 w-3.5 flex-none text-[var(--coord)]" aria-hidden="true" />;
  if (step.kind === 'done') return <Check className="h-3.5 w-3.5 flex-none text-[#56d364]" aria-hidden="true" />;
  return <FileText className="h-3.5 w-3.5 flex-none text-[var(--muted)]" aria-hidden="true" />;
}

// A run of agent steps: the newest few, with the rest behind "Show all"
function WorkCard({ steps, live }: { steps: AgentActivity[]; live: boolean }) {
  const [open, setOpen] = React.useState(false);
  const shown = open ? steps : steps.slice(-STEPS_SHOWN);
  const hidden = steps.length - shown.length;
  const failed = steps.filter(s => s.ok === false).length;
  return (
    <div className={`work-card ${live ? 'live' : ''}`}>
      <div className="work-head">
        <span className="agent-av av" aria-hidden="true">M</span>
        <span className="font-semibold text-[var(--ink)]">{live ? 'Agent is working' : 'Agent worked'}</span>
        <span className="mono text-[11px] text-[var(--faint)]">
          {steps.length} step{steps.length === 1 ? '' : 's'}{failed ? ` · ${failed} failed` : ''}
        </span>
      </div>
      {hidden > 0 && (
        <button type="button" className="work-more" onClick={() => setOpen(true)}>
          <ChevronDown className="h-3 w-3" /> Show {hidden} earlier step{hidden === 1 ? '' : 's'}
        </button>
      )}
      <ol className="work-steps">
        {shown.map(step => (
          <li key={step.id} className={`work-step item-in ${step.pending ? 'pending' : ''}`}>
            <StepIcon step={step} />
            <span className="min-w-0 flex-1">
              <span className="block truncate" title={step.label}>{step.label}</span>
              {step.detail && <span className="block truncate text-[11px] text-[var(--conflict)]" title={step.detail}>{step.detail}</span>}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function Feed({
  messages,
  activity = [],
  currentUser,
  onSendMessage,
  canPostTeam,
  activeConflict,
  collapsed,
  onCollapse,
  roomId,
  canReact,
  memberNames,
}: FeedProps) {
  const [win, setWin] = React.useState<Window>('agent');
  const feedEndRef = React.useRef<HTMLDivElement>(null);

  const agentMessages = React.useMemo(() => messages.filter(m => m.to !== 'team'), [messages]);
  const teamMessages = React.useMemo(() => messages.filter(m => m.to === 'team'), [messages]);
  const rows = React.useMemo(() => agentRows(agentMessages, activity), [agentMessages, activity]);
  const current = [...activity].reverse().find(a => a.pending);

  // Unread: messages that arrived after you last looked at that window. Counting by time, not by list
  // length, keeps the history replayed when the room opens from showing up as unread.
  const [lastSeen, setLastSeen] = React.useState(() => ({ agent: Date.now(), team: Date.now() }));
  React.useEffect(() => {
    setLastSeen(s => ({ ...s, [win]: Date.now() }));
  }, [win, agentMessages.length, teamMessages.length]);
  const newer = (list: Message[], since: number) => list.filter(m => time(m.created_at) > since && m.user_id !== currentUser.id).length;
  const unread = { agent: newer(agentMessages, lastSeen.agent), team: newer(teamMessages, lastSeen.team) };

  React.useEffect(() => {
    feedEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [win, rows.length, teamMessages.length, activity.length]);

  const tab = (key: Window, label: string, Icon: typeof Bot) => (
    <button
      type="button"
      role="tab"
      aria-selected={win === key}
      onClick={() => setWin(key)}
      className="tab flex items-center gap-1.5"
    >
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      {label}
      {key === 'agent' && current && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--coder)]" aria-label="working" />}
      {win !== key && unread[key] > 0 && <span className="feed-badge">{unread[key]}</span>}
    </button>
  );

  return (
    <section className="col flex flex-col" aria-label="Agent and team feed" hidden={collapsed}>
      <div className="col-head">
        <div className="tabs" role="tablist" aria-label="Feed window">
          {tab('agent', 'Agent', Bot)}
          {tab('team', 'Team', Users)}
        </div>
        <span className="flex items-center gap-2">
          {onCollapse && <CollapseButton side="left" label="feed" onCollapse={onCollapse} />}
        </span>
      </div>

      {win === 'agent' && activeConflict && (
        <ConflictBanner conflictId={activeConflict.id} taskId={activeConflict.taskId} options={activeConflict.options} />
      )}

      <div className="scroll">
        <div className="feed" role="tabpanel" aria-label={win === 'agent' ? 'Agent window' : 'Team window'}>
          {win === 'agent' ? (
            rows.length === 0 ? (
              <p className="feed-empty">Ask the agent for something below. Its replies and every file it reads, edits and builds show up here.</p>
            ) : (
              rows.map((row, i) =>
                row.type === 'message' ? (
                  <FeedItem key={row.key} message={row.message} currentUser={currentUser} roomId={roomId} canReact={canReact} memberNames={memberNames} />
                ) : (
                  <WorkCard key={row.key} steps={row.steps} live={i === rows.length - 1 && row.steps.some(s => s.pending)} />
                ),
              )
            )
          ) : teamMessages.length === 0 ? (
            <p className="feed-empty">Notes between people in the room. The agent never sees these.</p>
          ) : (
            teamMessages.map(m => <FeedItem key={m.id} message={m} currentUser={currentUser} roomId={roomId} canReact={canReact} memberNames={memberNames} />)
          )}
          <div ref={feedEndRef} />
        </div>
      </div>

      {win === 'agent' && current && (
        <div className="working-now" role="status">
          <span className="h-3 w-3 flex-none animate-spin rounded-full border-[1.5px] border-[var(--coder)] border-t-transparent" aria-hidden="true" />
          <span className="truncate">{current.label}</span>
        </div>
      )}

      {/* One message box per window, both kept mounted so a half-written message survives switching */}
      <div hidden={win !== 'agent'}>
        <Composer onSend={onSendMessage} canPostTeam={canPostTeam} target="agent" />
      </div>
      <div hidden={win !== 'team'}>
        {canPostTeam ? (
          <Composer onSend={onSendMessage} canPostTeam={canPostTeam} target="team" />
        ) : (
          <div className="composer"><p className="hint">Viewers can read team notes but not post them.</p></div>
        )}
      </div>
    </section>
  );
}
