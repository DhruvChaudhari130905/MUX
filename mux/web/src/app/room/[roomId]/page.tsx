'use client';

import React, { useEffect, useState, useRef, useCallback } from 'react';
import { useParams, useRouter } from 'next/navigation';
import {
  TopBar,
  BudgetMeter,
  Presence,
} from '@/components/room';
import { Feed } from '@/components/feed';
import { CenterTabs } from '@/components/center';
import { SidePanel } from '@/components/side';
import { Timeline } from '@/components/timeline';
import { ShaderBackground } from '@/components/shell';
import { api, ApiError } from '@/lib/api';
import { getDemoFiles, isDemoMode, isSampleRoom } from '@/lib/demo';
import { loadRoomFiles, saveRoomFiles } from '@/lib/roomFiles';
import { takeStashedImport } from '@/lib/projectImport';
import { clearLastRoom, getPanelOpen, setLastRoom, setPanelOpen, type RoomPanel } from '@/lib/preferences';
import { PanelRail } from '@/components/room/PanelRail';
import { starterProjectFiles } from '@/lib/starterProject';
import { getSocket } from '@/lib/socket';
import { notifyForEvent } from '@/lib/roomNotifications';
import { NotificationToasts } from '@/components/room/Notifications';
import { notify } from '@/lib/notifications';
import { needsApproval } from '@/lib/fileAccess';
import { reduce } from '@/lib/reducer';
import { supabase, getUser, loginHref } from '@/lib/supabase';
import type { RoomState, Room, User, Message, MessageTo, PlanItem, Conflict, Question, Checkpoint, Membership } from '@/types';
import { format } from 'date-fns';

export default function RoomPage() {
  const params = useParams();
  const router = useRouter();
  const roomId = params.roomId as string;

  const [room, setRoom] = useState<Room | null>(null);
  const [state, setState] = useState<RoomState | null>(null);
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [currentUserMembership, setCurrentUserMembership] = useState<Membership | null>(null);
  const currentUserRef = useRef(currentUser);
  currentUserRef.current = currentUser;
  const [activeTab, setActiveTab] = useState<'preview' | 'code'>('preview');
  // Side columns can be collapsed to give Preview/Code more room; remembered per browser
  const [panels, setPanels] = useState({ feed: true, side: true, timeline: true });
  useEffect(() => {
    setPanels({ feed: getPanelOpen('feed'), side: getPanelOpen('side'), timeline: getPanelOpen('timeline') });
  }, []);
  const togglePanel = useCallback((panel: RoomPanel, open: boolean) => {
    setPanelOpen(panel, open);
    setPanels(p => ({ ...p, [panel]: open }));
  }, []);
  const [activeFile, setActiveFile] = useState<string | null>(null);
  // Which room the files in state belong to; saving waits until they've loaded
  const filesRoom = useRef<string | null>(null);
  const [files, setFiles] = useState<Map<string, { content: string }>>(new Map());
  const [fileVersions, setFileVersions] = useState<Map<string, number>>(new Map());
  const [lockedFiles, setLockedFiles] = useState<Set<string>>(new Set());
  const [lockingUser, setLockingUser] = useState<Map<string, string>>(new Map());
  const [isRewound, setIsRewound] = useState(false);
  const [currentCheckpoint, setCurrentCheckpoint] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // Set when the server won't let this user in (private room, no invite): shown instead of the room
  const [accessError, setAccessError] = useState<string | null>(null);

  const socketRef = useRef<ReturnType<typeof getSocket> | null>(null);

  // Server version of each file whose content is in `files`. file.changed carries only path, hash and
  // version, so any newer version (the coder's edits, a teammate's save) is fetched from the files API.
  const syncedVersions = useRef<{ roomId: string | null; versions: Map<string, number> }>({ roomId: null, versions: new Map() });
  const pullServerFiles = async (forRoom: string, entries: [string, { version: number }][]) => {
    if (isDemoMode()) return;
    const synced = syncedVersions.current;
    if (synced.roomId !== forRoom) return;
    const stale = entries.filter(([path, v]) => synced.versions.get(path) !== v.version);
    stale.forEach(([path, v]) => synced.versions.set(path, v.version)); // claim them so a second event doesn't refetch
    const loaded = await Promise.all(stale.map(async ([path, v]) => {
      try {
        return { path, version: v.version, content: (await api.readFile(forRoom, path)).content };
      } catch (error) {
        console.error(`Could not load ${path}:`, error);
        synced.versions.delete(path);
        return null;
      }
    }));
    if (syncedVersions.current !== synced) return; // left the room meanwhile
    writeLocal(loaded.filter((e): e is NonNullable<typeof e> => e !== null));
  };
  const fileEntries = (files: RoomState['files']): [string, { hash: string; version: number }][] =>
    // files may arrive as a Map or as a plain object after JSON transport
    files instanceof Map ? Array.from(files.entries()) : Object.entries(files ?? {});

  // The room effect runs once per room and calls the latest versions of these (assigned below)
  const roomFns = useRef<{ loadInitialFiles: () => Promise<void>; pullServerFiles: typeof pullServerFiles } | null>(null);

  // Initialize room data
  useEffect(() => {
    let cancelled = false;
    const unsubscribers: (() => void)[] = [];
    const fns = () => roomFns.current!;

    const initRoom = async () => {
      // Nothing from the previous room carries over
      filesRoom.current = null;
      syncedVersions.current = { roomId, versions: new Map() };
      setFiles(new Map());
      setFileVersions(new Map());
      setActiveFile(null);
      try {
        const user = await getUser();
        if (!user) {
          router.replace(loginHref());
          return;
        }

        const userData: User = {
          id: user.id,
          email: user.email || '',
          name: user.user_metadata.full_name || user.email?.split('@')[0] || 'User',
          avatar_url: user.user_metadata.avatar_url,
          initials: (user.user_metadata.full_name || user.email || 'U').slice(0, 2).toUpperCase(),
          color: `hsl(${Math.random() * 360}, 70%, 60%)`,
        };
        setCurrentUser(userData);

        // Joining first is what lets invited people (their email), shared-link visitors and returning members in
        setAccessError(null);
        if (!isDemoMode()) await api.joinRoom(roomId, { user_name: userData.name });

        // Fetch room data
        const roomData = await api.getRoom(roomId);
        setRoom(roomData);
        setLastRoom(roomData);

        const membership = roomData.members.find(m => m.user_id === user.id);
        if (membership) {
          setCurrentUserMembership(membership);
        }

        // Connect to WebSocket
        const socket = getSocket(roomId);
        socketRef.current = socket;

        await socket.connect();

        // Subscribe to state updates
        // Paths the server had at the last state update: one that disappears was deleted there (by a
        // teammate or the coder). Files that only exist in this browser are left alone.
        let serverPaths = new Set<string>();
        const unsubState = socket.onState((newState) => {
          setState(newState);
          const entries = fileEntries(newState.files);
          const paths = new Set(entries.map(([path]) => path));
          const gone = Array.from(serverPaths).filter(path => !paths.has(path));
          serverPaths = paths;
          gone.forEach(path => syncedVersions.current.versions.delete(path));
          setFiles(prev => {
            const next = new Map(prev);
            gone.forEach(path => next.delete(path));
            for (const [path] of entries) {
              if (!next.has(path)) next.set(path, { content: '' });
            }
            return next;
          });
          setFileVersions(new Map(entries.map(([path, v]) => [path, v.version])));
          void fns().pullServerFiles(roomId, entries);
        });

        const unsubPresence = socket.onPresence((presence) => {
          setState(prev => prev ? { ...prev, presence } : null);
        });
        unsubscribers.push(unsubState, unsubPresence);
        if (cancelled) return; // left the room while connecting; cleanup below unsubscribes

        // Load initial files (starter template)
        await fns().loadInitialFiles();
        // The browser's saved copy may be older than the server's: fetch every server file again
        syncedVersions.current = { roomId, versions: new Map() };
        const current = socket.getState();
        if (current) void fns().pullServerFiles(roomId, fileEntries(current.files));

        setLoading(false);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          // A stale link (old bookmark or the header's last-room pill): forget it and go back quietly
          clearLastRoom(roomId);
          router.replace('/dashboard');
          return;
        }
        if (error instanceof ApiError && (error.status === 403 || error.status === 429)) {
          setAccessError(error.message);
          return;
        }
        console.error('Failed to load room:', error);
        router.push('/dashboard');
      }
    };

    initRoom();

    return () => {
      cancelled = true;
      unsubscribers.forEach(unsubscribe => unsubscribe());
      if (socketRef.current) {
        socketRef.current.disconnect();
      }
    };
  }, [roomId, router]);

  // A room opens with the files it had last time in this browser. New rooms start empty (the Explorer offers
  // import, the starter template or a new file); sample demo rooms start from the starter plus their own files.
  // TODO: load from the backend files API once it exists.
  const loadInitialFiles = async () => {
    let initial = await loadRoomFiles(roomId);
    if (!initial) {
      initial = new Map();
      if (isDemoMode() && isSampleRoom(roomId)) {
        initial = starterProjectFiles();
        Object.entries(getDemoFiles(roomId)).forEach(([path, content]) => initial!.set(path, { content }));
      }
    }

    // A project picked on the dashboard ("Import project") replaces the empty room's files
    const imported = takeStashedImport(roomId);
    if (imported) initial = new Map(imported.files.map(f => [f.path, { content: f.content }]));

    setFiles(initial);
    setFileVersions(new Map(Array.from(initial.keys(), path => [path, 1])));
    setActiveFile(['README.md', 'src/App.tsx', 'package.json', 'index.html'].find(p => initial!.has(p)) ?? null);
    filesRoom.current = roomId;
    if (imported) {
      void saveRoomFiles(roomId, initial);
      // The agents work from the server's copy, so the project goes there too (it used to stay in this browser
      // only, and the coordinator and coder never saw it). No base version: it replaces the empty room's files.
      const saves = imported.files.map(f =>
        api.saveFile(roomId, f.path, f.content, null)
          .then(({ version, held }) => (held ? restoreHeld(f.path) : writeLocal([{ path: f.path, content: f.content, version: version ?? 0 }])))
          .catch(error => console.error(`Could not save ${f.path} to the room:`, error)),
      );
      // "Plan it with me": start once the agents can read every file
      if (imported.kickoff) {
        void Promise.allSettled(saves).then(() => api.kickoff(roomId))
          .catch(error => alert(error instanceof Error ? `Planning didn't start: ${error.message}` : "Planning didn't start"));
      }
    }
  };
  roomFns.current = { loadInitialFiles, pullServerFiles };

  // Keep this room's files in the browser so they're still here next time. Saves are batched, but a
  // pending save is flushed (never dropped) when leaving the room or hiding/closing the tab.
  const pendingSave = useRef<(() => void) | null>(null);
  useEffect(() => {
    if (filesRoom.current !== roomId) return;
    const snapshot = files;
    const flush = () => {
      clearTimeout(t);
      if (pendingSave.current === flush) pendingSave.current = null;
      void saveRoomFiles(roomId, snapshot);
    };
    const t = setTimeout(flush, 400);
    pendingSave.current = flush;
    return () => clearTimeout(t);
  }, [files, roomId]);
  useEffect(() => {
    const flushNow = () => pendingSave.current?.();
    const onHide = () => { if (document.visibilityState === 'hidden') flushNow(); };
    window.addEventListener('pagehide', flushNow);
    document.addEventListener('visibilitychange', onHide);
    return () => {
      flushNow(); // leaving the room
      window.removeEventListener('pagehide', flushNow);
      document.removeEventListener('visibilitychange', onHide);
    };
  }, [roomId]);

  // Photos are uploaded first, then sent with the note; a failure is shown in the composer
  const handleSendMessage = useCallback(async (text: string, to: MessageTo, photos: File[] = []) => {
    if (!roomId || !currentUser) return;
    const ids = await Promise.all(photos.map(p => api.uploadImage(roomId, p).then(r => r.id)));
    await api.sendMessage(roomId, text, to, ids);
    // Message will appear via WebSocket
  }, [roomId, currentUser]);

  // Latest files for callbacks that need to read them (rename/delete/upload)
  const filesRef = useRef(files);
  filesRef.current = files;

  const handleFileSelect = useCallback((path: string | null) => {
    setActiveFile(path);
  }, []);

  // Put files into local state (content + version) immediately; the server write follows
  const writeLocal = useCallback((entries: { path: string; content: string; version: number }[]) => {
    entries.forEach(e => syncedVersions.current.versions.set(e.path, e.version));
    setFiles(prev => {
      const next = new Map(prev);
      entries.forEach(e => next.set(e.path, { content: e.content }));
      return next;
    });
    setFileVersions(prev => {
      const next = new Map(prev);
      entries.forEach(e => next.set(e.path, e.version));
      return next;
    });
  }, []);

  const removeLocal = useCallback((paths: string[]) => {
    setFiles(prev => {
      const next = new Map(prev);
      paths.forEach(p => next.delete(p));
      return next;
    });
    setFileVersions(prev => {
      const next = new Map(prev);
      paths.forEach(p => next.delete(p));
      return next;
    });
  }, []);

  // A change outside your folders went for approval (lib/fileAccess.ts): say so, and show the room's copy again
  const restoreHeld = useCallback(async (path: string) => {
    notify({
      category: 'decisions',
      title: 'Sent for approval',
      body: `${path} is outside your folders. The owner or a coder of that area will approve or reject it.`,
    });
    // The local copy was written ahead of the server, so the room state (file.changed events) has the real version
    const files = stateRef.current?.files;
    const entry = files instanceof Map ? files.get(path) : (files as Record<string, { version: number }> | undefined)?.[path];
    if (!entry) {
      syncedVersions.current.versions.delete(path);
      removeLocal([path]);
      return;
    }
    try {
      writeLocal([{ path, content: (await api.readFile(roomId, path)).content, version: entry.version }]);
    } catch (error) {
      console.error(`Could not reload ${path}:`, error);
    }
  }, [roomId, writeLocal, removeLocal]);

  const handleFileSave = useCallback(async (path: string, content: string, baseVersion: number) => {
    if (!roomId) return;
    try {
      const { held } = await api.saveFile(roomId, path, content, baseVersion);
      if (held) {
        await restoreHeld(path);
        return;
      }
      writeLocal([{ path, content, version: baseVersion + 1 }]);
    } catch (error) {
      console.error('Failed to save file:', error);
      alert(`Failed to save ${path}: ${error instanceof Error ? error.message : 'the server could not be reached'}`);
      throw error; // keep the unsaved draft in the editor
    }
  }, [roomId, writeLocal, restoreHeld]);

  const handleCreateFile = useCallback((path: string, content = starterContent(path), open = true) => {
    writeLocal([{ path, content, version: 1 }]);
    if (open) setActiveFile(path);
    api.saveFile(roomId, path, content, 0)
      .then(({ held }) => { if (held) void restoreHeld(path); })
      .catch(error => console.error('Failed to create file:', error));
  }, [roomId, writeLocal, restoreHeld]);

  const handleUploadFiles = useCallback((uploaded: { path: string; content: string }[]) => {
    if (!uploaded.length) return;
    writeLocal(uploaded.map(f => ({ ...f, version: (syncedVersions.current.versions.get(f.path) ?? 0) + 1 })));
    setActiveFile(uploaded[uploaded.length - 1].path);
    // Uploads and imports replace files of the same name, so no base version (0 would mean "new file" and
    // the server refuses it for every file that already exists). Keep the version the server assigns.
    uploaded.forEach(f =>
      api.saveFile(roomId, f.path, f.content, null)
        .then(({ version, held }) => (held ? restoreHeld(f.path) : writeLocal([{ ...f, version: version ?? 0 }])))
        .catch(error => console.error(`Could not save ${f.path} to the room:`, error)),
    );
  }, [roomId, writeLocal, restoreHeld]);

  const handleDeleteFile = useCallback((path: string, isDirectory: boolean) => {
    const targets = isDirectory
      ? Array.from(filesRef.current.keys()).filter(p => p.startsWith(path + '/'))
      : [path];
    removeLocal(targets);
    targets.forEach(p => api.deleteFile(roomId, p)
      .then(result => { if (result?.held) void restoreHeld(p); })
      .catch(error => console.error('Failed to delete file:', error)));
  }, [roomId, removeLocal, restoreHeld]);

  const handleRenameFile = useCallback((from: string, to: string, isDirectory: boolean) => {
    const moves = (isDirectory
      ? Array.from(filesRef.current.keys()).filter(p => p.startsWith(from + '/'))
      : [from]
    ).map(oldPath => ({ oldPath, newPath: to + oldPath.slice(from.length), content: filesRef.current.get(oldPath)?.content ?? '' }));
    // An approval covers one file, so a rename outside your folders would become a "create" and a "delete"
    // decided separately (leaving copies behind). Ask instead.
    const live = stateRef.current?.room;
    const me = currentUserRef.current?.id;
    if (live && me && moves.some(m => needsApproval(live, me, m.oldPath) || needsApproval(live, me, m.newPath))) {
      notify({
        category: 'decisions',
        tone: 'err',
        title: 'Rename needs the owner',
        body: `${from} or ${to} is outside your folders. Ask the owner or a coder of that area to rename it.`,
      });
      return;
    }
    writeLocal(moves.map(m => ({ path: m.newPath, content: m.content, version: 1 })));
    removeLocal(moves.map(m => m.oldPath));
    setActiveFile(current => moves.find(m => m.oldPath === current)?.newPath ?? current);
    // A rename is "save the new path, then delete the old one": if either half went for approval, the old
    // file must stay (deleting it after a held save would lose the file)
    moves.forEach(m =>
      api.saveFile(roomId, m.newPath, m.content, 0)
        .then(async ({ held }) => {
          if (held) {  // the folders changed after the check above: keep the old file
            await restoreHeld(m.newPath);
            const files = stateRef.current?.files;
            const entry = files instanceof Map ? files.get(m.oldPath) : undefined;
            if (entry) writeLocal([{ path: m.oldPath, content: m.content, version: entry.version }]);
            return;
          }
          const removed = await api.deleteFile(roomId, m.oldPath);
          if (removed?.held) await restoreHeld(m.oldPath);
        })
        .catch(error => console.error('Failed to rename file:', error)),
    );
  }, [roomId, writeLocal, removeLocal, restoreHeld]);

  const handleVote = useCallback(async (conflictId: string, option: string) => {
    if (!roomId) return;
    try {
      await api.voteConflict(roomId, conflictId, option);
    } catch (error) {
      console.error('Failed to vote:', error);
    }
  }, [roomId]);

  const handleOverride = useCallback(async (conflictId: string, option: string) => {
    if (!roomId) return;
    try {
      await api.overrideConflict(roomId, conflictId, option);
    } catch (error) {
      console.error('Failed to override:', error);
    }
  }, [roomId]);

  const handleAnswer = useCallback(async (questionId: string, answer: string) => {
    if (!roomId) return;
    try {
      await api.answerQuestion(roomId, questionId, answer);
    } catch (error) {
      console.error('Failed to answer:', error);
    }
  }, [roomId]);

  const handlePlanUpdate = useCallback(async (items: PlanItem[]) => {
    if (!roomId) return;
    try {
      await api.updatePlan(roomId, items);
    } catch (error) {
      console.error('Failed to update plan:', error);
      alert(`Could not save the plan: ${error instanceof Error ? error.message : 'please try again'}`);
    }
  }, [roomId]);

  const handleKickoff = useCallback(async () => {
    try {
      await api.kickoff(roomId);
    } catch (error) {
      alert(error instanceof Error ? `Planning didn't start: ${error.message}` : "Planning didn't start");
    }
  }, [roomId]);

  const handlePlanApprove = useCallback(async () => {
    if (!roomId) return;
    try {
      await api.approvePlan(roomId);
    } catch (error) {
      console.error('Failed to approve plan:', error);
      alert(`Could not approve the plan: ${error instanceof Error ? error.message : 'please try again'}`);
    }
  }, [roomId]);

  const handlePlanRun = useCallback(async () => {
    if (!roomId) return;
    try {
      const { retried } = await api.runPlan(roomId);
      if (!retried) alert('Nothing to run: the stopped tasks are waiting for an answer in Decisions.');
    } catch (error) {
      console.error('Failed to run plan:', error);
      alert(`Could not run the plan: ${error instanceof Error ? error.message : 'please try again'}`);
    }
  }, [roomId]);

  const handleRewind = useCallback(async (checkpointId: string) => {
    if (!roomId) return;
    try {
      await api.rewind(roomId, checkpointId);
      setIsRewound(true);
      setCurrentCheckpoint(checkpointId);
    } catch (error) {
      console.error('Failed to rewind:', error);
    }
  }, [roomId]);

  // Live events (not the history replayed on connect) become notifications
  const stateRef = useRef(state);
  stateRef.current = state;
  useEffect(() => {
    const socket = socketRef.current;
    if (!socket || !room || !currentUser) return;
    const unsubscribe = socket.onEvent(event => {
      notifyForEvent(event, {
        roomTitle: room.title,
        currentUser,
        nameOf: id => room.members.find(m => m.user_id === id)?.user?.name ?? (id ? id.charAt(0).toUpperCase() + id.slice(1) : 'Someone'),
        planItem: id => stateRef.current?.plan.find(p => p.id === id),
        changeRequest: id => stateRef.current?.room.change_requests?.find(r => r.id === id),
        focusSidePanel: () => togglePanel('side', true),
      });
    });
    return () => { unsubscribe(); };
  }, [room, currentUser, togglePanel]);

  const handleReturnToLatest = useCallback(() => {
    setIsRewound(false);
    setCurrentCheckpoint(null);
  }, []);

  if (accessError) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--bg)] p-4">
        <div className="w-full max-w-sm rounded-xl border border-[var(--line)] bg-[var(--panel)] p-6 text-center">
          <h1 className="mb-2 text-lg font-semibold">You don&apos;t have access to this room</h1>
          <p className="mb-1 text-sm text-[var(--muted)]">{accessError}</p>
          <p className="mb-5 text-sm text-[var(--muted)]">
            Ask the owner to invite your email, or join from the dashboard with the room ID and password.
          </p>
          <p className="mb-5 font-mono text-xs text-[var(--muted)]">Room ID: {roomId}</p>
          <button type="button" className="btn primary" onClick={() => router.push(`/dashboard?join=${encodeURIComponent(roomId)}`)}>
            Join with a password
          </button>
        </div>
      </div>
    );
  }

  if (loading || !room || !state || !currentUser || !currentUserMembership) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--bg)]">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[var(--coord)]" />
      </div>
    );
  }

  const openConflicts = state.conflicts.filter(c => c.status === 'open' || c.status === 'voting');
  const openQuestions = state.questions.filter(q => q.status === 'open');
  const myChanges = (state.room.change_requests ?? [])
    .filter(r => r.status === 'pending' && (r.approvers.includes(currentUser.id) || r.requested_by === currentUser.id));

  const presenceData = state.presence.map(p => ({
    user: p.user,
    active: p.active,
    typing: !!p.typing,
  }));

  const typingUser = state.presence.find(p => p.typing)?.user.id;

  return (
    <>
      <ShaderBackground className="shader-bg" />
      <div className="app h-screen">
        <TopBar
          room={room}
          currentUser={currentUser}
          currentUserMembership={currentUserMembership}
          budget={state.budget}
          presence={presenceData}
        />

        <main className={`main${panels.feed ? '' : ' feed-closed'}${panels.side ? '' : ' side-closed'}`}>
          {/* Left: Agent Feed */}
          {!panels.feed && <PanelRail side="left" label="Feed" onOpen={() => togglePanel('feed', true)} />}
          <Feed
            roomId={roomId}
            canReact={currentUserMembership.permission !== 'viewer'}
            memberNames={Object.fromEntries(room.members.map(m => [m.user_id, m.user.name]))}
            collapsed={!panels.feed}
            onCollapse={() => togglePanel('feed', false)}
            messages={state.messages}
            activity={state.activity}
            currentUser={currentUser}
            onSendMessage={handleSendMessage}
            canPostTeam={currentUserMembership.permission !== 'viewer'}
            activeConflict={openConflicts[0] ? { id: openConflicts[0].id, taskId: openConflicts[0].task_id, options: openConflicts[0].options } : undefined}
            activeQuestion={openQuestions[0] ? { id: openQuestions[0].id, taskId: openQuestions[0].task_id } : undefined}
          />

          {/* Center: Preview / Code */}
          <CenterTabs
            key={roomId}
            roomId={roomId}
            roomTitle={room.title}
            roomDescription={room.description}
            plan={state.plan}
            checkpointCount={state.checkpoints.length}
            activeTab={activeTab}
            onTabChange={setActiveTab}
            files={files}
            activeFile={activeFile}
            onFileSelect={handleFileSelect}
            onFileEdit={handleFileSave}
            onCreateFile={handleCreateFile}
            onDeleteFile={handleDeleteFile}
            onRenameFile={handleRenameFile}
            onUploadFiles={handleUploadFiles}
            fileVersions={fileVersions}
            lockedFiles={lockedFiles}
            lockingUser={lockingUser}
            needsApproval={(path, isDirectory) => needsApproval(state.room, currentUser.id, path, isDirectory)}
          />

          {/* Right: Cards + Plan */}
          {!panels.side && (
            <PanelRail side="right" label="Decisions & plan" badge={openConflicts.length + openQuestions.length + myChanges.length} onOpen={() => togglePanel('side', true)} />
          )}
          <SidePanel
            collapsed={!panels.side}
            onCollapse={() => togglePanel('side', false)}
            state={state}
            roomId={roomId}
            nameOf={id => room.members.find(m => m.user_id === id)?.user?.name ?? id}
            currentUserRole={currentUserMembership.permission}
            currentUserDomainRole={currentUserMembership.domain_role}
            onVote={handleVote}
            onOverride={handleOverride}
            onAnswer={handleAnswer}
            onPlanUpdate={handlePlanUpdate}
            onPlanApprove={handlePlanApprove}
            onPlanRun={handlePlanRun}
            onKickoff={handleKickoff}
          />
        </main>
        <NotificationToasts />

        <Timeline
          checkpoints={state.checkpoints}
          currentCheckpoint={currentCheckpoint}
          onSelectCheckpoint={handleRewind}
          onReturnToLatest={handleReturnToLatest}
          isRewound={isRewound}
          collapsed={!panels.timeline}
          onToggle={open => togglePanel('timeline', open)}
        />
      </div>
    </>
  );
}

// Sensible first contents for a new file, by extension
function starterContent(path: string): string {
  const file = path.split('/').pop() || '';
  const base = file.replace(/\.[^.]+$/, '');
  const component = base.replace(/(^|[-_ ])(\w)/g, (_, __, c: string) => c.toUpperCase()).replace(/[^A-Za-z0-9]/g, '') || 'Component';
  if (/\.(tsx|jsx)$/.test(file)) return `export function ${component}() {\n  return <div>${component}</div>;\n}\n`;
  if (/\.(ts|js)$/.test(file)) return `export {};\n`;
  if (file.endsWith('.json')) return '{\n}\n';
  if (file.endsWith('.css')) return `/* ${file} */\n`;
  if (file.endsWith('.md')) return `# ${base}\n`;
  if (file.endsWith('.html')) return '<!DOCTYPE html>\n<html lang="en">\n  <head>\n    <meta charset="UTF-8" />\n  </head>\n  <body></body>\n</html>\n';
  return '';
}
