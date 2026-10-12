'use client';

import React from 'react';
import { SmilePlus } from 'lucide-react';
import { api } from '@/lib/api';
import { EmojiButton } from './EmojiPicker';

const QUICK = ['👍', '❤️', '😂', '🎉', '👀'];

interface ReactionsProps {
  roomId: string;
  messageId: string;
  reactions?: Record<string, string[]>;
  currentUserId: string;
  canReact: boolean;
  names?: Record<string, string>;
}

// The new state arrives as message.reacted over the room socket, so nothing is updated here
export function ReactionChips({ roomId, messageId, reactions, currentUserId, canReact, names = {} }: ReactionsProps) {
  const entries = Object.entries(reactions ?? {});
  if (!entries.length) return null;
  return (
    <div className="reactions">
      {entries.map(([emoji, users]) => {
        const mine = users.includes(currentUserId);
        const who = users.map(u => (u === currentUserId ? 'You' : names[u] ?? 'Someone')).join(', ');
        return (
          <button
            key={emoji}
            type="button"
            className={`reaction${mine ? ' mine' : ''}`}
            title={who}
            aria-pressed={mine}
            disabled={!canReact}
            onClick={() => void toggle(roomId, messageId, emoji)}
          >
            <span>{emoji}</span> <span className="mono">{users.length}</span>
          </button>
        );
      })}
    </div>
  );
}

export function ReactionBar({ roomId, messageId }: { roomId: string; messageId: string }) {
  return (
    <div className="react-bar" role="toolbar" aria-label="React">
      {QUICK.map(emoji => (
        <button key={emoji} type="button" aria-label={`React ${emoji}`} onClick={() => void toggle(roomId, messageId, emoji)}>
          {emoji}
        </button>
      ))}
      <EmojiButton onPick={emoji => void toggle(roomId, messageId, emoji)} label="More reactions" className="">
        <SmilePlus className="h-3.5 w-3.5" aria-hidden="true" />
      </EmojiButton>
    </div>
  );
}

async function toggle(roomId: string, messageId: string, emoji: string) {
  try {
    await api.toggleReaction(roomId, messageId, emoji);
  } catch (error) {
    console.error('Failed to react:', error);
  }
}
