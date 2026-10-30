'use client';

import { useEffect } from 'react';

// Close a room dialog on Escape while it is open. Room dialogs are plain overlay divs (the browser
// gives them no Escape behavior natively), so they need this one listener each.
export function useEscapeClose(isOpen: boolean, onClose: () => void) {
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);
}
