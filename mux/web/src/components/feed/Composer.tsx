'use client';

import React, { useEffect, useId, useRef, useState, FormEvent } from 'react';
import { ImagePlus, X } from 'lucide-react';
import type { MessageTo } from '@/types';
import { EmojiButton } from './EmojiPicker';

interface ComposerProps {
  onSend: (text: string, to: MessageTo, photos?: File[]) => Promise<void> | void;
  disabled?: boolean;
  // Viewers can't post team notes (the server enforces this too)
  canPostTeam?: boolean;
  // Set by the Agent / Team windows: always send there, with no Agent/Team switch
  target?: MessageTo;
  // Photos are for people only, so only Team notes can carry them
  canAttach?: boolean;
}

// "@sam can you check this" reads as a note to a person, not an instruction for the agent
const MENTION_START = /^@\w/;

// Same limits as the server (mux/server/mux/rooms/uploads.py)
const PHOTO_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];
const MAX_PHOTO_BYTES = 5 * 1024 * 1024;
const MAX_PHOTOS = 4;

export function Composer({ onSend, disabled, canPostTeam = true, target: fixedTarget, canAttach = false }: ComposerProps) {
  const inputId = useId(); // the Agent and Team windows each have a box, so the id must be unique
  const [text, setText] = useState('');
  const [to, setTo] = useState<MessageTo>('agent');
  // Set once the person picks a side themselves, so the @mention switch doesn't fight them
  const [picked, setPicked] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [photos, setPhotos] = useState<{ file: File; url: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const photosRef = useRef(photos);
  photosRef.current = photos;
  useEffect(() => () => photosRef.current.forEach(p => URL.revokeObjectURL(p.url)), []);

  const target: MessageTo = fixedTarget ?? (canPostTeam ? to : 'agent');
  const attach = canAttach && target === 'team';

  const handleChange = (value: string) => {
    setText(value);
    if (!value) setPicked(false);
    if (!fixedTarget && canPostTeam && !picked && MENTION_START.test(value)) setTo('team');
  };

  // Insert at the cursor (or the end), then put the cursor after the emoji
  const insertEmoji = (emoji: string) => {
    const input = inputRef.current;
    const start = input?.selectionStart ?? text.length;
    const end = input?.selectionEnd ?? text.length;
    handleChange(text.slice(0, start) + emoji + text.slice(end));
    requestAnimationFrame(() => {
      input?.focus();
      input?.setSelectionRange(start + emoji.length, start + emoji.length);
    });
  };

  const addPhotos = (files: File[]) => {
    setError(null);
    const ok: File[] = [];
    for (const f of files) {
      if (!PHOTO_TYPES.includes(f.type)) { setError('Only PNG, JPEG, GIF and WebP images'); continue; }
      if (f.size > MAX_PHOTO_BYTES) { setError(`${f.name} is over 5 MB`); continue; }
      ok.push(f);
    }
    const room = MAX_PHOTOS - photos.length;
    if (ok.length > room) setError(`Up to ${MAX_PHOTOS} photos per message`);
    const added = ok.slice(0, Math.max(room, 0)).map(file => ({ file, url: URL.createObjectURL(file) }));
    if (added.length) setPhotos(prev => [...prev, ...added]);
  };

  const removePhoto = (url: string) => {
    URL.revokeObjectURL(url);
    setPhotos(prev => prev.filter(p => p.url !== url));
  };

  const onPaste = (e: React.ClipboardEvent) => {
    if (!attach) return;
    const files = Array.from(e.clipboardData.files).filter(f => f.type.startsWith('image/'));
    if (files.length) { e.preventDefault(); addPhotos(files); }
  };

  const pick = (next: MessageTo) => {
    setTo(next);
    setPicked(true);
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = text.trim();
    const sent = attach ? photos : [];
    if ((!trimmed && !sent.length) || disabled || sending) return;
    setSending(true);
    setError(null);
    try {
      await onSend(trimmed, target, sent.map(p => p.file));
      photos.forEach(p => URL.revokeObjectURL(p.url));
      setPhotos([]);
      setText('');
      setTo('agent');
      setPicked(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not send');
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="composer">
      {canPostTeam && !fixedTarget && (
        <div className="tabs to-toggle" role="tablist" aria-label="Send to">
          <button type="button" role="tab" className="tab" aria-selected={target === 'agent'} onClick={() => pick('agent')}>
            Agent
          </button>
          <button type="button" role="tab" className="tab" aria-selected={target === 'team'} onClick={() => pick('team')}>
            Team
          </button>
        </div>
      )}
      {attach && photos.length > 0 && (
        <div className="photo-previews">
          {photos.map(p => (
            <div key={p.url} className="photo-preview">
              {/* eslint-disable-next-line @next/next/no-img-element -- a local object URL */}
              <img src={p.url} alt={p.file.name} />
              <button type="button" aria-label={`Remove ${p.file.name}`} onClick={() => removePhoto(p.url)}>
                <X className="h-3 w-3" aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>
      )}
      <form onSubmit={handleSubmit}>
        <label htmlFor={inputId} className="mono" hidden>
          Message
        </label>
        <input
          ref={inputRef}
          id={inputId}
          autoComplete="off"
          placeholder={target === 'team' ? 'Note to the team…' : 'Message the agent…'}
          value={text}
          onChange={(e) => handleChange(e.target.value)}
          onPaste={onPaste}
          disabled={disabled}
        />
        {attach && (
          <>
            <button type="button" className="btn icon" aria-label="Add photo" title="Add photo" onClick={() => fileRef.current?.click()}>
              <ImagePlus className="h-4 w-4" aria-hidden="true" />
            </button>
            <input ref={fileRef} type="file" accept={PHOTO_TYPES.join(',')} multiple hidden
              onChange={(e) => { addPhotos(Array.from(e.target.files ?? [])); e.target.value = ''; }} />
          </>
        )}
        <EmojiButton onPick={insertEmoji} />
        <button className="btn primary" type="submit" disabled={disabled || sending || (!text.trim() && !(attach && photos.length))}>
          {sending ? 'Sending…' : 'Send'}
        </button>
      </form>
      {error && <div className="composer-error" role="alert">{error}</div>}
      <div className="hint">
        {target === 'team'
          ? 'Team notes go to people in the room only. The agent never sees them.'
          : 'Try "add a pricing page", "stop", or "make headings bigger". The coordinator labels each message.'}
      </div>
    </div>
  );
}
