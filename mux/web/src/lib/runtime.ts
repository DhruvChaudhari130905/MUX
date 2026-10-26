// Keeps the room's files and the in-browser WebContainer in step, so a real shell (jsh, npm, node, npx)
// can run against them. Room edits are written into the container; files that shell commands create,
// change or delete are sent back to the room. Dependencies and build output stay in the container.
import type { WebContainer, WebContainerProcess } from '@webcontainer/api';
import { initWebContainer } from './webcontainer';
import { BINARY_ASSET, bytesToDataUrl, fileBytes, maxBytesFor } from './binaryFiles';
import { notify } from './notifications';
import { dropSnapshot, loadSnapshot, saveSnapshot, snapshotKey } from './packageSnapshots';
import { enqueueSync } from './syncQueue';

export interface RoomFs {
  files: Map<string, { content: string }>;
  write: (path: string, content: string) => void | Promise<void>;
  remove: (paths: string[]) => void;
}

// Never synced back into the room
const IGNORED_DIRS = new Set(['node_modules', '.git', 'dist', 'build', '.next', '.cache', '.npm', '.vite', 'coverage', '.turbo']);
const BINARY = /\.(png|jpe?g|gif|webp|ico|bmp|woff2?|ttf|otf|eot|zip|gz|tgz|pdf|mp[34]|wav|webm|mov|sqlite3?|db|wasm|node)$/i;
const LOCKFILES = new Set(['package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'bun.lockb']);

export function webContainersSupported(): { ok: true } | { ok: false; reason: string } {
  if (typeof window === 'undefined') return { ok: false, reason: 'Not in a browser' };
  if (!window.crossOriginIsolated) return { ok: false, reason: 'This page isn’t cross-origin isolated (COOP/COEP headers missing)' };
  if (typeof SharedArrayBuffer === 'undefined') return { ok: false, reason: 'This browser has no SharedArrayBuffer support' };
  return { ok: true };
}

function ignored(path: string): boolean {
  const parts = path.split('/');
  if (parts.some(p => IGNORED_DIRS.has(p))) return true;
  const name = parts[parts.length - 1];
  // Lockfiles that `npm install` writes are large and machine-made: they stay in the container
  return (BINARY.test(name) && !BINARY_ASSET.test(name)) || name === '.DS_Store' || name.endsWith('.log') || LOCKFILES.has(name);
}

// ───────────── State shared by every terminal on the page ─────────────

let mountedRoom: string | null = null;
let synced = new Map<string, string>(); // what the container holds for each room-tracked file
let roomFs: RoomFs | null = null;
let watcherStarted = false;
let pushChain: Promise<void> = Promise.resolve();
const serverListeners = new Set<(port: number, url: string) => void>();
const openServers = new Map<number, string>();

async function container(): Promise<WebContainer> {
  const wc = (await initWebContainer()) as WebContainer | null;
  if (!wc) throw new Error('WebContainers only run in the browser');
  return wc;
}

// ───────────── Installed packages ─────────────

// Written after an install (or a snapshot restore): the package.json the container's node_modules matches
const INSTALLED_MARKER = 'node_modules/.mux-installed';

// Puts saved packages back into the container; false when there's no usable snapshot
// Saved packages can only be mounted into a container that hasn't installed or mounted any yet (a fresh
// page): on a container that has, mount() resolves without the files ever appearing. Same-tab room switches
// keep node_modules instead (see attach), so they don't need a snapshot.
let packagesTouched = false;

// Puts saved packages back into the container; false when there's no usable snapshot
async function restoreSnapshot(wc: WebContainer, key: string, roomId: string, packageJson: string): Promise<boolean> {
  if (packagesTouched) return false;
  const snapshot = await loadSnapshot(key, roomId);
  if (!snapshot) return false;
  packagesTouched = true;
  appendLog('$ npm install (skipped: restoring the packages saved from an earlier install)\n');
  let step = 'clearing node_modules';
  try {
    await wc.fs.rm('node_modules', { recursive: true, force: true });
    step = 'mounting node_modules';
    // The mount only lands in a folder that exists, and the executable bit on node_modules/.bin doesn't
    // survive it, so it's set again below
    await wc.fs.mkdir('node_modules', { recursive: true });
    await wc.mount(snapshot.data, { mountPoint: 'node_modules' });
    step = 'waiting for node_modules';
    for (let i = 0; ; i++) {
      try {
        await wc.fs.readdir('node_modules/.bin');
        break;
      } catch (err) {
        if (i >= 50) throw err;
        await new Promise(resolve => setTimeout(resolve, 100));
      }
    }
    step = 'making node_modules/.bin executable';
    const bins = (await wc.fs.readdir('node_modules/.bin')).map(name => `node_modules/.bin/${name}`);
    const chmod = await wc.spawn('chmod', ['+x', ...bins]);
    const chmodCode = await chmod.exit;
    if (chmodCode !== 0) throw new Error(`chmod exited with ${chmodCode}`);
    step = 'writing package-lock.json';
    if (snapshot.lockfile) await wc.fs.writeFile('package-lock.json', snapshot.lockfile);
    step = 'writing the installed marker';
    await wc.fs.writeFile(INSTALLED_MARKER, packageJson);
    return true;
  } catch (err) {
    // On a fresh container this means the snapshot itself is bad: don't use it again
    console.warn(`Restoring saved packages failed while ${step}, installing instead:`, err);
    await dropSnapshot(key);
    await wc.fs.rm('node_modules', { recursive: true, force: true }).catch(() => undefined);
    appendLog('Restoring failed; installing instead.\n');
    return false;
  }
}

// Saves a fresh install for next time. The export happens before the dev server starts writing its own
// cache into node_modules; storing it in IndexedDB doesn't hold up the start.
async function keepSnapshot(wc: WebContainer, key: string, roomId: string) {
  try {
    const data = await wc.export('node_modules', { format: 'binary', excludes: ['.vite/**', '.cache/**'] });
    const lockfile = await wc.fs.readFile('package-lock.json', 'utf-8').catch(() => null);
    void saveSnapshot(key, roomId, { data, lockfile }).catch(err => console.warn('Saving packages failed:', err));
  } catch (err) {
    console.warn('Exporting packages failed:', err);
  }
}

async function installedFor(wc: WebContainer): Promise<string | null> {
  try {
    return await wc.fs.readFile(INSTALLED_MARKER, 'utf-8');
  } catch {
    return null;
  }
}

async function ensureDir(wc: WebContainer, path: string) {
  const dir = path.split('/').slice(0, -1).join('/');
  if (dir) await wc.fs.mkdir(dir, { recursive: true });
}

// Boots the container (once) and makes it hold exactly this room's files. The terminal and the preview
// can both attach at once: attaches run one after another so one can't push files while another wipes.
let attachChain: Promise<unknown> = Promise.resolve();
export function attachRoom(roomId: string, fs: RoomFs): Promise<WebContainer> {
  const run = attachChain.then(() => attach(roomId, fs));
  attachChain = run.catch(() => undefined);
  return run;
}

async function attach(roomId: string, fs: RoomFs): Promise<WebContainer> {
  const wc = await container();
  if (mountedRoom !== roomId) {
    // Detach first: while the previous room's files are wiped below, the watcher sees them vanish, and
    // with the old room still tracked (and the new room's fs attached) it would delete them from the new room
    const previous = mountedRoom;
    mountedRoom = null;
    roomFs = null;
    // Another room was mounted earlier in this tab: start from an empty project. The wipe runs in the push
    // queue, so a push that was already under way finishes first (and is wiped) instead of writing files
    // in the middle of it; pushes queued meanwhile see no mounted room and skip.
    pushChain = pushChain.then(async () => {
      if (previous !== null) {
        // node_modules stays: the marker says which package.json it matches, so the next room either uses it
        // as is (same dependencies) or npm install brings it in line, which is much faster than from scratch
        for (const entry of await wc.fs.readdir('.')) {
          if (entry !== 'node_modules') await wc.fs.rm(entry, { recursive: true, force: true });
        }
      }
    }).catch(err => console.warn('WebContainer reset failed:', err));
    await pushChain;
    pending = new Set();
    mountedRoom = roomId;
    dirWatchers.forEach(w => w.close());
    dirWatchers.clear();
    openServers.clear();
  }
  roomFs = fs;
  startWatcher(wc);
  startServerEvents(wc);
  // Write every file again rather than trusting what was synced before: attaching (or "Try again") then
  // always leaves the container holding the room's files, even if something removed them meanwhile
  await pushChain;
  synced = new Map();
  await pushRoomFiles(roomId, fs.files);
  await watchTopLevelDirs(wc);
  return wc;
}

// Every call names its room and does nothing unless that room is the one in the container. A room's
// components render (and call these) before its attach has swapped the container over; without the check,
// the new room's fs was attached to the old room's files and the watcher copied one room into the other.
export function updateRoomFs(roomId: string, fs: RoomFs) {
  if (mountedRoom === roomId) roomFs = fs;
}

// Room → container: write what changed since the last sync, delete what the room no longer has
export function pushRoomFiles(roomId: string, files: Map<string, { content: string }>): Promise<void> {
  const queued = enqueueSync(pushChain, async () => {
    if (mountedRoom !== roomId) return;
    const wc = await container();
    for (const [path, { content }] of Array.from(files)) {
      if (synced.get(path) === content) continue;
      await ensureDir(wc, path);
      await wc.fs.writeFile(path, fileBytes(content));
      synced.set(path, content);
    }
    for (const path of Array.from(synced.keys())) {
      if (files.has(path)) continue;
      await wc.fs.rm(path, { force: true });
      synced.delete(path);
    }
  }, err => {
    console.warn('WebContainer sync failed:', err);
    if (mountedRoom === roomId && previewRoom === roomId) {
      setPreview({ phase: 'error', error: `Preview file sync failed: ${err instanceof Error ? err.message : String(err)}` });
    }
  });
  // Keep the queue usable after one failed sync, while returning this sync's rejection to its caller.
  pushChain = queued.queue;
  return queued.result;
}

// Writes straight into the container; the watcher then copies the file into the room like any shell edit
export async function writeContainerFile(roomId: string, path: string, content: string): Promise<void> {
  if (mountedRoom !== roomId) throw new Error('This room is not running in the container yet');
  const wc = await container();
  await ensureDir(wc, path);
  await wc.fs.writeFile(path, fileBytes(content));
}

// ───────────── Container → room ─────────────

let pending = new Set<string>();
let flushTimer: ReturnType<typeof setTimeout> | null = null;

// Watching the project root recursively would stream an event for every file npm writes into
// node_modules, so the root is watched shallowly and each top-level folder (except ignored ones) recursively.
const dirWatchers = new Map<string, { close(): void }>();

function queue(wc: WebContainer, path: string) {
  if (!path || ignored(path)) return;
  pending.add(path);
  if (flushTimer) clearTimeout(flushTimer);
  flushTimer = setTimeout(() => void flush(wc), 150);
}

const decode = (name: string | Uint8Array) => (typeof name === 'string' ? name : new TextDecoder().decode(name)).replace(/^\.?\//, '');

function watchDir(wc: WebContainer, dir: string) {
  if (dirWatchers.has(dir) || IGNORED_DIRS.has(dir)) return;
  try {
    dirWatchers.set(dir, wc.fs.watch(dir, { recursive: true }, (_event, filename) => queue(wc, `${dir}/${decode(filename)}`)));
  } catch {
    // folder vanished before we could watch it
  }
}

async function watchTopLevelDirs(wc: WebContainer) {
  for (const entry of await wc.fs.readdir('.', { withFileTypes: true })) {
    if (entry.isDirectory()) watchDir(wc, entry.name);
  }
}

function startWatcher(wc: WebContainer) {
  if (watcherStarted) return;
  watcherStarted = true;
  wc.fs.watch('.', (_event, filename) => {
    const name = decode(filename);
    if (!name || IGNORED_DIRS.has(name)) return;
    queue(wc, name);
    // A new top-level folder needs its own watcher; a removed one drops it
    void wc.fs.readdir(name).then(
      () => watchDir(wc, name),
      () => { dirWatchers.get(name)?.close(); dirWatchers.delete(name); },
    );
  });
  void watchTopLevelDirs(wc);
}

async function readText(wc: WebContainer, path: string): Promise<{ kind: 'file'; content: string } | { kind: 'dir' } | { kind: 'missing' } | { kind: 'skip' }> {
  // Reading a folder doesn't reliably throw EISDIR in a WebContainer (it can return no bytes), which
  // used to upload folders as empty files: ask whether it's a folder first
  try {
    await wc.fs.readdir(path);
    return { kind: 'dir' };
  } catch {
    // not a folder (or gone): read it as a file below
  }
  try {
    const bytes = await wc.fs.readFile(path);
    if (bytes.length > maxBytesFor(path)) return { kind: 'skip' };
    return { kind: 'file', content: BINARY_ASSET.test(path) ? bytesToDataUrl(bytes, path) : new TextDecoder().decode(bytes) };
  } catch (err) {
    const msg = String((err as Error)?.message ?? err);
    if (/EISDIR|illegal operation on a directory/i.test(msg)) return { kind: 'dir' };
    if (/ENOENT|no such file/i.test(msg)) return { kind: 'missing' };
    return { kind: 'skip' };
  }
}

async function listFiles(wc: WebContainer, dir: string, out: string[] = [], depth = 0): Promise<string[]> {
  if (depth > 12 || out.length > 2000) return out;
  for (const entry of await wc.fs.readdir(dir, { withFileTypes: true })) {
    const path = `${dir}/${entry.name}`;
    if (ignored(path)) continue;
    if (entry.isDirectory()) await listFiles(wc, path, out, depth + 1);
    else if (entry.isFile()) out.push(path);
  }
  return out;
}

async function flush(wc: WebContainer) {
  const paths = Array.from(pending);
  pending = new Set();
  const fs = roomFs;
  if (!fs || !mountedRoom) return;

  const removed: string[] = [];
  const check = async (path: string) => {
    const r = await readText(wc, path);
    if (r.kind === 'file') {
      // package-lock.json is big and noisy; only sync it if the room already tracks one
      if (path === 'package-lock.json' && !synced.has(path)) return;
      if (synced.get(path) === r.content) return;
      synced.set(path, r.content);
      await fs.write(path, r.content);
    } else if (r.kind === 'dir') {
      // A folder appeared (mkdir, mv, git clone…): sync whatever files it holds
      for (const f of await listFiles(wc, path)) await check(f);
    } else if (r.kind === 'missing') {
      // The path, or a folder containing tracked files, was deleted
      for (const p of Array.from(synced.keys())) {
        if (p === path || p.startsWith(`${path}/`)) {
          synced.delete(p);
          removed.push(p);
        }
      }
    }
  };
  for (const p of paths) await check(p);
  if (removed.length) fs.remove(removed);
}

// ───────────── Dev servers ─────────────

let serverEventsStarted = false;

function startServerEvents(wc: WebContainer) {
  if (serverEventsStarted) return;
  serverEventsStarted = true;
  wc.on('server-ready', (port, url) => {
    openServers.set(port, url);
    notify({ category: 'terminal', tone: 'ok', title: `Dev server ready on port ${port}`, body: url, onClick: () => window.open(url, '_blank', 'noopener') });
    serverListeners.forEach(l => l(port, url));
  });
  wc.on('port', (port, type) => {
    if (type === 'close') openServers.delete(port);
  });
}

export function onServerReady(listener: (port: number, url: string) => void): () => void {
  serverListeners.add(listener);
  return () => serverListeners.delete(listener);
}

// Servers belong to the room mounted in the container: another room's (still running after a room
// switch until this room attaches) is never shown as this room's app
export function runningServers(roomId: string): [number, string][] {
  return mountedRoom === roomId ? Array.from(openServers) : [];
}

// Dev servers print http://localhost:<port>/, but that port is inside the browser's WebContainer, not
// on this machine: opened as-is it's "site can't be reached". Links to a running server's port go to
// its WebContainer URL instead; other URLs are returned unchanged.
export function resolveServerLink(uri: string): string {
  let parsed: URL;
  try {
    parsed = new URL(uri);
  } catch {
    return uri;
  }
  if (!['localhost', '127.0.0.1', '0.0.0.0', '[::1]'].includes(parsed.hostname)) return uri;
  const server = openServers.get(Number(parsed.port || (parsed.protocol === 'https:' ? 443 : 80)));
  if (!server) return uri;
  const base = new URL(server);
  base.pathname = parsed.pathname;
  base.search = parsed.search;
  base.hash = parsed.hash;
  return base.toString();
}

// ───────────── Preview: install and run the room's app ─────────────

export type PreviewPhase = 'idle' | 'installing' | 'starting' | 'ready' | 'error';
export interface PreviewState {
  phase: PreviewPhase;
  log: string; // the last few KB of npm output
  url?: string;
  error?: string;
}

let preview: PreviewState = { phase: 'idle', log: '' };
let previewRoom: string | null = null;
let previewGeneration = 0;
let devProcess: WebContainerProcess | null = null;
let installProcess: WebContainerProcess | null = null;
const previewListeners = new Set<(state: PreviewState) => void>();

function setPreview(change: Partial<PreviewState>) {
  preview = { ...preview, ...change };
  previewListeners.forEach(l => l(preview));
}

// npm colours and redraws its progress lines with escape codes; the log shows plain text
const ANSI = /\u001b\[[0-9;?]*[A-Za-z]|\u001b\][^\u0007]*\u0007|\r(?!\n)/g;

function appendLog(chunk: string) {
  const log = (preview.log + chunk.replace(ANSI, '')).slice(-6000);
  setPreview({ log });
}

export function onPreviewState(listener: (state: PreviewState) => void): () => void {
  previewListeners.add(listener);
  listener(preview);
  return () => previewListeners.delete(listener);
}

export function previewRoomId(): string | null {
  return previewRoom;
}

// `npm install`, then `npm run <script>` until it opens a port. Runs once per room per tab; switching
// tabs keeps the server running.
export async function startPreview(roomId: string, fs: RoomFs, script: string): Promise<void> {
  if (previewRoom === roomId && preview.phase !== 'idle' && preview.phase !== 'error') return;
  stopPreview();
  const generation = ++previewGeneration;
  previewRoom = roomId;
  setPreview({ phase: 'installing', log: '', url: undefined, error: undefined });
  try {
    const wc = await attachRoom(roomId, fs);
    if (previewRoom !== roomId || generation !== previewGeneration) return;

    // npm install runs once per set of dependencies: the container may already hold them (same tab,
    // "Try again"), or this browser saved them from an earlier install (any visit, reloads included)
    const packageJson = fs.files.get('package.json')?.content ?? '';
    const key = packageJson ? await snapshotKey(packageJson).catch(() => null) : null;
    if (previewRoom !== roomId || generation !== previewGeneration) return;
    const installedPackage = packageJson ? await installedFor(wc) : null;
    if (previewRoom !== roomId || generation !== previewGeneration) return;
    if (packageJson && installedPackage === packageJson) {
      appendLog('$ npm install (skipped: packages are already installed for this package.json)\n');
    } else if (key && await restoreSnapshot(wc, key, roomId, packageJson)) {
      if (previewRoom !== roomId || generation !== previewGeneration) return;
    } else {
      appendLog('$ npm install\n');
      packagesTouched = true;
      const install = await wc.spawn('npm', ['install']);
      if (previewRoom !== roomId || generation !== previewGeneration) {
        install.kill();
        return;
      }
      installProcess = install;
      void install.output.pipeTo(new WritableStream({ write: chunk => {
        if (generation === previewGeneration) appendLog(chunk);
      } }));
      const installed = await install.exit;
      if (installProcess === install) installProcess = null;
      if (previewRoom !== roomId || generation !== previewGeneration) return;
      if (installed !== 0) throw new Error(`npm install failed (exit code ${installed})`);
      await wc.fs.writeFile(INSTALLED_MARKER, packageJson).catch(() => undefined);
      if (key) await keepSnapshot(wc, key, roomId);
    }

    setPreview({ phase: 'starting' });
    appendLog(`\n$ npm run ${script}\n`);
    const stopListening = onServerReady((_port, url) => {
      if (previewRoom === roomId && generation === previewGeneration && preview.phase === 'starting') setPreview({ phase: 'ready', url });
    });
    const proc = await wc.spawn('npm', ['run', script]);
    if (previewRoom !== roomId || generation !== previewGeneration) {
      stopListening();
      proc.kill();
      return;
    }
    devProcess = proc;
    void proc.output.pipeTo(new WritableStream({ write: chunk => {
      if (generation === previewGeneration) appendLog(chunk);
    } }));
    void proc.exit.then(code => {
      stopListening();
      if (devProcess !== proc || generation !== previewGeneration) return; // stopped or superseded
      devProcess = null;
      // The installed packages may be why it crashed: the next start ("Try again") installs afresh
      // instead of skipping npm install and failing the same way
      void wc.fs.rm(INSTALLED_MARKER, { force: true }).catch(() => undefined);
      if (key) void dropSnapshot(key);
      setPreview({ phase: 'error', url: undefined, error: `npm run ${script} exited (code ${code})` });
    });
  } catch (error) {
    if (previewRoom === roomId && generation === previewGeneration) setPreview({ phase: 'error', error: error instanceof Error ? error.message : String(error) });
  }
}

export function stopPreview() {
  previewGeneration++;
  const proc = devProcess;
  const installer = installProcess;
  devProcess = null;
  installProcess = null;
  proc?.kill();
  installer?.kill();
  setPreview({ phase: 'idle', url: undefined, error: undefined });
}
