'use client';

import React from 'react';
import dynamic from 'next/dynamic';
import { Smile } from 'lucide-react';
import { Theme, EmojiStyle } from 'emoji-picker-react';

// Loaded on first open, so the room's bundle doesn't carry the emoji data
const Picker = dynamic(() => import('emoji-picker-react'), { ssr: false, loading: () => <div className="emoji-loading">Loading…</div> });

interface EmojiButtonProps {
  onPick: (emoji: string) => void;
  label?: string;
  className?: string;
  children?: React.ReactNode;
}

export function EmojiButton({ onPick, label = 'Add emoji', className = 'btn icon', children }: EmojiButtonProps) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="emoji-anchor" ref={ref}>
      <button type="button" className={className} aria-label={label} title={label} aria-expanded={open} onClick={() => setOpen(o => !o)}>
        {children ?? <Smile className="h-4 w-4" aria-hidden="true" />}
      </button>
      {open && (
        <div className="emoji-pop" role="dialog" aria-label="Emoji picker">
          <Picker
            theme={Theme.AUTO}
            emojiStyle={EmojiStyle.NATIVE}
            lazyLoadEmojis
            previewConfig={{ showPreview: false }}
            width={300}
            height={360}
            onEmojiClick={(data) => { onPick(data.emoji); setOpen(false); }}
          />
        </div>
      )}
    </div>
  );
}
