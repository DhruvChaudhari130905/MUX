'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  X, Save, SaveAll, Download, FilePlus, FolderPlus, Upload, Lock, ChevronRight, Check, Code2, WandSparkles, WrapText,
  Play, Square, FolderInput, LayoutTemplate, Map as MapIcon, ZoomIn, ZoomOut, SquareTerminal, Command, Keyboard, CircleAlert, TriangleAlert,
} from 'lucide-react';
import type { PlanItem } from '@/types';
import { canFormat, formatCode } from '@/lib/formatter';
import { FileTree, FileIcon, downloadFile, readUploads, type CreateRequest, type UploadedFile } from './FileTree';
import { CodeEditor, languageFor, type EditorSettings, type EditorShortcut } from './CodeEditor';
import { isBinaryContent, isImagePath } from '@/lib/binaryFiles';
import { Preview } from './Preview';
import { BottomPanel, type Problem, type PanelView } from './BottomPanel';
import type { RunState } from './RunOutput';
import { canRun, remoteFallback, runRemote, runnerFor, type Runner } from '@/lib/codeRunner';
import { notify } from '@/lib/notifications';
import { QuickOpen, type PaletteCommand } from './QuickOpen';
import { OpenInVsCode } from './OpenInVsCode';
import { ImportProjectDialog, type ImportMode } from './ImportProjectDialog';
import { importFromDrop, type ImportResult } from '@/lib/projectImport';
import { starterProjectFiles } from '@/lib/starterProject';
import { ActivityBar, SearchView, SourceControlView, ExtensionsView, diffAgainst, type SideView, type Change, type Commit } from './SideViews';
import type { TerminalFs } from './Terminal';

interface CenterTabsProps {
  roomId: string;
  roomTitle: string;
  roomDescription: string;
  plan: PlanItem[];
  checkpointCount: number;
  activeTab: 'preview' | 'code';
  onTabChange: (tab: 'preview' | 'code') => void;
  files: Map<string, { content: string }>;
  activeFile: string | null;
  onFileSelect: (path: string | null) => void;
  onFileEdit: (path: string, content: string, baseVersion: number) => Promise<void> | void;
  onCreateFile: (path: string, content?: string, open?: boolean) => void;
  onDeleteFile: (path: string, isDirectory: boolean) => void;
  onRenameFile: (from: string, to: string, isDirectory: boolean) => void;
  onUploadFiles: (files: UploadedFile[]) => void;
  fileVersions: Map<string, number>;
  lockedFiles: Set<string>;
  lockingUser: Map<string, string>;
  // Outside your folders: saving there sends the change for approval (lib/fileAccess.ts)
  needsApproval?: (path: string, isDirectory: boolean) => boolean;
}

type Settings = EditorSettings;

const SETTINGS_KEY = 'mux_editor_settings';
const DEFAULT_SETTINGS: Settings = { wordWrap: false, minimap: false, fontSize: 13 };

const SHORTCUTS: [string, string][] = [
  ['⌘/Ctrl S', 'Save file'],
  ['F5', 'Run file'],
  ['⇧ ⌥ F', 'Format document'],
  ['⌘/Ctrl P', 'Quick open file'],
  ['⌘/Ctrl ⇧ P', 'Command palette'],
  ['Ctrl `', 'Toggle terminal'],
  ['⌘/Ctrl F / H', 'Find / replace'],
  ['⌘/Ctrl G', 'Go to line'],
  ['⌘/Ctrl D', 'Select next match'],
  ['⌥ ↑ / ↓', 'Move line up / down'],
  ['⌘/Ctrl /', 'Toggle comment'],
  ['F2', 'Rename symbol'],
];

interface MarkerLike {
  severity: number;
  resource: { path: string };
  startLineNumber: number;
  startColumn: number;
  message: string;
}

type MonacoEditor = Parameters<React.ComponentProps<typeof CodeEditor>['onReady']>[0];
type Monaco = Parameters<React.ComponentProps<typeof CodeEditor>['onReady']>[1];

export function CenterTabs({
  roomId,
  roomTitle,
  roomDescription,
  plan,
  checkpointCount,
  activeTab,
  onTabChange,
  files,
  activeFile,
  onFileSelect,
  onFileEdit,
  onCreateFile,
  onDeleteFile,
  onRenameFile,
  onUploadFiles,
  fileVersions,
  lockedFiles,
  lockingUser,
  needsApproval,
}: CenterTabsProps) {
  const [openTabs, setOpenTabs] = useState<string[]>(activeFile ? [activeFile] : []);
  const [drafts, setDrafts] = useState<Map<string, string>>(new Map()); // unsaved edits per file
  const [cursor, setCursor] = useState({ line: 1, column: 1 });
  const [toast, setToast] = useState<{ text: string; tone: 'ok' | 'err' } | null>(null);
  const [createRequest, setCreateRequest] = useState<CreateRequest | null>(null);
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [panel, setPanel] = useState<{ open: boolean; view: PanelView; height: number }>({ open: false, view: 'terminal', height: 240 });
  const [palette, setPalette] = useState<'files' | 'commands' | null>(null);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [formatting, setFormatting] = useState(false);
  const [problems, setProblems] = useState<Problem[]>([]);
  const [sideView, setSideView] = useState<SideView | null>('explorer');
  // Source control: file contents at the last commit (taken once the room's files have loaded)
  // The editor mounts once the room's files have loaded (and remounts per room), so they are the starting point
  const [baseline, setBaseline] = useState<Map<string, string> | null>(() => new Map(Array.from(files, ([p, f]) => [p, f.content])));
  const [commits, setCommits] = useState<Commit[]>([]);
  const [showVsCode, setShowVsCode] = useState(false);
  // Import project: null = closed; a promise when a drop is already being read
  const [importing, setImporting] = useState<{ initial: Promise<ImportResult> | null } | null>(null);
  // Run file
  const [run, setRun] = useState<RunState | null>(null);
  const [stdinByFile, setStdinByFile] = useState<Map<string, string>>(new Map());
  const [shellRequest, setShellRequest] = useState<{ id: number; command: string; path: string } | null>(null);
  const runAbort = useRef<AbortController | null>(null);
  const uploadRef = useRef<HTMLInputElement>(null);
  const editorRef = useRef<MonacoEditor | null>(null);
  const monacoRef = useRef<Monaco | null>(null);
  const pendingJump = useRef<{ line: number; column: number } | null>(null);
  const markersHooked = useRef(false);

  // Editor settings persist per browser
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || 'null');
      if (saved) setSettings({ ...DEFAULT_SETTINGS, ...saved });
    } catch {
      // ignore bad or missing storage
    }
  }, []);
  const updateSettings = (patch: Partial<Settings>) =>
    setSettings(prev => {
      const next = { ...prev, ...patch };
      try {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(next));
      } catch {
        // storage unavailable
      }
      return next;
    });

  const flash = (text: string, tone: 'ok' | 'err' = 'ok') => {
    setToast({ text, tone });
    setTimeout(() => setToast(t => (t?.text === text ? null : t)), 2600);
  };

  // Opening a file adds a tab; files that disappear (deleted/renamed) lose their tab and draft
  useEffect(() => {
    setOpenTabs(prev => {
      let next = prev.filter(p => files.has(p));
      if (activeFile && files.has(activeFile) && !next.includes(activeFile)) next = [...next, activeFile];
      return next.length === prev.length && next.every((p, i) => p === prev[i]) ? prev : next;
    });
    setDrafts(prev => {
      if (Array.from(prev.keys()).every(p => files.has(p))) return prev;
      return new Map(Array.from(prev).filter(([p]) => files.has(p)));
    });
    if (activeFile && !files.has(activeFile)) onFileSelect(null);
  }, [files, activeFile, onFileSelect]);

  const changes = useMemo(() => (baseline ? diffAgainst(baseline, files) : []), [baseline, files]);

  const commitChanges = (message: string) => {
    setCommits(prev => [{ id: Math.random().toString(16).slice(2, 9), message, files: changes.length, at: new Date() }, ...prev]);
    setBaseline(new Map(Array.from(files, ([p, f]) => [p, f.content])));
    flash(`Committed ${changes.length} change${changes.length === 1 ? '' : 's'}`);
  };

  const discardChange = async (c: Change) => {
    const original = baseline?.get(c.path) ?? '';
    if (c.status === 'A') onDeleteFile(c.path, false);
    else if (c.status === 'D') onCreateFile(c.path, original, false);
    else await onFileEdit(c.path, original, fileVersions.get(c.path) || 0);
    setDrafts(prev => {
      if (!prev.has(c.path)) return prev;
      const next = new Map(prev);
      next.delete(c.path);
      return next;
    });
  };

  // Clicking the open view's icon collapses the side bar, like VS Code
  const toggleSideView = (view: SideView) => setSideView(v => (v === view ? null : view));

  const dirtyFiles = useMemo(
    () => new Set(Array.from(drafts).filter(([p, d]) => d !== files.get(p)?.content).map(([p]) => p)),
    [drafts, files],
  );

  // Unsaved editor changes would be lost: let the browser ask before the tab closes or reloads
  useEffect(() => {
    if (!dirtyFiles.size) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirtyFiles.size]);

  const contentOf = useCallback((path: string) => drafts.get(path) ?? files.get(path)?.content ?? '', [drafts, files]);

  const setDraft = (path: string, value: string) =>
    setDrafts(prev => {
      const next = new Map(prev);
      if (value === files.get(path)?.content) next.delete(path);
      else next.set(path, value);
      return next;
    });

  const closeTab = (path: string) => {
    if (dirtyFiles.has(path) && !window.confirm(`Discard unsaved changes to ${path.split('/').pop()}?`)) return;
    setDrafts(prev => {
      const next = new Map(prev);
      next.delete(path);
      return next;
    });
    const i = openTabs.indexOf(path);
    const next = openTabs.filter(p => p !== path);
    setOpenTabs(next);
    if (path === activeFile) onFileSelect(next[Math.min(i, next.length - 1)] ?? null);
  };

  const save = async (path: string) => {
    const content = drafts.get(path);
    if (content === undefined || lockedFiles.has(path)) return;
    try {
      await onFileEdit(path, content, fileVersions.get(path) || 0);
    } catch {
      return; // save failed; keep the draft
    }
    setDrafts(prev => {
      const next = new Map(prev);
      next.delete(path);
      return next;
    });
    flash(`Saved ${path.split('/').pop()}`);
  };

  const saveAll = async () => {
    const paths = Array.from(dirtyFiles);
    for (const p of paths) await save(p);
    if (paths.length > 1) flash(`Saved ${paths.length} files`);
  };

  const formatActive = async () => {
    if (!activeFile) return;
    if (!canFormat(activeFile)) return flash(`No formatter for .${activeFile.split('.').pop()} files`, 'err');
    setFormatting(true);
    try {
      const before = contentOf(activeFile);
      const after = await formatCode(activeFile, before);
      if (after === before) flash('Already formatted');
      else {
        setDraft(activeFile, after);
        flash('Formatted');
      }
    } catch (e) {
      flash(`Format failed: ${(e as Error).message.split('\n')[0]}`, 'err');
    } finally {
      setFormatting(false);
    }
  };

  const applyImport = (incoming: { path: string; content: string }[], mode: ImportMode) => {
    setImporting(null);
    if (mode === 'replace') {
      const keep = new Set(incoming.map(f => f.path));
      Array.from(files.keys()).filter(p => !keep.has(p) && !lockedFiles.has(p)).forEach(p => onDeleteFile(p, false));
      setOpenTabs([]);
      setDrafts(new Map());
    }
    onUploadFiles(incoming);
    // Open something useful rather than whichever file happened to be last
    const entry = ['README.md', 'readme.md', 'package.json', 'src/App.tsx', 'src/main.tsx', 'index.html', 'main.py']
      .map(name => incoming.find(f => f.path === name || f.path.endsWith(`/${name}`)))
      .find(Boolean);
    if (entry) onFileSelect(entry.path);
    setSideView('explorer');
    flash(`Imported ${incoming.length} file${incoming.length === 1 ? '' : 's'}`);
  };

  const runRemotely = async (path: string, runner: Runner) => {
    runAbort.current?.abort();
    const abort = new AbortController();
    runAbort.current = abort;
    setPanel(p => ({ ...p, open: true, view: 'output' }));
    setRun({ path, language: runner.language, status: 'running' });
    const name = path.split('/').pop();
    try {
      const result = await runRemote(path, runner.language, filesWithDrafts(), stdinByFile.get(path) ?? '', abort.signal);
      if (abort.signal.aborted) return;
      setRun({ path, language: runner.language, status: 'done', result });
      // Only worth a toast/desktop notification if the user has looked away
      if (document.hidden) {
        notify({ category: 'terminal', tone: result.ok ? 'ok' : 'err', title: `${name} ${result.ok ? 'finished' : `exited with ${result.exitCode}`}`, body: (result.stdout || result.stderr || result.compileOutput).slice(0, 140) });
      }
    } catch (e) {
      if (abort.signal.aborted) return;
      setRun({ path, language: runner.language, status: 'error', error: (e as Error).message });
    }
  };

  // Unsaved edits run too: the file being run is what's on screen
  const filesWithDrafts = () => {
    const merged = new Map(files);
    drafts.forEach((content, path) => merged.set(path, { content }));
    return merged;
  };

  const runFile = async (path = activeFile) => {
    if (!path) return;
    const runner = runnerFor(path);
    if (!runner) return flash(`Can't run .${path.split('.').pop()} files`, 'err');
    if (runner.kind === 'remote') return runRemotely(path, runner);
    // JS/TS run in the real shell: save first so the terminal sees the latest code
    if (dirtyFiles.has(path)) await save(path);
    setPanel(p => ({ ...p, open: true, view: 'terminal' }));
    setShellRequest({ id: Date.now(), command: runner.command, path });
  };

  const stopRun = () => {
    runAbort.current?.abort();
    setRun(r => (r ? { ...r, status: 'error', error: 'Stopped' } : r));
  };

  const togglePanel = (view?: PanelView) =>
    setPanel(p => (view && p.open && p.view !== view ? { ...p, view } : { ...p, open: !p.open, view: view ?? p.view }));

  const requestCreate = (kind: 'file' | 'folder') => {
    setSideView('explorer');
    setCreateRequest({ kind, nonce: Date.now() });
  };

  const handleUploadPick = async (list: FileList) => {
    const target = activeFile ? activeFile.split('/').slice(0, -1).join('/') : '';
    const { files: uploaded } = await readUploads(list, target);
    if (uploaded.length) onUploadFiles(uploaded);
  };

  // Jump to a line (from Problems), opening the file first if needed
  const applyJump = () => {
    const ed = editorRef.current;
    const j = pendingJump.current;
    if (!ed || !j) return;
    pendingJump.current = null;
    ed.setPosition({ lineNumber: j.line, column: j.column });
    ed.revealLineInCenter(j.line);
    ed.focus();
  };
  const jumpTo = (p: { path: string; line: number; column: number }) => {
    pendingJump.current = { line: p.line, column: p.column };
    if (p.path === activeFile) applyJump();
    else onFileSelect(p.path);
  };

  const handleReady = (editor: MonacoEditor, monaco: Monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;
    applyJump();
    if (markersHooked.current) return;
    markersHooked.current = true;
    const collect = () => {
      const list: Problem[] = monaco.editor
        .getModelMarkers({})
        .filter((m: MarkerLike) => m.severity >= monaco.MarkerSeverity.Warning)
        .map((m: MarkerLike) => ({
          path: m.resource.path.replace(/^\//, ''),
          line: m.startLineNumber,
          column: m.startColumn,
          message: m.message,
          severity: m.severity === monaco.MarkerSeverity.Error ? ('error' as const) : ('warning' as const),
        }))
        .sort((a: Problem, b: Problem) => (a.severity === b.severity ? a.path.localeCompare(b.path) || a.line - b.line : a.severity === 'error' ? -1 : 1));
      setProblems(list);
    };
    monaco.editor.onDidChangeMarkers(collect);
    collect();
  };
  useEffect(() => {
    // Drop problems for files that no longer exist
    setProblems(prev => (prev.every(p => files.has(p.path)) ? prev : prev.filter(p => files.has(p.path))));
  }, [files]);

  const onShortcut = (action: EditorShortcut) => {
    if (action === 'save' && activeFile) save(activeFile);
    if (action === 'format') formatActive();
    if (action === 'quickOpen') setPalette('files');
    if (action === 'palette') setPalette('commands');
    if (action === 'toggleTerminal') togglePanel('terminal');
    if (action === 'run') void runFile();
  };
  const shortcutRef = useRef(onShortcut);
  shortcutRef.current = onShortcut;

  // The same shortcuts when focus is outside Monaco (explorer, terminal, tabs)
  useEffect(() => {
    if (activeTab !== 'code') return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest?.('.monaco-editor')) return;
      const mod = e.metaKey || e.ctrlKey;
      let action: EditorShortcut | null = null;
      if (mod && e.shiftKey && e.key.toLowerCase() === 'p') action = 'palette';
      else if (mod && !e.shiftKey && e.key.toLowerCase() === 'p') action = 'quickOpen';
      else if (e.ctrlKey && e.key === '`') action = 'toggleTerminal';
      else if (mod && e.key.toLowerCase() === 's') action = 'save';
      else if (e.shiftKey && e.altKey && e.code === 'KeyF') action = 'format';
      else if (e.key === 'F5') action = 'run';
      else if (mod && e.shiftKey) {
        const view = ({ KeyE: 'explorer', KeyF: 'search', KeyG: 'scm', KeyX: 'extensions' } as const)[e.code as 'KeyE'];
        if (view) {
          e.preventDefault();
          setSideView(view);
          return;
        }
      }
      if (action) {
        e.preventDefault();
        shortcutRef.current(action);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [activeTab]);

  // What the terminal can do to the room's files
  const terminalFs: TerminalFs = {
    files,
    write: async (path, content) => {
      if (files.has(path)) await onFileEdit(path, content, fileVersions.get(path) || 0);
      else onCreateFile(path, content, false); // terminal writes don't steal the editor
    },
    remove: paths => paths.forEach(p => onDeleteFile(p, false)),
    rename: (from, to) => onRenameFile(from, to, false),
    open: path => onFileSelect(path),
    problems: {
      errors: problems.filter(p => p.severity === 'error').length,
      warnings: problems.filter(p => p.severity === 'warning').length,
      list: problems,
    },
  };

  const commands: PaletteCommand[] = [
    { id: 'run', label: 'Run File', shortcut: 'F5', run: () => void runFile() },
    { id: 'format', label: 'Format Document', shortcut: '⇧⌥F', run: formatActive },
    { id: 'save', label: 'Save File', shortcut: '⌘S', run: () => { if (activeFile) save(activeFile); } },
    { id: 'saveAll', label: 'Save All', run: saveAll },
    { id: 'viewExplorer', label: 'View: Explorer', shortcut: '⌘⇧E', run: () => setSideView('explorer') },
    { id: 'viewSearch', label: 'View: Search in Files', shortcut: '⌘⇧F', run: () => setSideView('search') },
    { id: 'viewScm', label: 'View: Source Control', shortcut: '⌘⇧G', run: () => setSideView('scm') },
    { id: 'viewExtensions', label: 'View: Extensions', shortcut: '⌘⇧X', run: () => setSideView('extensions') },
    { id: 'importProject', label: 'Import Project from Computer…', run: () => setImporting({ initial: null }) },
    { id: 'openVsCode', label: 'Open in VS Code…', run: () => setShowVsCode(true) },
    { id: 'toggleSidebar', label: 'Toggle Side Bar', run: () => setSideView(v => (v ? null : 'explorer')) },
    { id: 'terminal', label: 'Toggle Terminal', shortcut: 'Ctrl `', run: () => togglePanel('terminal') },
    { id: 'problems', label: 'Show Problems', run: () => setPanel(p => ({ ...p, open: true, view: 'problems' })) },
    { id: 'wrap', label: `${settings.wordWrap ? 'Disable' : 'Enable'} Word Wrap`, run: () => updateSettings({ wordWrap: !settings.wordWrap }) },
    { id: 'minimap', label: `${settings.minimap ? 'Hide' : 'Show'} Minimap`, run: () => updateSettings({ minimap: !settings.minimap }) },
    { id: 'zoomIn', label: 'Increase Font Size', run: () => updateSettings({ fontSize: Math.min(22, settings.fontSize + 1) }) },
    { id: 'zoomOut', label: 'Decrease Font Size', run: () => updateSettings({ fontSize: Math.max(10, settings.fontSize - 1) }) },
    { id: 'newFile', label: 'New File…', run: () => requestCreate('file') },
    { id: 'newFolder', label: 'New Folder…', run: () => requestCreate('folder') },
    { id: 'upload', label: 'Upload Files…', run: () => uploadRef.current?.click() },
    { id: 'download', label: 'Download Current File', run: () => { if (activeFile) downloadFile(activeFile, contentOf(activeFile)); } },
    { id: 'closeAll', label: 'Close All Tabs', run: () => { setOpenTabs([]); setDrafts(new Map()); onFileSelect(null); } },
    { id: 'find', label: 'Find in File', shortcut: '⌘F', run: () => { editorRef.current?.getAction('actions.find')?.run(); } },
    { id: 'gotoLine', label: 'Go to Line…', shortcut: '⌘G', run: () => { editorRef.current?.getAction('editor.action.gotoLine')?.run(); } },
    { id: 'shortcuts', label: 'Keyboard Shortcuts', run: () => setShowShortcuts(true) },
  ];

  const activeContent = activeFile ? contentOf(activeFile) : '';
  const isLocked = !!activeFile && lockedFiles.has(activeFile);
  const isDirty = !!activeFile && dirtyFiles.has(activeFile);
  const heldSave = !!activeFile && !!needsApproval?.(activeFile, false);
  const lineCount = activeContent.split('\n').length;
  const errorCount = problems.filter(p => p.severity === 'error').length;
  const warningCount = problems.length - errorCount;
  const hasFile = !!activeFile && files.has(activeFile);

  const tool = (label: string, icon: React.ReactNode, onClick: () => void, on = false, disabled = false) => (
    <button type="button" className={`icon-btn ${on ? 'on' : ''}`} title={label} aria-label={label} aria-pressed={on} onClick={onClick} disabled={disabled}>
      {icon}
    </button>
  );

  return (
    <section className="col flex flex-col" aria-label="Preview and code">
      <div className="col-head">
        <div className="tabs" role="tablist">
          <button className="tab" role="tab" id="tabPreview" aria-selected={activeTab === 'preview'} onClick={() => onTabChange('preview')} type="button">
            Preview
          </button>
          <button className="tab" role="tab" id="tabCode" aria-selected={activeTab === 'code'} onClick={() => onTabChange('code')} type="button">
            Code
            {dirtyFiles.size > 0 && <span className="ml-1.5 inline-block h-1.5 w-1.5 rounded-full bg-[var(--coder)] align-middle" title={`${dirtyFiles.size} unsaved`} />}
          </button>
        </div>
        <span className="urlbar mono">{checkpointCount ? `webcontainer · checkpoint ${checkpointCount} + live edits` : 'webcontainer · no checkpoints yet'}</span>
      </div>

      <div className="stage" id="stagePreview" hidden={activeTab === 'code'}>
        <Preview files={files} fs={terminalFs} roomId={roomId} title={roomTitle} description={roomDescription} plan={plan} />
      </div>

      <div className="stage !p-2.5" id="stageCode" hidden={activeTab !== 'code'}>
        <div className={`code with-activity relative ${sideView ? '' : 'side-closed'}`}>
          <ActivityBar
            view={sideView}
            onSelect={toggleSideView}
            badges={{ scm: changes.length + Array.from(dirtyFiles).filter(p => !changes.some(c => c.path === p)).length }}
            onSettings={() => setPalette('commands')}
            onOpenVsCode={() => setShowVsCode(true)}
          />

          <div className="side-view" hidden={sideView !== 'explorer'}>
          <FileTree
            files={files}
            activeFile={activeFile}
            onFileSelect={onFileSelect}
            fileVersions={fileVersions}
            lockedFiles={lockedFiles}
            lockingUser={lockingUser}
            dirtyFiles={dirtyFiles}
            onCreateFile={path => onCreateFile(path)}
            onDelete={onDeleteFile}
            onRename={onRenameFile}
            onUpload={onUploadFiles}
            createRequest={createRequest}
            onImport={() => setImporting({ initial: null })}
            onImportDrop={items => setImporting({ initial: importFromDrop(items) })}
            needsApproval={needsApproval}
          />
          </div>
          {sideView === 'search' && (
            <div className="side-view">
              <SearchView paths={Array.from(files.keys()).sort()} contentOf={contentOf} onJump={jumpTo} />
            </div>
          )}
          {sideView === 'scm' && (
            <div className="side-view">
              <SourceControlView
                changes={changes}
                unsaved={Array.from(dirtyFiles).sort()}
                commits={commits}
                onOpen={path => onFileSelect(path)}
                onDiscard={discardChange}
                onCommit={commitChanges}
              />
            </div>
          )}
          {sideView === 'extensions' && (
            <div className="side-view">
              <ExtensionsView fs={terminalFs} onOpenVsCode={() => setShowVsCode(true)} />
            </div>
          )}

          <div className="code-editor">
            {/* Open file tabs */}
            <div className="editor-tabbar" role="tablist" aria-label="Open files">
              {openTabs.map(path => {
                const name = path.split('/').pop() || path;
                const active = path === activeFile;
                const dirty = dirtyFiles.has(path);
                return (
                  <div
                    key={path}
                    role="tab"
                    aria-selected={active}
                    tabIndex={0}
                    title={path}
                    onClick={() => onFileSelect(path)}
                    onKeyDown={e => { if (e.key === 'Enter') onFileSelect(path); }}
                    onAuxClick={e => { if (e.button === 1) closeTab(path); }}
                    className={`editor-tab item-in group ${active ? 'active' : ''}`}
                  >
                    <FileIcon name={name} className="h-3.5 w-3.5 flex-none" />
                    <span className="truncate">{name}</span>
                    <button
                      type="button"
                      aria-label={`Close ${name}`}
                      onClick={e => { e.stopPropagation(); closeTab(path); }}
                      className="relative grid h-4 w-4 flex-none place-items-center rounded hover:bg-white/10"
                    >
                      {dirty && <span className="absolute h-2 w-2 rounded-full bg-[var(--coder)] transition-opacity group-hover:opacity-0" />}
                      <X className={`h-3 w-3 transition-opacity ${dirty ? 'opacity-0 group-hover:opacity-100' : active ? 'opacity-70' : 'opacity-0 group-hover:opacity-70'}`} />
                    </button>
                  </div>
                );
              })}
              <div className="flex-1" />
              <div className="flex flex-none items-center gap-0.5 px-1.5">
                {dirtyFiles.size > 1 && tool(`Save all (${dirtyFiles.size})`, <SaveAll className="h-3.5 w-3.5" />, saveAll)}
                {tool('Quick open (⌘P)', <Command className="h-3.5 w-3.5" />, () => setPalette('files'))}
                {tool('Toggle terminal (Ctrl+`)', <SquareTerminal className="h-3.5 w-3.5" />, () => togglePanel('terminal'), panel.open && panel.view === 'terminal')}
                {tool('Keyboard shortcuts', <Keyboard className="h-3.5 w-3.5" />, () => setShowShortcuts(s => !s), showShortcuts)}
              </div>
            </div>

            {hasFile ? (
              <>
                {/* Breadcrumb + editor tools */}
                <div className="editor-header">
                  <div className="flex min-w-0 items-center gap-1 text-[var(--muted)]">
                    {activeFile!.split('/').map((part, i, arr) => (
                      <React.Fragment key={i}>
                        {i > 0 && <ChevronRight className="h-3 w-3 flex-none text-[var(--faint)]" />}
                        <span className={`truncate ${i === arr.length - 1 ? 'text-[var(--ink)]' : ''}`}>{part}</span>
                      </React.Fragment>
                    ))}
                    {toast && (
                      <span key={toast.text} className={`item-in ml-3 truncate font-sans text-[11px] ${toast.tone === 'ok' ? 'text-[var(--coder)]' : 'text-[var(--conflict)]'}`}>
                        {toast.text}
                      </span>
                    )}
                  </div>
                  <div className="editor-actions">
                    {isLocked && (
                      <span className="flex items-center gap-1 text-[11px] text-[var(--ask)]">
                        <Lock className="h-3 w-3" /> Locked by {lockingUser.get(activeFile!)}
                      </span>
                    )}
                    {canRun(activeFile!) && (
                      <button
                        type="button"
                        onClick={() => (run?.status === 'running' && run.path === activeFile ? stopRun() : void runFile())}
                        title={run?.status === 'running' && run.path === activeFile ? 'Stop' : `Run ${activeFile!.split('/').pop()} (F5)`}
                        className="mr-1 flex h-[26px] items-center gap-1 rounded-md bg-[var(--ok)]/15 px-2 font-sans text-[11.5px] font-medium text-[var(--ok)] transition-colors hover:bg-[var(--ok)]/25"
                      >
                        {run?.status === 'running' && run.path === activeFile ? <Square className="h-3 w-3 fill-current" /> : <Play className="h-3 w-3 fill-current" />}
                        {run?.status === 'running' && run.path === activeFile ? 'Stop' : 'Run'}
                      </button>
                    )}
                    {tool(
                      canFormat(activeFile!) ? 'Format document (⇧⌥F)' : 'No formatter for this file type',
                      formatting ? <span className="h-3.5 w-3.5 animate-spin rounded-full border-[1.5px] border-[var(--coder)] border-t-transparent" /> : <WandSparkles className="h-3.5 w-3.5" />,
                      formatActive,
                      false,
                      !canFormat(activeFile!) || isLocked || formatting,
                    )}
                    {tool('Word wrap', <WrapText className="h-3.5 w-3.5" />, () => updateSettings({ wordWrap: !settings.wordWrap }), settings.wordWrap)}
                    {tool('Minimap', <MapIcon className="h-3.5 w-3.5" />, () => updateSettings({ minimap: !settings.minimap }), settings.minimap)}
                    {tool('Smaller text', <ZoomOut className="h-3.5 w-3.5" />, () => updateSettings({ fontSize: Math.max(10, settings.fontSize - 1) }))}
                    <span className="w-5 text-center font-mono text-[10.5px] text-[var(--faint)]">{settings.fontSize}</span>
                    {tool('Larger text', <ZoomIn className="h-3.5 w-3.5" />, () => updateSettings({ fontSize: Math.min(22, settings.fontSize + 1) }))}
                    {tool('Download file', <Download className="h-3.5 w-3.5" />, () => downloadFile(activeFile!, activeContent))}
                    <button
                      type="button"
                      className={`btn ml-1 flex items-center gap-1.5 !py-1 text-xs transition-all ${isDirty && !isLocked ? 'primary' : ''}`}
                      onClick={() => save(activeFile!)}
                      disabled={isLocked || !isDirty}
                      title={isLocked ? 'File is locked by another user' : heldSave ? 'Outside your folders: this sends the change for approval (⌘S / Ctrl+S)' : 'Save (⌘S / Ctrl+S)'}
                    >
                      {!isDirty && toast?.text.startsWith('Saved') ? <Check className="h-3.5 w-3.5" /> : <Save className="h-3.5 w-3.5" />}
                      {heldSave ? 'Send for approval' : 'Save'}
                    </button>
                  </div>
                </div>

                {isBinaryContent(activeContent) ? (
                  <BinaryFileView path={activeFile!} content={activeContent} />
                ) : <CodeEditor
                  path={activeFile!}
                  value={activeContent}
                  isLocked={isLocked}
                  settings={settings}
                  onChange={v => setDraft(activeFile!, v)}
                  onShortcut={onShortcut}
                  onCursor={(line, column) => setCursor({ line, column })}
                  onReady={handleReady}
                />}
              </>
            ) : (
              <div className="flex flex-1 flex-col items-center justify-center gap-4 p-6 text-center">
                <div className="grid h-14 w-14 place-items-center rounded-2xl bg-gradient-to-br from-[var(--coord)]/20 to-[var(--coder)]/20 ring-1 ring-white/10">
                  <Code2 className="h-7 w-7 text-[var(--coder)]" />
                </div>
                <div>
                  <p className="font-medium text-[var(--ink)]">{files.size === 0 ? 'This room is empty' : 'No file open'}</p>
                  <p className="text-sm text-[var(--muted)]">
                    {files.size === 0 ? 'Import a project from your computer, start from the template, or create a file.' : 'Pick a file from the explorer, or start something new.'}
                  </p>
                </div>
                {files.size === 0 && (
                  <div className="flex flex-wrap justify-center gap-2">
                    <button type="button" className="btn primary flex items-center gap-1.5" onClick={() => setImporting({ initial: null })}>
                      <FolderInput className="h-4 w-4" /> Import project
                    </button>
                    <button
                      type="button"
                      className="btn flex items-center gap-1.5"
                      title="React + Vite frontend, Hono API, SQLite"
                      onClick={() => {
                        const starter = Array.from(starterProjectFiles(), ([path, f]) => ({ path, content: f.content }));
                        onUploadFiles(starter);
                        onFileSelect('src/App.tsx');
                        flash('Added the starter project');
                      }}
                    >
                      <LayoutTemplate className="h-4 w-4" /> Use starter template
                    </button>
                  </div>
                )}
                <div className="flex flex-wrap justify-center gap-2">
                  <button type="button" className="btn flex items-center gap-1.5" onClick={() => requestCreate('file')}>
                    <FilePlus className="h-4 w-4" /> New file
                  </button>
                  <button type="button" className="btn flex items-center gap-1.5" onClick={() => requestCreate('folder')}>
                    <FolderPlus className="h-4 w-4" /> New folder
                  </button>
                  <button type="button" className="btn flex items-center gap-1.5" onClick={() => uploadRef.current?.click()}>
                    <Upload className="h-4 w-4" /> Upload
                  </button>
                  <button type="button" className="btn flex items-center gap-1.5" onClick={() => setPalette('files')}>
                    <Command className="h-4 w-4" /> Quick open
                  </button>
                </div>
                <p className="mono text-[11px] text-[var(--faint)]">⌘P open file · ⌘⇧P commands · Ctrl+` terminal</p>
              </div>
            )}
            <input ref={uploadRef} type="file" multiple hidden onChange={e => { if (e.target.files?.length) handleUploadPick(e.target.files); e.target.value = ''; }} />

            {panel.open && (
              <BottomPanel
                roomId={roomId}
                view={panel.view}
                onViewChange={view => setPanel(p => ({ ...p, view }))}
                onClose={() => setPanel(p => ({ ...p, open: false }))}
                height={panel.height}
                onResize={height => setPanel(p => ({ ...p, height }))}
                fs={terminalFs}
                problems={problems}
                onJump={jumpTo}
                shellRequest={shellRequest}
                onShellRequestFailed={() => {
                  // No real shell in this browser: run JavaScript/TypeScript remotely instead
                  const fallback = shellRequest && remoteFallback(shellRequest.path);
                  if (shellRequest && fallback) void runRemotely(shellRequest.path, fallback);
                }}
                run={run}
                stdin={run ? stdinByFile.get(run.path) ?? '' : ''}
                onStdinChange={value => run && setStdinByFile(prev => new Map(prev).set(run.path, value))}
                onRun={() => run && void runFile(run.path)}
                onStopRun={stopRun}
              />
            )}

            {/* Status bar */}
            <div className="editor-status">
              <button type="button" className="status-btn" onClick={() => togglePanel('problems')} title="Problems">
                <CircleAlert className={`h-3 w-3 ${errorCount ? 'text-[var(--conflict)]' : ''}`} /> {errorCount}
                <TriangleAlert className={`ml-1 h-3 w-3 ${warningCount ? 'text-[#d29922]' : ''}`} /> {warningCount}
              </button>
              <button type="button" className="status-btn" onClick={() => togglePanel('terminal')} title="Toggle terminal (Ctrl+`)">
                <SquareTerminal className="h-3 w-3" /> Terminal
              </button>
              {hasFile && (
                <span className="flex items-center gap-1.5">
                  <span className={`h-1.5 w-1.5 rounded-full ${isDirty ? 'bg-[var(--coder)]' : 'bg-[var(--ok)]'}`} />
                  {isLocked ? 'Read-only' : isDirty ? 'Unsaved' : 'Saved'}
                </span>
              )}
              <span className="ml-auto" />
              {hasFile && (
                <>
                  <span>Ln {cursor.line}, Col {cursor.column}</span>
                  <span className="hidden xl:inline">{lineCount} lines</span>
                  <span className="capitalize">{languageFor(activeFile!)}</span>
                </>
              )}
            </div>
          </div>

          {palette && (
            <QuickOpen
              files={Array.from(files.keys()).sort()}
              commands={commands}
              initialMode={palette}
              onOpenFile={path => onFileSelect(path)}
              onClose={() => setPalette(null)}
            />
          )}

          {importing && (
            <ImportProjectDialog
              existingPaths={Array.from(files.keys())}
              lockedFiles={lockedFiles}
              initial={importing.initial}
              onImport={applyImport}
              onClose={() => setImporting(null)}
            />
          )}

          {showVsCode && (
            <OpenInVsCode
              roomId={roomId}
              roomTitle={roomTitle}
              files={files}
              lockedFiles={lockedFiles}
              onWrite={terminalFs.write}
              onApplied={paths => {
                // Edits from VS Code replace any unsaved drafts of the same files
                setDrafts(prev => (paths.some(p => prev.has(p)) ? new Map(Array.from(prev).filter(([p]) => !paths.includes(p))) : prev));
                if (paths.length) flash(`Brought back ${paths.length} file${paths.length === 1 ? '' : 's'} from VS Code`);
              }}
              onClose={() => setShowVsCode(false)}
            />
          )}

          {showShortcuts && (
            <div className="item-in absolute right-3 top-11 z-30 w-72 rounded-lg border border-[var(--line)] bg-[var(--panel)] p-3 shadow-2xl">
              <div className="mb-2 flex items-center justify-between">
                <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-[var(--muted)]">Keyboard shortcuts</span>
                <button type="button" className="icon-btn" onClick={() => setShowShortcuts(false)} aria-label="Close shortcuts"><X className="h-3.5 w-3.5" /></button>
              </div>
              <div className="space-y-1">
                {SHORTCUTS.map(([keys, what]) => (
                  <div key={keys} className="flex items-center justify-between gap-2 text-[12px]">
                    <span className="text-[var(--muted)]">{what}</span>
                    <kbd className="rounded border border-[var(--line)] bg-[var(--bg)] px-1.5 py-0.5 font-mono text-[10.5px] text-[var(--ink)]">{keys}</kbd>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

// Images, fonts and media are stored as data URLs: show the image (or the file's size) instead of base64 text
function BinaryFileView({ path, content }: { path: string; content: string }) {
  const bytes = Math.floor(((content.length - content.indexOf(',') - 1) * 3) / 4);
  const size = bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 overflow-auto p-6">
      {isImagePath(path) && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={content} alt={path} className="max-h-[70%] max-w-full rounded-md object-contain ring-1 ring-white/10" />
      )}
      <p className="text-xs text-[var(--muted)]">{path.split('/').pop()} · {size} · binary file, not editable as text</p>
    </div>
  );
}
