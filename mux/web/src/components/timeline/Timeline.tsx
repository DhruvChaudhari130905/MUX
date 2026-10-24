'use client';

import React from 'react';
import type { Checkpoint } from '@/types';
import { format } from 'date-fns';
import { PanelBottomClose, PanelBottomOpen } from 'lucide-react';

interface TimelineProps {
  checkpoints: Checkpoint[];
  currentCheckpoint: string | null;
  onSelectCheckpoint: (checkpointId: string) => void;
  onReturnToLatest: () => void;
  isRewound: boolean;
  activeCheckpointId: string | null;
  returnCheckpointId: string | null;
  // Collapsed, only the header row shows and Preview/Code gets the height
  collapsed?: boolean;
  onToggle?: (open: boolean) => void;
}

export function Timeline({
  checkpoints,
  currentCheckpoint,
  onSelectCheckpoint,
  onReturnToLatest,
  isRewound,
  activeCheckpointId,
  returnCheckpointId,
  collapsed,
  onToggle,
}: TimelineProps) {
  const sortedCheckpoints = [...checkpoints].sort((a, b) => a.seq - b.seq);
  const byId = new Map(checkpoints.map(checkpoint => [checkpoint.id, checkpoint]));
  const activeBranch = new Set<string>();
  let branchNode = activeCheckpointId ? byId.get(activeCheckpointId) : undefined;
  while (branchNode && !activeBranch.has(branchNode.id)) {
    activeBranch.add(branchNode.id);
    branchNode = branchNode.parent_id ? byId.get(branchNode.parent_id) : undefined;
  }

  return (
    <footer className={`timeline${collapsed ? ' collapsed' : ''}`}>
      <div className="tl-head">
        <span>Timeline</span>
        {collapsed && <span>{sortedCheckpoints.length} checkpoint{sortedCheckpoints.length === 1 ? '' : 's'}</span>}
        {isRewound && (
          <>
            <span className="note">Viewing a saved branch. Later checkpoints remain available.</span>
            <button className="btn" onClick={onReturnToLatest} type="button">
              Return to saved present
            </button>
          </>
        )}
        {onToggle && (
          <button
            className="panel-collapse ml-auto"
            onClick={() => onToggle(!!collapsed)}
            type="button"
            aria-expanded={!collapsed}
            aria-label={collapsed ? 'Show timeline' : 'Hide timeline'}
            title={collapsed ? 'Show timeline' : 'Hide timeline'}
          >
            {collapsed ? <PanelBottomOpen className="h-3.5 w-3.5" /> : <PanelBottomClose className="h-3.5 w-3.5" />}
          </button>
        )}
      </div>
      <div className="track-wrap" hidden={collapsed}>
        <div className="track">
          {sortedCheckpoints.length === 0 && (
            <p className="relative z-[1] mx-auto bg-[var(--panel)] px-3 text-xs text-[var(--faint)]">
              No checkpoints yet. Each passing build adds one here, and you can rewind to any of them.
            </p>
          )}
          {sortedCheckpoints.map((checkpoint, index) => {
            const isCurrent = checkpoint.id === currentCheckpoint;
            const isArchived = !activeBranch.has(checkpoint.id);
            const isLatest = checkpoint.id === activeCheckpointId;

            return (
              <button
                key={checkpoint.id}
                className={`timeline-node ${isCurrent ? 'now' : ''} ${isLatest ? 'live' : ''} ${isArchived ? 'archived' : ''} ${checkpoint.id === returnCheckpointId ? 'return-point' : ''}`}
                onClick={() => onSelectCheckpoint(checkpoint.id)}
                type="button"
                data-i={index}
              >
                <span className="pt">{index}</span>
                <span className="lb">
                  {checkpoint.plan[checkpoint.plan.length - 1]?.title || checkpoint.description || `Checkpoint ${index}`}
                  <small>
                    {format(new Date(checkpoint.created_at), 'HH:mm')}
                    {checkpoint.sandbox_snapshot_uuid && ' · Nebius'}
                    {checkpoint.id === returnCheckpointId && ' · saved present'}
                    {isArchived && ' · other branch'}
                  </small>
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </footer>
  );
}
