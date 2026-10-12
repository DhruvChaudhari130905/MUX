'use client';

import React from 'react';
import type { Message, User } from '@/types';
import { LabelChip } from './LabelChip';
import { ReactionBar, ReactionChips } from './Reactions';
import { MessageImage } from './MessageImage';
import { format } from 'date-fns';

interface FeedItemProps {
  message: Message;
  currentUser: User;
  roomId: string;
  canReact: boolean;
  memberNames?: Record<string, string>;
}

export function FeedItem({ message, currentUser, roomId, canReact, memberNames }: FeedItemProps) {
  const isAgent = message.user_id === 'agent' || message.user_id === 'mux';
  // The room's own agents post replies, reviews and task summaries; nothing labels those
  const isBot = isAgent || message.user_id === 'coordinator' || message.user_id === 'coder';
  const isNote = message.to === 'team';
  // Only people's messages have server ids that reactions can point at
  const reactable = !isBot;

  return (
    <div className={`msg ${isNote ? 'note' : ''}${reactable && canReact ? ' reactable' : ''}`}>
      <span
        className={`av ${isAgent ? 'agent-av' : ''}`}
        style={{ background: isAgent ? undefined : message.user.color }}
      >
        {isAgent ? 'M' : message.user.initials}
      </span>
      <div>
        <div className="who">
          {isAgent ? 'MUX' : message.user.name}{' '}
          <time className="mono">{format(new Date(message.created_at), 'HH:mm')}</time>
          {isNote ? (
            <span className="note-tag mono">team</span>
          ) : message.label ? (
            <LabelChip label={message.label} />
          ) : (
            !isBot && <span className="chip pending">waiting for coordinator</span>
          )}
        </div>
        {message.text && <p className="whitespace-pre-wrap">{message.text}</p>}
        {message.attachments && message.attachments.length > 0 && (
          <div className="msg-photos">
            {message.attachments.map(id => <MessageImage key={id} roomId={roomId} uploadId={id} />)}
          </div>
        )}
        {reactable && (
          <ReactionChips roomId={roomId} messageId={message.id} reactions={message.reactions}
            currentUserId={currentUser.id} canReact={canReact} names={memberNames} />
        )}
      </div>
      {reactable && canReact && <ReactionBar roomId={roomId} messageId={message.id} />}
    </div>
  );
}
