'use client';

import React from 'react';
import { api } from '@/lib/api';

// Fetched with the sign-in token (an <img src> to the API couldn't send it) and shown from an object URL
export function MessageImage({ roomId, uploadId }: { roomId: string; uploadId: string }) {
  const [url, setUrl] = React.useState<string | null>(null);
  const [failed, setFailed] = React.useState(false);
  const [open, setOpen] = React.useState(false);

  React.useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    api.fetchUpload(roomId, uploadId)
      .then(blob => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [roomId, uploadId]);

  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  if (failed) return <div className="msg-photo placeholder">Couldn&apos;t load image</div>;
  if (!url) return <div className="msg-photo placeholder" aria-busy="true" />;
  return (
    <>
      <button type="button" className="msg-photo" onClick={() => setOpen(true)} aria-label="Open photo">
        {/* eslint-disable-next-line @next/next/no-img-element -- an object URL from an authenticated fetch */}
        <img src={url} alt="Shared photo" />
      </button>
      {open && (
        <div className="lightbox" role="dialog" aria-label="Photo" onClick={() => setOpen(false)}>
          {/* eslint-disable-next-line @next/next/no-img-element -- same object URL */}
          <img src={url} alt="Shared photo, full size" />
        </div>
      )}
    </>
  );
}
