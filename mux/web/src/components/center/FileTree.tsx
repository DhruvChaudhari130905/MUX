'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ChevronRight, File, FileCode2, FileJson, FileText, FileImage, Palette, Globe, Folder, FolderOpen,
  FilePlus, FolderPlus, FolderInput, Upload, Pencil, Trash2, Download, Search, ChevronsDownUp, Lock, LockKeyhole, X,
} from 'lucide-react';
import { isProjectDrop } from '@/lib/projectImport';
import { BINARY_ASSET, bytesToDataUrl, dataUrlToBytes, isBinaryContent, maxBytesFor, mimeFor } from '@/lib/binaryFiles';

export type FileMap = Map<string, { content: string }>;

export interface UploadedFile {
  path: string;
  content: string;
}

interface TreeNode {
  name: string;
  path: string;
  isDirectory: boolean;
  children: TreeNode[];
}

function buildFileTree(paths: string[], folders: string[]): TreeNode[] {
  const root: TreeNode = { name: '', path: '', isDirectory: true, children: [] };
  const ensureDir = (parts: string[]) => {
    let node = root;
    parts.forEach((part, i) => {
      const path = parts.slice(0, i + 1).join('/');
      let child = node.children.find(c => c.name === part && c.isDirectory);
      if (!child) {
        child = { name: part, path, isDirectory: true, children: [] };
        node.children.push(child);
      }
      node = child;
    });
    return node;
  };
  folders.forEach(f => ensureDir(f.split('/').filter(Boolean)));
  paths.forEach(p => {
    const parts = p.split('/');
    const dir = ensureDir(parts.slice(0, -1));
    if (!dir.children.some(c => c.path === p)) dir.children.push({ name: parts[parts.length - 1], path: p, isDirectory: false, children: [] });
  });
  const sort = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => (a.isDirectory === b.isDirectory ? a.name.localeCompare(b.name) : a.isDirectory ? -1 : 1));
    nodes.forEach(n => sort(n.children));
  };
  sort(root.children);
  return root.children;
}

// Icon + tint by file extension
export function FileIcon({ name, className = 'h-4 w-4' }: { name: string; className?: string }) {
  const ext = name.split('.').pop()?.toLowerCase();
  if (ext === 'tsx' || ext === 'jsx') return <FileCode2 className={`${className} text-[#61dafb]`} />;
  if (ext === 'ts') return <FileCode2 className={`${className} text-[#3b82f6]`} />;
  if (ext === 'js' || ext === 'mjs' || ext === 'cjs') return <FileCode2 className={`${className} text-[#f1e05a]`} />;
  if (ext === 'json') return <FileJson className={`${className} text-[#e3b341]`} />;
  if (ext === 'css' || ext === 'scss') return <Palette className={`${className} text-[#f778ba]`} />;
  if (ext === 'html') return <Globe className={`${className} text-[#f0883e]`} />;
  if (ext === 'md' || ext === 'txt') return <FileText className={`${className} text-[var(--muted)]`} />;
  if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp'].includes(ext || '')) return <FileImage className={`${className} text-[#a371f7]`} />;
  return <File className={`${className} text-[var(--muted)]`} />;
}

function parentOf(path: string): string {
  const i = path.lastIndexOf('/');
  return i === -1 ? '' : path.slice(0, i);
}

// Download of one file: text as is, binary files (data URLs) as their bytes
export function downloadFile(path: string, content: string) {
  const blob = isBinaryContent(content) ? new Blob([dataUrlToBytes(content) as BlobPart], { type: mimeFor(path) }) : new Blob([content], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = path.split('/').pop() || 'file.txt';
  a.click();
  URL.revokeObjectURL(url);
}

// Read browser File objects: text as is, images/fonts/media as data URLs; other binaries and oversized files are skipped
export async function readUploads(list: FileList | File[], targetDir: string): Promise<{ files: UploadedFile[]; skipped: string[] }> {
  const files: UploadedFile[] = [];
  const skipped: string[] = [];
  for (const f of Array.from(list)) {
    const asset = BINARY_ASSET.test(f.name);
    const binary = !asset && !f.name.endsWith('.svg') && (/^(image|audio|video|font)\//.test(f.type) || /\.(zip|gz|tgz|rar|7z|exe|dll|wasm)$/i.test(f.name));
    if (binary || f.size > maxBytesFor(f.name)) {
      skipped.push(f.name);
      continue;
    }
    const relative = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
    files.push({ path: targetDir ? `${targetDir}/${relative}` : relative, content: asset ? bytesToDataUrl(new Uint8Array(await f.arrayBuffer()), f.name) : await f.text() });
  }
  return { files, skipped };
}

type EditState =
  | { mode: 'new-file' | 'new-folder'; parent: string }
  | { mode: 'rename'; target: string; isDirectory: boolean };

export interface CreateRequest {
  kind: 'file' | 'folder';
  nonce: number;
}

interface FileTreeProps {
  files: FileMap;
  activeFile: string | null;
  onFileSelect: (path: string) => void;
  fileVersions: Map<string, number>;
  lockedFiles: Set<string>;
  lockingUser: Map<string, string>;
  dirtyFiles: Set<string>;
  onCreateFile: (path: string) => void;
  onDelete: (path: string, isDirectory: boolean) => void;
  onRename: (from: string, to: string, isDirectory: boolean) => void;
  onUpload: (files: UploadedFile[]) => void;
  // Lets the editor's empty state ask the explorer to start a new file/folder
  createRequest?: CreateRequest | null;
  // Whole-project import (button, or a folder/.zip dropped on the tree)
  onImport?: () => void;
  onImportDrop?: (items: DataTransferItemList) => void;
  // Outside your folders: changes there go for approval (lib/fileAccess.ts)
  needsApproval?: (path: string, isDirectory: boolean) => boolean;
}

const APPROVAL_HINT = 'Outside your folders: changes here need approval';

export function FileTree({
  files,
  activeFile,
  onFileSelect,
  lockedFiles,
  lockingUser,
  dirtyFiles,
  onCreateFile,
  onDelete,
  onRename,
  onUpload,
  createRequest,
  onImport,
  onImportDrop,
  needsApproval,
}: FileTreeProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set(['src', 'src/components']));
  const [extraFolders, setExtraFolders] = useState<Set<string>>(new Set()); // empty folders created in the UI
  const [selectedDir, setSelectedDir] = useState<string | null>(null);
  const [editing, setEditing] = useState<EditState | null>(null);
  const [draftName, setDraftName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [dragging, setDragging] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [recent, setRecent] = useState<Set<string>>(new Set()); // files added in this session get a "new" badge
  const markRecent = (paths: string[]) => setRecent(prev => new Set([...Array.from(prev), ...paths]));
  const uploadRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);

  const paths = useMemo(() => Array.from(files.keys()), [files]);
  const allFolders = useMemo(() => {
    const set = new Set(extraFolders);
    paths.forEach(p => {
      const parts = p.split('/');
      for (let i = 1; i < parts.length; i++) set.add(parts.slice(0, i).join('/'));
    });
    return set;
  }, [paths, extraFolders]);

  // Where new files/folders/uploads go: the clicked folder, else the active file's folder, else root
  const targetDir = selectedDir ?? (activeFile ? parentOf(activeFile) : '');

  const q = query.trim().toLowerCase();
  const visiblePaths = q ? paths.filter(p => p.toLowerCase().includes(q)) : paths;
  const tree = useMemo(
    () => buildFileTree(visiblePaths, q ? [] : Array.from(extraFolders)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [visiblePaths.join('\n'), q, extraFolders],
  );

  const flash = (msg: string) => {
    setNotice(msg);
    setTimeout(() => setNotice(null), 2500);
  };

  const startCreate = (mode: 'new-file' | 'new-folder', parent = targetDir) => {
    setEditing({ mode, parent });
    setDraftName('');
    setError(null);
    setConfirmDelete(null);
    if (parent) setExpanded(prev => new Set(prev).add(parent));
  };

  useEffect(() => {
    if (createRequest) startCreate(createRequest.kind === 'file' ? 'new-file' : 'new-folder');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [createRequest?.nonce]);

  const validate = (name: string, parent: string, ignore?: string): string | null => {
    if (!name.trim()) return 'Name is required';
    if (/[\\:*?"<>|]/.test(name) || name.split('/').some(s => s === '' || s === '.' || s === '..')) return 'Invalid name';
    const full = parent ? `${parent}/${name.trim()}` : name.trim();
    if (full !== ignore && (files.has(full) || allFolders.has(full))) return 'A file or folder with that name already exists';
    return null;
  };

  const commit = () => {
    if (!editing) return;
    const name = draftName.trim();
    if (editing.mode === 'rename') {
      const parent = parentOf(editing.target);
      const err = validate(name, parent, editing.target);
      if (err) return setError(err);
      const to = parent ? `${parent}/${name}` : name;
      if (to !== editing.target) {
        if (editing.isDirectory) {
          setExtraFolders(prev => {
            const next = new Set<string>();
            prev.forEach(f => next.add(f === editing.target || f.startsWith(editing.target + '/') ? to + f.slice(editing.target.length) : f));
            return next;
          });
          setExpanded(prev => new Set(prev).add(to));
        }
        onRename(editing.target, to, editing.isDirectory);
        if (!editing.isDirectory) markRecent([to]);
      }
    } else {
      const err = validate(name, editing.parent);
      if (err) return setError(err);
      const full = editing.parent ? `${editing.parent}/${name}` : name;
      // Every folder on the way becomes visible
      const parts = full.split('/');
      setExpanded(prev => {
        const next = new Set(prev);
        for (let i = 1; i <= parts.length; i++) next.add(parts.slice(0, i).join('/'));
        return next;
      });
      if (editing.mode === 'new-folder') {
        setExtraFolders(prev => new Set(prev).add(full));
        setSelectedDir(full);
      } else {
        onCreateFile(full);
        markRecent([full]);
        setSelectedDir(null);
      }
    }
    setEditing(null);
    setError(null);
  };

  const cancel = () => {
    setEditing(null);
    setError(null);
  };

  const doDelete = (path: string, isDirectory: boolean) => {
    if (isDirectory) {
      setExtraFolders(prev => new Set(Array.from(prev).filter(f => f !== path && !f.startsWith(path + '/'))));
      if (selectedDir === path || selectedDir?.startsWith(path + '/')) setSelectedDir(null);
    }
    onDelete(path, isDirectory);
    setConfirmDelete(null);
  };

  const handleUpload = async (list: FileList | File[]) => {
    const { files: uploaded, skipped } = await readUploads(list, targetDir);
    if (uploaded.length) {
      onUpload(uploaded);
      markRecent(uploaded.map(f => f.path));
      if (targetDir) setExpanded(prev => new Set(prev).add(targetDir));
    }
    flash(
      [uploaded.length && `Added ${uploaded.length} file${uploaded.length === 1 ? '' : 's'}${targetDir ? ` to ${targetDir}/` : ''}`, skipped.length && `skipped ${skipped.join(', ')} (binary or over 1 MB, 2 MB for images)`]
        .filter(Boolean)
        .join(' · ') || 'Nothing to add',
    );
  };

  const toggle = (path: string) => {
    setSelectedDir(path);
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const inputRow = (depth: number, icon: React.ReactNode) => (
    <div className="item-in px-2 py-0.5" style={{ paddingLeft: `${8 + depth * 14}px` }}>
      <div className={`flex items-center gap-1.5 rounded border bg-[var(--bg)] px-1.5 ${error ? 'tree-shake border-[var(--conflict)]' : 'border-[var(--coord)]'}`}>
        {icon}
        <input
          autoFocus
          value={draftName}
          onChange={e => { setDraftName(e.target.value); setError(null); }}
          onKeyDown={e => {
            if (e.key === 'Enter') commit();
            if (e.key === 'Escape') cancel();
          }}
          onBlur={() => (draftName.trim() ? commit() : cancel())}
          placeholder={editing?.mode === 'new-folder' ? 'folder name' : editing?.mode === 'rename' ? '' : 'file.tsx or dir/file.tsx'}
          className="min-w-0 flex-1 bg-transparent py-1 font-[inherit] text-[12.5px] text-[var(--ink)] outline-none placeholder:text-[var(--faint)]"
          aria-label={editing?.mode === 'rename' ? 'New name' : editing?.mode === 'new-folder' ? 'New folder name' : 'New file name'}
        />
      </div>
      {error && <p className="mt-1 font-sans text-[11px] leading-tight text-[var(--conflict)]">{error}</p>}
    </div>
  );

  const actionBtn = (label: string, icon: React.ReactNode, onClick: () => void, danger = false) => (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={e => { e.stopPropagation(); onClick(); }}
      className={`grid h-5 w-5 place-items-center rounded transition-colors hover:bg-white/10 ${danger ? 'hover:text-[var(--conflict)]' : 'hover:text-[var(--ink)]'}`}
    >
      {icon}
    </button>
  );

  const renderNode = (node: TreeNode, depth: number): React.ReactNode => {
    const isRenaming = editing?.mode === 'rename' && editing.target === node.path;
    const isConfirming = confirmDelete === node.path;

    if (isRenaming) {
      return <div key={node.path}>{inputRow(depth, node.isDirectory ? <Folder className="h-4 w-4 text-[var(--coder)]" /> : <FileIcon name={draftName || node.name} />)}</div>;
    }

    const confirmBar = isConfirming && (
      <span className="item-in ml-auto flex items-center gap-1 font-sans text-[11px]" onClick={e => e.stopPropagation()}>
        <span className="text-[var(--conflict)]">Delete?</span>
        <button type="button" className="rounded px-1.5 py-0.5 text-[var(--conflict)] hover:bg-[var(--conflict)]/15" onClick={() => doDelete(node.path, node.isDirectory)}>Yes</button>
        <button type="button" className="rounded px-1.5 py-0.5 hover:bg-white/10" onClick={() => setConfirmDelete(null)}>No</button>
      </span>
    );

    if (node.isDirectory) {
      const isOpen = !!q || expanded.has(node.path);
      const isTarget = selectedDir === node.path;
      const creatingHere = editing && editing.mode !== 'rename' && editing.parent === node.path;
      return (
        <div key={node.path}>
          <div
            role="treeitem"
            aria-expanded={isOpen}
            aria-selected={isTarget}
            tabIndex={0}
            onClick={() => toggle(node.path)}
            onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(node.path); } }}
            className={`tree-row group ${isTarget ? 'target' : ''}`}
            style={{ paddingLeft: `${8 + depth * 14}px` }}
          >
            <ChevronRight className={`h-3.5 w-3.5 flex-none transition-transform duration-200 ${isOpen ? 'rotate-90' : ''}`} />
            {isOpen ? <FolderOpen className="h-4 w-4 flex-none text-[var(--coder)]" /> : <Folder className="h-4 w-4 flex-none text-[var(--coder)]" />}
            <span className="truncate text-[var(--ink)]">{node.name}</span>
            {needsApproval?.(node.path, true) && <span title={APPROVAL_HINT}><LockKeyhole className="h-3 w-3 flex-none text-[var(--faint)]" /></span>}
            {confirmBar || (
              <span className="row-actions ml-auto flex items-center gap-0.5">
                {actionBtn('New file here', <FilePlus className="h-3.5 w-3.5" />, () => startCreate('new-file', node.path))}
                {actionBtn('New folder here', <FolderPlus className="h-3.5 w-3.5" />, () => startCreate('new-folder', node.path))}
                {actionBtn('Rename', <Pencil className="h-3 w-3" />, () => { setEditing({ mode: 'rename', target: node.path, isDirectory: true }); setDraftName(node.name); setError(null); })}
                {actionBtn('Delete folder', <Trash2 className="h-3.5 w-3.5" />, () => setConfirmDelete(node.path), true)}
              </span>
            )}
          </div>
          {isOpen && (
            <div className="tree-children" style={{ ['--guide' as string]: `${15 + depth * 14}px` }}>
              {creatingHere && inputRow(depth + 1, editing.mode === 'new-folder' ? <Folder className="h-4 w-4 text-[var(--coder)]" /> : <FileIcon name={draftName} />)}
              {node.children.map(child => renderNode(child, depth + 1))}
              {node.children.length === 0 && !creatingHere && (
                <div className="py-0.5 font-sans text-[11px] italic text-[var(--faint)]" style={{ paddingLeft: `${30 + depth * 14}px` }}>empty folder</div>
              )}
            </div>
          )}
        </div>
      );
    }

    const isActive = node.path === activeFile;
    const isLocked = lockedFiles.has(node.path);
    const isNew = recent.has(node.path);
    const isDirty = dirtyFiles.has(node.path);
    return (
      <div
        key={node.path}
        role="treeitem"
        aria-selected={isActive}
        tabIndex={0}
        onClick={() => { setSelectedDir(null); onFileSelect(node.path); }}
        onKeyDown={e => { if (e.key === 'Enter') onFileSelect(node.path); }}
        className={`tree-row group ${isActive ? 'on' : ''}`}
        style={{ paddingLeft: `${22 + depth * 14}px` }}
        title={node.path}
      >
        <FileIcon name={node.name} className="h-4 w-4 flex-none" />
        <span className={`truncate ${isActive ? 'text-[var(--ink)]' : ''}`}>{node.name}</span>
        {isLocked && <span title={`Locked by ${lockingUser.get(node.path)}`}><Lock className="h-3 w-3 flex-none text-[var(--ask)]" /></span>}
        {!isLocked && needsApproval?.(node.path, false) && <span title={APPROVAL_HINT}><LockKeyhole className="h-3 w-3 flex-none text-[var(--faint)]" /></span>}
        {confirmBar || (
          <>
            <span className="row-meta ml-auto flex items-center gap-1">
              {isDirty && <span className="h-1.5 w-1.5 rounded-full bg-[var(--coder)]" title="Unsaved changes" />}
              {!isDirty && isNew && <span className="font-sans text-[10px] text-[var(--ok)]">new</span>}
            </span>
            <span className="row-actions ml-auto flex items-center gap-0.5">
              {actionBtn('Rename', <Pencil className="h-3 w-3" />, () => { setEditing({ mode: 'rename', target: node.path, isDirectory: false }); setDraftName(node.name); setError(null); })}
              {actionBtn('Download', <Download className="h-3.5 w-3.5" />, () => downloadFile(node.path, files.get(node.path)?.content ?? ''))}
              {actionBtn('Delete file', <Trash2 className="h-3.5 w-3.5" />, () => setConfirmDelete(node.path), true)}
            </span>
          </>
        )}
      </div>
    );
  };

  const creatingAtRoot = editing && editing.mode !== 'rename' && editing.parent === '';
  const toolBtn = (label: string, icon: React.ReactNode, onClick: () => void) => (
    <button type="button" title={label} aria-label={label} onClick={onClick} className="grid h-6 w-6 place-items-center rounded text-[var(--muted)] transition-all hover:scale-110 hover:bg-white/10 hover:text-[var(--ink)]">
      {icon}
    </button>
  );

  return (
    <div
      className={`tree relative flex flex-col ${dragging ? 'dragging' : ''}`}
      onDragEnter={e => { if (e.dataTransfer.types.includes('Files')) { dragDepth.current++; setDragging(true); } }}
      onDragOver={e => { if (e.dataTransfer.types.includes('Files')) e.preventDefault(); }}
      onDragLeave={() => { dragDepth.current = Math.max(0, dragDepth.current - 1); if (dragDepth.current === 0) setDragging(false); }}
      onDrop={e => {
        e.preventDefault();
        dragDepth.current = 0;
        setDragging(false);
        if (onImportDrop && isProjectDrop(e.dataTransfer.items)) onImportDrop(e.dataTransfer.items);
        else if (e.dataTransfer.files.length) handleUpload(e.dataTransfer.files);
      }}
    >
      <div className="flex items-center justify-between gap-1 px-3 pb-1.5 pt-1">
        <span className="font-sans text-[10.5px] font-medium uppercase tracking-[0.12em] text-[var(--muted)]">Explorer</span>
        <div className="flex items-center">
          {toolBtn('New file', <FilePlus className="h-3.5 w-3.5" />, () => startCreate('new-file'))}
          {toolBtn('New folder', <FolderPlus className="h-3.5 w-3.5" />, () => startCreate('new-folder'))}
          {toolBtn('Upload files', <Upload className="h-3.5 w-3.5" />, () => uploadRef.current?.click())}
          {onImport && toolBtn('Import project (folder or .zip)', <FolderInput className="h-3.5 w-3.5" />, onImport)}
          {toolBtn('Collapse all', <ChevronsDownUp className="h-3.5 w-3.5" />, () => { setExpanded(new Set()); setSelectedDir(null); })}
        </div>
        <input
          ref={uploadRef}
          type="file"
          multiple
          hidden
          onChange={e => { if (e.target.files?.length) handleUpload(e.target.files); e.target.value = ''; }}
        />
      </div>

      <div className="mx-2 mb-1.5 flex items-center gap-1.5 rounded border border-[var(--line)] bg-[var(--bg)] px-2 transition-colors focus-within:border-[var(--coord)]">
        <Search className="h-3.5 w-3.5 flex-none text-[var(--faint)]" />
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search files"
          className="min-w-0 flex-1 bg-transparent py-1 font-sans text-[12px] text-[var(--ink)] outline-none placeholder:text-[var(--faint)]"
          aria-label="Search files"
        />
        {query && (
          <button type="button" onClick={() => setQuery('')} className="text-[var(--faint)] hover:text-[var(--ink)]" aria-label="Clear search">
            <X className="h-3 w-3" />
          </button>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-auto pb-2" role="tree" aria-label="Files" onClick={e => { if (e.target === e.currentTarget) setSelectedDir(''); }}>
        {creatingAtRoot && inputRow(0, editing.mode === 'new-folder' ? <Folder className="h-4 w-4 text-[var(--coder)]" /> : <FileIcon name={draftName} />)}
        {tree.map(node => renderNode(node, 0))}
        {tree.length === 0 && !creatingAtRoot && (
          <div className="px-3 py-4 text-center font-sans text-[12px] text-[var(--faint)]">
            {q ? `No files match “${query}”` : 'No files yet'}
          </div>
        )}
      </div>

      <div className="border-t border-[var(--line)] px-3 py-1.5 font-sans text-[10.5px] text-[var(--faint)]">
        {notice ? <span key={notice} className="item-in block text-[var(--coder)]">{notice}</span> : <>New items go in <span className="mono text-[var(--muted)]">{targetDir ? `${targetDir}/` : 'root'}</span> · drop files to upload</>}
      </div>

      {dragging && (
        <div className="pointer-events-none absolute inset-1 z-10 grid place-items-center rounded-md border-2 border-dashed border-[var(--coder)] bg-[var(--coder)]/10 backdrop-blur-[1px]">
          <div className="item-in text-center font-sans">
            <Upload className="mx-auto mb-1 h-6 w-6 animate-bounce text-[var(--coder)]" />
            <p className="text-[12px] text-[var(--ink)]">Drop to add to</p>
            <p className="mono text-[11px] text-[var(--coder)]">{targetDir ? `${targetDir}/` : 'project root'}</p>
          </div>
        </div>
      )}
    </div>
  );
}
