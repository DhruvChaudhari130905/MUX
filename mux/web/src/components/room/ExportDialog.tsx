'use client';

import React, { useState } from 'react';
import { X, Github, Lock, Globe, ExternalLink, Check } from 'lucide-react';
import type { Room } from '@/types';
import { api } from '@/lib/api';
import { useEscapeClose } from '@/lib/useEscapeClose';

interface ExportDialogProps {
  isOpen: boolean;
  onClose: () => void;
  room: Room;
}

// Repo names: lowercase letters, digits, dashes
function slugify(title: string): string {
  return title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'mux-app';
}

export function ExportDialog({ isOpen, onClose, room }: ExportDialogProps) {
  const [repoName, setRepoName] = useState(() => slugify(room.title));
  const [isPrivate, setIsPrivate] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [repoUrl, setRepoUrl] = useState<string | null>(null);

  useEscapeClose(isOpen, onClose);

  if (!isOpen) return null;

  const handleExport = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = slugify(repoName);
    if (!name) return;
    setExporting(true);
    setError(null);
    try {
      const { url } = await api.exportToGitHub(room.id, name, isPrivate);
      setRepoUrl(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export failed. Is GitHub connected on your profile?');
    } finally {
      setExporting(false);
    }
  };

  const handleClose = () => {
    setRepoUrl(null);
    setError(null);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center glass-overlay p-4" onClick={handleClose}>
      <div
        className="w-full max-w-md rounded-2xl glass-modal p-6"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="export-title"
      >
        <div className="mb-5 flex items-center justify-between">
          <h2 id="export-title" className="flex items-center gap-2 text-lg font-semibold">
            <Github className="h-5 w-5" />
            Export to GitHub
          </h2>
          <button className="btn p-2" onClick={handleClose} type="button" aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>

        {repoUrl ? (
          <div className="space-y-4">
            <div className="flex items-start gap-3 rounded-lg border border-[var(--coord)]/40 bg-[var(--coord)]/10 p-4">
              <Check className="mt-0.5 h-5 w-5 flex-none text-[var(--coord)]" />
              <div>
                <p className="font-medium">Export started</p>
                <p className="text-sm text-[var(--muted)]">The current checkpoint is being pushed to your new repository.</p>
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button className="btn" onClick={handleClose} type="button">Close</button>
              <a className="btn primary flex items-center gap-1.5" href={repoUrl} target="_blank" rel="noopener noreferrer">
                Open repository
                <ExternalLink className="h-4 w-4" />
              </a>
            </div>
          </div>
        ) : (
          <form onSubmit={handleExport} className="space-y-4">
            <div>
              <label htmlFor="repo-name" className="mb-1 block text-sm font-medium">Repository name</label>
              <input
                id="repo-name"
                value={repoName}
                onChange={e => setRepoName(e.target.value)}
                className="w-full rounded border border-[var(--line)] bg-[var(--bg)] px-3 py-2 font-mono text-sm"
                autoFocus
              />
              <p className="mt-1 text-xs text-[var(--faint)]">Will be created as <span className="mono">{slugify(repoName)}</span></p>
            </div>

            <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="Visibility">
              {[
                { value: true, label: 'Private', icon: Lock, hint: 'Only you and invitees' },
                { value: false, label: 'Public', icon: Globe, hint: 'Anyone can see it' },
              ].map(opt => {
                const Icon = opt.icon;
                const selected = isPrivate === opt.value;
                return (
                  <button
                    key={opt.label}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    onClick={() => setIsPrivate(opt.value)}
                    className={`rounded-lg border p-3 text-left transition-colors ${
                      selected ? 'border-[var(--coord)] bg-[var(--coord)]/10' : 'border-[var(--line)] bg-[var(--bg)] hover:border-[var(--faint)]'
                    }`}
                  >
                    <span className="flex items-center gap-1.5 font-medium">
                      <Icon className="h-4 w-4" /> {opt.label}
                    </span>
                    <span className="text-xs text-[var(--muted)]">{opt.hint}</span>
                  </button>
                );
              })}
            </div>

            {error && <p className="text-sm text-[var(--conflict)]">{error}</p>}

            <div className="flex justify-end gap-2 border-t border-[var(--line)] pt-4">
              <button className="btn" onClick={handleClose} type="button" disabled={exporting}>Cancel</button>
              <button className="btn primary flex items-center gap-1.5" type="submit" disabled={exporting || !slugify(repoName)}>
                <Github className="h-4 w-4" />
                {exporting ? 'Exporting…' : 'Export'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
