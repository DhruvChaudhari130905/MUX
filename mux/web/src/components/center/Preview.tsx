'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Sparkles, MessageSquareText, ExternalLink, Maximize2, Minimize2, Play, RotateCw, Wrench } from 'lucide-react';
import type { PlanItem } from '@/types';
import { api } from '@/lib/api';
import { getDemoPreview, isDemoMode, type DemoPreview } from '@/lib/demo';
import {
  onPreviewState, onServerReady, previewRoomId, pushRoomFiles, runningServers, startPreview, stopPreview, updateRoomFs,
  webContainersSupported, type PreviewState, type RoomFs,
} from '@/lib/runtime';
import { projectKind, viteSetupFiles } from '@/lib/projectKind';
import { buildStaticPage, findEntryPage, htmlPages, resolveRef } from '@/lib/staticPreview';
import type { FileMap } from './FileTree';
import { PlanPreview } from './PlanPreview';

interface PreviewProps {
  files: FileMap;
  // Reads and writes the room's files (shared with the terminal), so the app runs on the live files
  fs: RoomFs;
  roomId: string;
  title: string;
  description: string;
  plan: PlanItem[];
}

// Preview tab. Sample projects in demo mode render their app. Other rooms show, in order: a Node app
// (package.json with a dev/start script) installed and run in the browser's WebContainer, a dev server
// started by hand in the Terminal tab, an offer to add a Vite setup to React/TypeScript sources, the
// room's own HTML rendered directly, or a blank-project canvas until there's something to show.
export function Preview({ files, fs, roomId, title, description, plan }: PreviewProps) {
  const [buildStatus, setBuildStatus] = useState<'building' | 'ready'>('building');
  const [spec, setSpec] = useState<DemoPreview | null>(null);
  const [serverUrl, setServerUrl] = useState<string | null>(null);

  useEffect(() => {
    setSpec(isDemoMode() ? getDemoPreview(roomId) : null);
    setBuildStatus('building');
    const timer = setTimeout(() => setBuildStatus('ready'), 900);
    return () => clearTimeout(timer);
  }, [roomId]);

  useEffect(() => {
    const pick = () => {
      const servers = runningServers(roomId);
      const preferred = servers.find(([port]) => port === 5173 || port === 3000) ?? servers[0];
      setServerUrl(preferred ? preferred[1] : null);
    };
    pick();
    return onServerReady(pick);
  }, [roomId]);

  const entryPage = useMemo(() => findEntryPage(files), [files]);
  const project = useMemo(() => projectKind(files), [files]);
  const hasDraftPlan = plan.some(item => item.status === 'draft');

  if (hasDraftPlan) return <PlanPreview plan={plan} title={title} description={description} />;

  if (buildStatus === 'building') {
    return (
      <div className="preview flex min-h-[400px] items-center justify-center">
        <div className="text-center">
          <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-b-2 border-[var(--coord)]" />
          <p className="text-[#8a7f74]">Starting preview…</p>
        </div>
      </div>
    );
  }

  if (spec) return <SampleApp spec={spec} />;
  if (project.kind === 'node') return <LiveApp fs={fs} roomId={roomId} script={project.script} packageJson={project.packageJson} />;
  if (serverUrl) return <ServerPreview url={serverUrl} />;
  if (project.kind === 'needs-setup') return <SetupNeeded files={files} fs={fs} title={title} />;
  if (entryPage) return <StaticPreview files={files} entryPage={entryPage} roomId={roomId} />;
  return <BlankProject title={title} description={description} plan={plan} fileCount={files.size} />;
}

// Fills the screen and back. The frame itself goes fullscreen (not a copy), so the running app keeps its state;
// Esc also leaves fullscreen.
function useFullscreen<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [isFull, setIsFull] = useState(false);
  const [supported, setSupported] = useState(false);
  useEffect(() => {
    setSupported(!!document.fullscreenEnabled);
    const onChange = () => setIsFull(!!ref.current && document.fullscreenElement === ref.current);
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);
  const toggle = () => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void ref.current?.requestFullscreen().catch(() => undefined);
  };
  return { ref, isFull, supported, toggle };
}

function PreviewFrame({ address, openUrl, onRefresh, children }: { address: React.ReactNode; openUrl?: string; onRefresh?: () => void; children: React.ReactNode }) {
  const full = useFullscreen<HTMLDivElement>();
  return (
    <div ref={full.ref} className="preview-frame item-in flex min-h-full flex-col overflow-hidden rounded-lg border border-[var(--line)] bg-[var(--panel)]">
      <div className="flex items-center gap-2 border-b border-[var(--line)] px-4 py-2.5">
        <span className="h-2.5 w-2.5 rounded-full bg-[#f85149]/60" />
        <span className="h-2.5 w-2.5 rounded-full bg-[#d29922]/60" />
        <span className="h-2.5 w-2.5 rounded-full bg-[#3fb950]/60" />
        <span className="mono ml-3 min-w-0 truncate rounded bg-[var(--bg)] px-2 py-0.5 text-[11px] text-[var(--faint)]">{address}</span>
        <span className="ml-auto flex items-center gap-3">
          {onRefresh && (
            <button
              className="text-[var(--faint)] hover:text-[var(--ink)]"
              onClick={onRefresh}
              type="button"
              aria-label="Refresh preview"
              title="Refresh"
            >
              <RotateCw className="h-3.5 w-3.5" />
            </button>
          )}
          {openUrl && (
            <a className="text-[var(--faint)] hover:text-[var(--ink)]" href={openUrl} target="_blank" rel="noopener noreferrer" aria-label="Open preview in a new tab" title="Open in a new tab">
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          )}
          {full.supported && (
            <button
              className="text-[var(--faint)] hover:text-[var(--ink)]"
              onClick={full.toggle}
              type="button"
              aria-pressed={full.isFull}
              aria-label={full.isFull ? 'Exit fullscreen preview' : 'Fullscreen preview'}
              title={full.isFull ? 'Exit fullscreen (Esc)' : 'Fullscreen'}
            >
              {full.isFull ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
            </button>
          )}
        </span>
      </div>
      {children}
    </div>
  );
}

// Installs and runs the room's app in the WebContainer, showing npm's progress until the server is up
function LiveApp({ fs, roomId, script, packageJson }: { fs: RoomFs; roomId: string; script: string; packageJson: string }) {
  const [state, setState] = useState<PreviewState>({ phase: 'idle', log: '' });
  const support = useMemo(() => webContainersSupported(), []);
  const logRef = useRef<HTMLPreElement>(null);
  const installedFor = useRef<string | null>(null);

  useEffect(() => onPreviewState(s => setState(previewRoomId() === roomId ? s : { phase: 'idle', log: '' })), [roomId]);

  // Start on first view; reinstall when package.json changes (new dependencies or scripts)
  useEffect(() => {
    if (!support.ok) return;
    if (installedFor.current === packageJson && previewRoomId() === roomId) return;
    const restart = installedFor.current !== null;
    installedFor.current = packageJson;
    if (restart) stopPreview();
    void startPreview(roomId, fs, script);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fs changes every render; the latest is pushed below
  }, [support.ok, roomId, script, packageJson]);

  // Room edits (the coder's, a teammate's) reach the running app, which hot-reloads them
  useEffect(() => {
    if (!support.ok || previewRoomId() !== roomId) return;
    updateRoomFs(roomId, fs);
    void pushRoomFiles(roomId, fs.files).catch(() => undefined);
  });

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [state.log]);

  if (!support.ok) {
    return (
      <PreviewFrame address="preview unavailable">
        <p className="p-6 text-sm text-[var(--muted)]">
          This app needs to run in the browser, which isn&apos;t possible here: {support.reason}. Try Chrome or Edge on a desktop.
        </p>
      </PreviewFrame>
    );
  }

  if (state.phase === 'ready' && state.url) return <ServerPreview url={state.url} />;

  const restart = () => {
    stopPreview();
    void startPreview(roomId, fs, script);
  };
  const step = state.phase === 'installing' ? 'Installing packages (npm install)…'
    : state.phase === 'starting' ? `Starting the app (npm run ${script})…`
      : state.phase === 'error' ? 'The app stopped' : 'Preparing…';

  return (
    <PreviewFrame address={`npm run ${script}`} onRefresh={restart}>
      <div className="flex min-h-[400px] flex-1 flex-col gap-3 p-4">
        <div className="flex items-center gap-2 text-sm text-[var(--ink)]">
          {state.phase === 'error'
            ? <span className="h-2.5 w-2.5 rounded-full bg-[var(--conflict)]" />
            : <span className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--coord)] border-t-transparent" />}
          <span>{step}</span>
          {state.phase === 'error' && (
            <button className="btn ml-auto flex items-center gap-1.5" onClick={restart} type="button">
              <RotateCw className="h-3.5 w-3.5" /> Try again
            </button>
          )}
        </div>
        {state.error && <p className="text-sm text-[var(--conflict)]">{state.error}</p>}
        <pre ref={logRef} className="mono flex-1 overflow-auto whitespace-pre-wrap rounded bg-[var(--bg)] p-3 text-[11px] leading-relaxed text-[var(--muted)]">
          {state.log || 'Waiting for npm…'}
        </pre>
        {state.phase === 'error' && (
          <p className="text-xs text-[var(--faint)]">Ask the agent in the feed to fix the error above, then try again.</p>
        )}
      </div>
    </PreviewFrame>
  );
}

// React/TypeScript sources with nothing to run them: offer the missing Vite files (nothing is overwritten)
function SetupNeeded({ files, fs, title }: { files: FileMap; fs: RoomFs; title: string }) {
  const [adding, setAdding] = useState(false);
  const missing = useMemo(() => viteSetupFiles(files, title || 'mux-app'), [files, title]);

  const add = async () => {
    setAdding(true);
    try {
      for (const [path, content] of Array.from(missing)) await fs.write(path, content);
    } finally {
      setAdding(false);
    }
  };

  return (
    <PreviewFrame address="not runnable yet">
      <div className="flex min-h-[400px] flex-1 flex-col items-center justify-center gap-4 p-8 text-center">
        <Wrench className="h-8 w-8 text-[var(--coder)]" />
        <div>
          <h3 className="mb-1 text-base font-semibold text-[var(--ink)]">This React project can&apos;t run yet</h3>
          <p className="mx-auto max-w-sm text-sm text-[var(--muted)]">
            It has .tsx files but no package.json to install and start it. Add a Vite setup and the preview will run it here.
          </p>
        </div>
        <button className="btn primary flex items-center gap-1.5" onClick={add} disabled={adding} type="button">
          <Play className="h-4 w-4" />
          {adding ? 'Adding…' : 'Add Vite setup and run'}
        </button>
        <p className="mono text-[11px] text-[var(--faint)]">adds {Array.from(missing.keys()).join(', ')}</p>
      </div>
    </PreviewFrame>
  );
}

function ServerPreview({ url }: { url: string }) {
  // Remounting the iframe re-navigates it; a cross-origin frame can't be reloaded from here
  const [run, setRun] = useState(0);
  return (
    <PreviewFrame address={url} openUrl={url} onRefresh={() => setRun(n => n + 1)}>
      <iframe key={run} className="min-h-[400px] w-full flex-1 bg-white" src={url} title="App preview" allow="cross-origin-isolated" />
    </PreviewFrame>
  );
}

// The room's HTML rendered in a sandboxed frame (scripts run, but it can't reach the MUX page)
function StaticPreview({ files, entryPage, roomId }: { files: FileMap; entryPage: string; roomId: string }) {
  const [page, setPage] = useState(entryPage);
  // Every refresh remounts the frame, so scripts and CSS re-run from the current files
  const [run, setRun] = useState(0);
  const frame = useRef<HTMLIFrameElement>(null);
  // Errors the page throws, sent to the room a moment later in one report (the server turns them into a
  // fix task for the coder). Each error is sent once per version of the page.
  const pending = useRef<string[]>([]);
  const reported = useRef(new Set<string>());
  const flush = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => { setPage(entryPage); }, [entryPage]);

  const current = files.has(page) ? page : entryPage;
  const html = useMemo(() => buildStaticPage(files, current), [files, current]);

  useEffect(() => {
    reported.current.clear();
    pending.current = [];
  }, [html]);

  useEffect(() => () => { if (flush.current) clearTimeout(flush.current); }, []);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow) return;
      const error = (event.data as { muxPreviewError?: unknown })?.muxPreviewError;
      if (typeof error === 'string') {
        if (reported.current.has(error)) return;
        reported.current.add(error);
        pending.current.push(error);
        if (flush.current) clearTimeout(flush.current);
        flush.current = setTimeout(() => {
          const errors = pending.current.splice(0, 20);
          // Viewers can't report, and a room without a model can't fix: both are fine to drop
          if (errors.length) api.reportErrors(roomId, errors).catch(() => {});
        }, 2000);
        return;
      }
      const href = (event.data as { muxPreviewNavigate?: unknown })?.muxPreviewNavigate;
      if (typeof href !== 'string') return;
      const target = resolveRef(files, current, href) ?? resolveRef(files, current, `${href.replace(/\/$/, '')}/index.html`);
      if (target?.endsWith('.html')) setPage(target);
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [files, current, roomId]);

  // Every page is reachable from here, even when the site's own links don't lead to it
  const pages = htmlPages(files);
  const address = pages.length > 1 ? (
    <select
      className="mono max-w-full cursor-pointer bg-transparent text-[11px] text-[var(--faint)] outline-none"
      value={current}
      onChange={e => setPage(e.target.value)}
      aria-label="Preview page"
    >
      {pages.map(p => <option key={p} value={p}>{p}</option>)}
    </select>
  ) : current;

  return (
    <PreviewFrame
      address={address}
      onRefresh={() => {
        // Fresh run of the page: clear what was reported last time so the new run reports cleanly
        pending.current = [];
        reported.current.clear();
        setRun(n => n + 1);
      }}
    >
      <iframe
        ref={frame}
        key={run}
        className="min-h-[400px] w-full flex-1 bg-white"
        srcDoc={html}
        sandbox="allow-scripts allow-forms allow-modals allow-popups"
        title="App preview"
      />
    </PreviewFrame>
  );
}

function SampleApp({ spec }: { spec: DemoPreview }) {
  const [clicked, setClicked] = useState<Set<string>>(new Set());
  const toggle = (t: string) =>
    setClicked(prev => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });

  return (
    <div className="preview item-in" style={{ background: spec.background, color: spec.ink }}>
      <div className="pv-nav" style={{ borderColor: `${spec.ink}14` }}>
        <span className="pv-logo">{spec.brand}</span>
        <div className="pv-links" style={{ color: `${spec.ink}aa` }}>
          {spec.links.map(l => <span key={l}>{l}</span>)}
        </div>
      </div>
      <div className="pv-hero">
        <h2>{spec.headline}</h2>
        <p style={{ color: `${spec.ink}aa` }}>{spec.tagline}</p>
      </div>
      <div className="pv-grid">
        {spec.cards.map(c => {
          const on = clicked.has(c.title);
          return (
            <div key={c.title} className="pv-card" style={{ borderColor: `${spec.ink}14` }}>
              <span className="t">{c.title}</span>
              <span className="m" style={{ color: `${spec.ink}88` }}>{c.meta}</span>
              <button
                className="pv-btn transition-transform active:scale-95"
                type="button"
                onClick={() => toggle(c.title)}
                aria-pressed={on}
                style={{ background: spec.accent, opacity: on ? 0.8 : 1 }}
              >
                {on ? `${c.action} ✓` : c.action}
              </button>
            </div>
          );
        })}
        <div className="pv-card skeleton whitespace-pre-line">{spec.building}</div>
      </div>
      <div className="built">
        <span><b>●</b> Last build passed</span>
        <span className="mono">snapshot 7f3a…e21</span>
        <span>Sample project preview</span>
      </div>
    </div>
  );
}

function BlankProject({ title, description, plan, fileCount }: { title: string; description: string; plan: PlanItem[]; fileCount: number }) {
  const done = plan.filter(p => p.status === 'done').length;
  return (
    <div className="item-in flex min-h-full flex-col overflow-hidden rounded-lg border border-[var(--line)] bg-[var(--panel)]">
      <div className="flex items-center gap-2 border-b border-[var(--line)] px-4 py-2.5">
        <span className="h-2.5 w-2.5 rounded-full bg-[#f85149]/60" />
        <span className="h-2.5 w-2.5 rounded-full bg-[#d29922]/60" />
        <span className="h-2.5 w-2.5 rounded-full bg-[#3fb950]/60" />
        <span className="mono ml-3 rounded bg-[var(--bg)] px-2 py-0.5 text-[11px] text-[var(--faint)]">localhost:5173</span>
      </div>
      <div className="relative flex flex-1 flex-col items-center justify-center gap-4 p-8 text-center">
        <div
          className="pointer-events-none absolute inset-0 opacity-[0.35]"
          style={{ backgroundImage: 'radial-gradient(var(--line) 1px, transparent 1px)', backgroundSize: '18px 18px' }}
        />
        <div className="relative grid h-16 w-16 place-items-center rounded-2xl bg-gradient-to-br from-[var(--coord)]/25 to-[var(--coder)]/25 ring-1 ring-white/10">
          <Sparkles className="h-7 w-7 text-[var(--coder)]" />
        </div>
        <div className="relative">
          <h3 className="mb-1 text-lg font-semibold text-[var(--ink)]">{title || 'Blank project'}</h3>
          <p className="mx-auto max-w-sm text-sm text-[var(--muted)]">
            {description && description.trim().toLowerCase() !== title.replace(/…$/, '').trim().toLowerCase()
              ? `“${description}”`
              : 'Nothing built yet.'}
          </p>
        </div>
        <div className="relative flex items-center gap-2 rounded-full bg-[var(--bg)] px-3 py-1.5 text-xs text-[var(--muted)] ring-1 ring-[var(--line)]">
          <MessageSquareText className="h-3.5 w-3.5 text-[var(--coord)]" />
          {plan.length
            ? `${done} of ${plan.length} plan items built · the preview appears after the first build`
            : 'Describe what you want in the feed — the preview appears after the first build'}
        </div>
        <p className="mono relative text-[11px] text-[var(--faint)]">{fileCount} starter files · edit them in the Code tab</p>
      </div>
    </div>
  );
}
