'use client';

import React, { useState, useEffect, useRef } from 'react';
import type { Conflict, DomainRole, User } from '@/types';
import { api } from '@/lib/api';
import { format } from 'date-fns';

// The domain role whose vote counts double for each conflict domain
const DOMAIN_ROLE_FOR: Record<Conflict['domain'], DomainRole> = {
  ui: 'design',
  architecture: 'eng',
  scope: 'pm',
};

interface ConflictCardProps {
  conflict: Conflict;
  currentUser: User;
  currentUserRole: 'owner' | 'editor' | 'viewer';
  currentUserDomainRole: 'pm' | 'design' | 'eng';
  onVote: (conflictId: string, option: string) => void;
  onOverride: (conflictId: string, option: string) => void;
}

export function ConflictCard({
  conflict,
  currentUser,
  currentUserRole,
  currentUserDomainRole,
  onVote,
  onOverride,
}: ConflictCardProps) {
  const [timeLeft, setTimeLeft] = useState(60);
  const [votes, setVotes] = useState<Record<string, number>>({});
  const [userVote, setUserVote] = useState<string | null>(null);
  const [voteDone, setVoteDone] = useState(false);
  const lastSeed = useRef<string | null>(null);

  // Initialize votes
  useEffect(() => {
    // Only re-seed when the recorded votes, status or user actually changed: the room pushes many
    // events (typing, messages) with fresh conflict objects, and re-seeding on those would drop the
    // user's just-recorded vote until the next server update arrives
    const seedKey = JSON.stringify([conflict.votes, conflict.status, currentUser.id]);
    if (seedKey === lastSeed.current) return;
    lastSeed.current = seedKey;

    const initialVotes: Record<string, number> = {};
    conflict.options.forEach(opt => { initialVotes[opt] = 0; });
    conflict.votes.forEach(v => { initialVotes[v.option] = (initialVotes[v.option] || 0) + v.weight; });
    setVotes(initialVotes);

    // Recomputed when the signed-in user changes too, so one person's vote never shows as another's
    const myVote = conflict.votes.find(v => v.user_id === currentUser.id);
    setUserVote(myVote ? myVote.option : null);

    if (conflict.status === 'closed') setVoteDone(true);
  }, [conflict, currentUser.id]);

  // Timer
  useEffect(() => {
    if (voteDone) return;
    const interval = setInterval(() => {
      setTimeLeft(prev => {
        if (prev <= 1) {
          setVoteDone(true);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [voteDone]);

  // Weighted tally (numbers on each option) and voter count are different units: the weights are
  // 2× for the domain role, so they must never be shown against a count of vote records
  const myWeight = DOMAIN_ROLE_FOR[conflict.domain] === currentUserDomainRole ? 2 : 1;
  const totalVotes = Object.values(votes).reduce((a, b) => a + b, 0);
  const voters = new Set(conflict.votes.map(v => v.user_id));
  if (userVote) voters.add(currentUser.id);

  const handleVote = (option: string) => {
    if (voteDone || currentUserRole === 'viewer') return;
    onVote(conflict.id, option);
    setVotes(prev => {
      const next = { ...prev };
      // switching options moves the vote: take the previous weight back before adding the new one
      if (userVote) next[userVote] = Math.max(0, (next[userVote] || 0) - myWeight);
      next[option] = (next[option] || 0) + myWeight;
      return next;
    });
    setUserVote(option);
  };

  const handleOverride = (option: string) => {
    if (currentUserRole !== 'owner') return;
    onOverride(conflict.id, option);
    setVoteDone(true);
  };

  return (
    <div className="card conflict">
      <div className="card-head">
        <span className="card-kind">Conflict · task {conflict.task_id}</span>
        <span className="timer mono">{format(new Date(0).setSeconds(timeLeft), 'm:ss')}</span>
      </div>
      <h4>{conflict.options.length > 1 ? `What should we do?` : conflict.options[0]}</h4>
      <p className="why">
        {conflict.domain === 'ui' && 'Design decision needed. '}
        {conflict.domain === 'architecture' && 'Architecture decision needed. '}
        {conflict.domain === 'scope' && 'Scope decision needed. '}
        The coder skipped this task until the vote closes.
      </p>

      {!voteDone ? (
        <div className="opts">
          {conflict.options.map((option, index) => (
            <button
              key={option}
              className="opt"
              onClick={() => handleVote(option)}
              aria-pressed={userVote === option}
              disabled={voteDone || currentUserRole === 'viewer'}
              data-opt={option}
            >
              <span className="lbl">{option}</span>
              <span className="n mono">{votes[option] || 0}</span>
              <span className="meter">
                <i style={{ width: totalVotes > 0 ? `${((votes[option] || 0) / totalVotes) * 100}%` : '0%' }} />
              </span>
            </button>
          ))}
        </div>
      ) : (
        <div className="resolved">
          Decided: {conflict.result} ({conflict.resolved_by === currentUser.id ? 'your override' : 'vote closed'}). Pinned in the room log.
        </div>
      )}

      <div className="voters">
        <span>
          {voters.size} editors voted{' '}
          {DOMAIN_ROLE_FOR[conflict.domain] === currentUserDomainRole && '· your role counts 2×'}
        </span>
        {currentUserRole === 'owner' && !voteDone && (
          <button className="linkbtn" onClick={() => handleOverride(conflict.options[1] || conflict.options[0])} type="button">
            Owner override
          </button>
        )}
      </div>

      {conflict.evidence.length > 0 && (
        <div className="mt-4 p-3 bg-[var(--panel)] rounded border border-[var(--line)]">
          <p className="text-sm font-medium mb-2">Tavily Evidence</p>
          {conflict.evidence.map((ev, i) => (
            <div key={i} className="text-xs text-[var(--muted)] mb-2">
              <p className="font-mono">{ev.query}</p>
              <p>{ev.summary}</p>
              {ev.citations.map((c, j) => (
                <a key={j} href={c} target="_blank" rel="noopener" className="text-[var(--coord)] underline text-xs">
                  [{j + 1}]
                </a>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}