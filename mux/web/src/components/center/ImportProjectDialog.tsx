'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { X, FolderOpen, FileArchive, Loader2, FolderInput, Replace, Combine, FolderPlus, TriangleAlert } from 'lucide-react';
import {
  describeSkipped, importFolder, importFromZip, pickZip, type ImportResult,
} from '@/lib/projectImport';
import { FileIcon } from './FileTree';

export type ImportMode = 'replace' | 'merge' | 'folder';

interface ImportProjectDialogProps {
  existingPaths: string[];
  lockedFiles: Set<string>;
  // Set when the import started from a drop: skips straight to the preview
  initial?: Promise<ImportResult> | null;
  onImport: (files: { path: string; content: string }[], mode: ImportMode) => void;
  onClose: () => void;
}

export function ImportProjectDialog({ existingPaths, lockedFiles, initial, onImport, onClose }: ImportProjectDialogProps) {
  const [result, setResult] = useState<ImportResult | null>(null);
  const [loading, setLoading] = useState<boolean>(!!initial);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<ImportMode>('replace');
  const [folder, setFolder] = useState('');

  const load = async (p: Promise<ImportResult | null>) => {
    setLoading(true);
    setError(null);
    try {
      const r = await p;
      if (r) {
        setResult(r);
        setFolder(r.name.toLowerCase().replace(/[^a-z0-9._-]+/g, '-') || 'imported');
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  // Drop imports arrive already in progress
  const startedInitial = useRef(false);
  useEffect(() => {
    if (!initial || startedInitial.current) return;
    startedInitial.current = true;
    void load(initial);
  }, [initial]);

  const chooseFolder = () => load(importFolder());
  const chooseZip = () => load(pickZip().then(files => (files ? importFromZip(files[0]) : null)));

  const target = (path: string) => (mode === 'folder' ? `${folder.replace(/\/+$/, '')}/${path}` : path);
  const summary = useMemo(() => {
    if (!result) return null;
    const existing = new Set(existingPaths);
    const incoming = result.files.map(f => target(f.path));
    const overwritten = incoming.filter(p => existing.has(p));
    const lockedHit = incoming.filter(p => lockedFiles.has(p));
    const removed = mode === 'replace' ? existingPaths.filter(p => !incoming.includes(p) && !lockedFiles.has(p)) : [];
    return { overwritten, lockedHit, removed };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result, mode, folder, existingPaths, lockedFiles]);

  const confirm = async () => {
    if (!result) return;
    const files = result.files.map(f => ({ path: target(f.path), content: f.content })).filter(f => !lockedFiles.has(f.path));
    setLoading(true);
    setError(null);
    try {
      await onImport(files, mode);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const modes: { id: ImportMode; icon: React.ElementType; label: string; detail: string }[] = [
    { id: 'replace', icon: Replace, label: 'Replace room files', detail: 'The room becomes this project' },
    { id: 'merge', icon: Combine, label: 'Merge into the room', detail: 'Same paths are overwritten' },
    { id: 'folder', icon: FolderPlus, label: 'Into a subfolder', detail: 'Nothing existing changes' },
  ];

  return (
    <div className="absolute inset-0 z-40 grid place-items-center bg-black/50 p-4 backdrop-blur-[2px]" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-labelledby="import-title" className="item-in flex max-h-full w-full max-w-lg flex-col overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--panel)] font-sans shadow-2xl">
        <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-3">
          <h2 id="import-title" className="flex items-center gap-2 text-[14px] font-semibold text-[var(--ink)]">
            <FolderInput className="h-4 w-4 text-[var(--coord)]" /> Import project
          </h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close"><X className="h-3.5 w-3.5" /></button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto p-4">
          {loading ? (
            <p className="flex items-center justify-center gap-2 py-10 text-[12.5px] text-[var(--muted)]"><Loader2 className="h-4 w-4 animate-spin" /> Reading files…</p>
          ) : !result ? (
            <>
              <p className="mb-3 text-[12px] leading-relaxed text-[var(--muted)]">
                Bring a project in from your computer. Source files come in; <span className="mono">node_modules</span>, <span className="mono">.git</span>, build output, binaries and <span className="mono">.env</span> secrets are left out.
              </p>
              <div className="grid grid-cols-2 gap-2">
                <button type="button" onClick={chooseFolder} className="flex flex-col items-center gap-2 rounded-lg border border-[var(--line)] bg-[var(--bg)] px-3 py-5 text-center transition-colors hover:border-[var(--coord)]">
                  <FolderOpen className="h-6 w-6 text-[var(--coord)]" />
                  <span className="text-[12.5px] font-medium text-[var(--ink)]">Choose folder…</span>
                  <span className="text-[11px] text-[var(--faint)]">A project folder on your computer</span>
                </button>
                <button type="button" onClick={chooseZip} className="flex flex-col items-center gap-2 rounded-lg border border-[var(--line)] bg-[var(--bg)] px-3 py-5 text-center transition-colors hover:border-[var(--coord)]">
                  <FileArchive className="h-6 w-6 text-[var(--coder)]" />
                  <span className="text-[12.5px] font-medium text-[var(--ink)]">Choose .zip…</span>
                  <span className="text-[11px] text-[var(--faint)]">e.g. a GitHub “Download ZIP”</span>
                </button>
              </div>
              <p className="mt-3 text-center text-[11px] text-[var(--faint)]">You can also drop a folder or .zip onto the Explorer.</p>
              {error && <p className="mt-3 text-[12px] text-[var(--conflict)]">{error}</p>}
            </>
          ) : result.files.length === 0 ? (
            <div className="py-6 text-center">
              <p className="text-[12.5px] text-[var(--ink)]">No source files found in “{result.name}”.</p>
              <p className="mt-1 text-[11.5px] text-[var(--faint)]">{describeSkipped(result.skipped)}</p>
              <button type="button" className="btn mt-4" onClick={() => setResult(null)}>Choose something else</button>
            </div>
          ) : (
            <>
              <div className="mb-3 flex items-baseline justify-between gap-2">
                <p className="text-[12.5px] text-[var(--ink)]"><span className="font-semibold">{result.name}</span> · {result.files.length} file{result.files.length === 1 ? '' : 's'}</p>
                <button type="button" className="text-[11.5px] text-[var(--muted)] hover:text-[var(--ink)] hover:underline" onClick={() => setResult(null)}>Change</button>
              </div>
              <ul className="mb-1 max-h-36 overflow-auto rounded-md border border-[var(--line)] bg-[var(--bg)] py-1 font-mono text-[11.5px]">
                {result.files.map(f => (
                  <li key={f.path} className="flex items-center gap-1.5 truncate px-2 py-0.5 text-[var(--muted)]">
                    <FileIcon name={f.path.split('/').pop()!} className="h-3 w-3 flex-none" /> {f.path}
                  </li>
                ))}
              </ul>
              {describeSkipped(result.skipped) && <p className="mb-3 text-[11px] text-[var(--faint)]">{describeSkipped(result.skipped)}</p>}

              <div className="mb-3 grid grid-cols-3 gap-1.5" role="radiogroup" aria-label="How to import">
                {modes.map(m => (
                  <button
                    key={m.id}
                    type="button"
                    role="radio"
                    aria-checked={mode === m.id}
                    onClick={() => setMode(m.id)}
                    className={`rounded-lg border p-2 text-left transition-colors ${mode === m.id ? 'border-[var(--coord)] bg-[var(--coord)]/10' : 'border-[var(--line)] hover:border-[var(--faint)]'}`}
                  >
                    <m.icon className={`mb-1 h-4 w-4 ${mode === m.id ? 'text-[var(--coord)]' : 'text-[var(--muted)]'}`} />
                    <span className="block text-[11.5px] font-medium text-[var(--ink)]">{m.label}</span>
                    <span className="block text-[10.5px] leading-snug text-[var(--faint)]">{m.detail}</span>
                  </button>
                ))}
              </div>

              {mode === 'folder' && (
                <label className="mb-3 flex items-center gap-2 rounded border border-[var(--line)] bg-[var(--bg)] px-2 focus-within:border-[var(--coord)]">
                  <span className="text-[11px] text-[var(--faint)]">Folder</span>
                  <input value={folder} onChange={e => setFolder(e.target.value.replace(/[^\w./-]+/g, '-'))} className="min-w-0 flex-1 bg-transparent py-1 font-mono text-[12px] text-[var(--ink)] outline-none" aria-label="Subfolder name" />
                </label>
              )}

              {summary && (summary.removed.length > 0 || summary.overwritten.length > 0 || summary.lockedHit.length > 0) && (
                <div className="flex gap-2 rounded-md border border-[#d29922]/40 bg-[#d29922]/10 p-2 text-[11.5px] leading-snug text-[var(--ink)]">
                  <TriangleAlert className="mt-0.5 h-3.5 w-3.5 flex-none text-[#d29922]" />
                  <span>
                    {summary.removed.length > 0 && <>Removes {summary.removed.length} current file{summary.removed.length === 1 ? '' : 's'}. </>}
                    {summary.overwritten.length > 0 && <>Overwrites {summary.overwritten.length} file{summary.overwritten.length === 1 ? '' : 's'}. </>}
                    {summary.lockedHit.length > 0 && <>{summary.lockedHit.length} locked file{summary.lockedHit.length === 1 ? ' is' : 's are'} skipped. </>}
                  </span>
                </div>
              )}
              {error && <p className="mt-3 text-[12px] text-[var(--conflict)]">{error}</p>}
            </>
          )}
        </div>

        {result && result.files.length > 0 && !loading && (
          <div className="flex justify-end gap-2 border-t border-[var(--line)] px-4 py-3">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="button" className="btn primary" onClick={() => void confirm()} disabled={loading || (mode === 'folder' && !folder.trim())}>
              Import {result.files.length} file{result.files.length === 1 ? '' : 's'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
