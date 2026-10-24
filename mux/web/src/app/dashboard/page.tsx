'use client';

import React, { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { DoorOpen, Trash2, Plus, FolderInput, FolderOpen, FileArchive, Timer, Terminal, ArrowRight, Activity, Zap, PlusCircle, X, RefreshCw, Search, Crown, Users, Sparkles, LogIn, ArrowUpDown } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { getUser, loginHref } from '@/lib/supabase';
import { api, ApiError } from '@/lib/api';
import { STARTER_TEMPLATES } from '@/lib/templates';
import { describeSkipped, importFolder, importFromZip, pickZip, stashImport, type ImportResult } from '@/lib/projectImport';
import { colorForId, getDefaultRole, getPlanWithMe, setPlanWithMe } from '@/lib/preferences';
import { isDemoMode, isSampleRoom } from '@/lib/demo';
import { AppShell, BTN_GHOST, BTN_PRIMARY, CARD, CountUp, CtaCard, FilterChip, IconTile, PageHero, Reveal, Spotlight, useTicker } from '@/components/shell';
import type { Room, User } from '@/types';
import { DeleteRoomDialog } from '@/components/room/DeleteRoomDialog';

// Active Rooms screen (stitch: mux_active_rooms_live_wallpaper_pro)

const HOUR = 3600_000;
const REFRESH_MS = 15_000;

function lastActivity(room: Room): number {
  return new Date(room.updated_at || room.created_at).getTime();
}

type ActivityLevel = 'active' | 'recent' | 'idle';

function activityOf(room: Room): ActivityLevel {
  const age = Date.now() - lastActivity(room);
  if (age < 6 * HOUR) return 'active';
  if (age < 24 * HOUR) return 'recent';
  return 'idle';
}

const ACTIVITY_STYLE: Record<ActivityLevel, { dot: string; chip: string; label: string }> = {
  active: { dot: 'bg-primary', chip: 'bg-primary/20 text-primary', label: 'Active' },
  recent: { dot: 'bg-secondary', chip: 'bg-secondary/20 text-secondary', label: 'Recent' },
  idle: { dot: 'bg-outline', chip: 'bg-surface-container-high text-on-surface-variant', label: 'Idle' },
};

// Tag colors cycle across cards like the stitch category chips
const TAG_STYLES = [
  'bg-secondary text-[#002026]',
  'bg-surface-bright text-on-surface',
  'bg-surface-container-high text-on-surface',
];

export default function DashboardPage() {
  const router = useRouter();

  const [rooms, setRooms] = useState<Room[]>([]);
  const [deleting, setDeleting] = useState<Room | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [showExportPicker, setShowExportPicker] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newRoomDesc, setNewRoomDesc] = useState('');
  const [newRoomRole, setNewRoomRole] = useState<'pm' | 'design' | 'eng'>('pm');
  const [newRoomPassword, setNewRoomPassword] = useState('');
  const [planWithMe, setPlanWithMeState] = useState(true);
  const choosePlanWithMe = (on: boolean) => { setPlanWithMeState(on); setPlanWithMe(on); };
  // Room id to prefill when the join dialog is open (null: closed)
  const [joinRoomId, setJoinRoomId] = useState<string | null>(null);

  useEffect(() => {
    const loadData = async () => {
      try {
        const authUser = await getUser();
        if (!authUser) {
          router.push(loginHref());
          return;
        }

        const userData: User = {
          id: authUser.id,
          email: authUser.email || '',
          name: authUser.user_metadata.full_name || authUser.email?.split('@')[0] || 'User',
          avatar_url: authUser.user_metadata.avatar_url,
          initials: (authUser.user_metadata.full_name || authUser.email || 'U').slice(0, 2).toUpperCase(),
          color: colorForId(authUser.id),
        };
        setUser(userData);
        setNewRoomRole(getDefaultRole());
        setPlanWithMeState(getPlanWithMe());

        const roomsData = await api.listRooms();
        setRooms([...roomsData].sort((a, b) => lastActivity(b) - lastActivity(a)));

        // Links from the landing page and Sandbox open the create dialog, optionally pre-filled
        const params = new URLSearchParams(window.location.search);
        const template = STARTER_TEMPLATES.find(t => t.id === params.get('template'));
        // Sandbox's "describe it" box passes its text along as ?desc=
        if (template) setNewRoomDesc(template.prompt);
        else if (params.get('desc')) setNewRoomDesc(params.get('desc')!.slice(0, 500));
        if (template || params.get('new')) setShowCreate(true);
        else if (params.get('export')) setShowExportPicker(true);
        else if (params.has('join')) setJoinRoomId(params.get('join') ?? '');
      } catch (error) {
        console.error('Failed to load dashboard:', error);
      } finally {
        setLoading(false);
      }
    };

    loadData();
  }, [router]);

  // Keep the rooms list live: re-fetch every 15s while the tab is visible
  const [lastRefresh, setLastRefresh] = useState(() => Date.now());
  const [refreshing, setRefreshing] = useState(false);
  useTicker(1000); // re-render each second for the "Updated Ns ago" label

  useEffect(() => {
    if (loading || !user) return;
    const id = setInterval(async () => {
      if (document.hidden) return;
      setRefreshing(true);
      try {
        const fresh = await api.listRooms();
        setRooms([...fresh].sort((a, b) => lastActivity(b) - lastActivity(a)));
        setLastRefresh(Date.now());
      } catch (error) {
        console.error('Failed to refresh rooms:', error);
      } finally {
        setRefreshing(false);
      }
    }, REFRESH_MS);
    return () => clearInterval(id);
  }, [loading, user]);

  const openCreate = (prompt = '') => {
    setNewRoomDesc(prompt);
    setShowCreate(true);
  };

  // Import project: pick a folder or .zip (needs this click), create a room, hand the files to it
  const [importMenu, setImportMenu] = useState(false);
  const [importing, setImporting] = useState(false);
  const importProject = async (source: 'folder' | 'zip') => {
    setImportMenu(false);
    setImporting(true);
    try {
      let result: ImportResult | null;
      if (source === 'folder') result = await importFolder();
      else {
        const picked = await pickZip();
        result = picked ? await importFromZip(picked[0]) : null;
      }
      if (!result) return; // cancelled
      if (!result.files.length) {
        alert(`No source files found in “${result.name}”. ${describeSkipped(result.skipped)}`);
        return;
      }
      const room = await api.createRoom(`Imported project: ${result.name}`, getDefaultRole());
      stashImport(room.id, { ...result, kickoff: getPlanWithMe() });
      router.push(`/room/${room.id}`);
    } catch (error) {
      console.error('Import failed:', error);
      if (!offerUpgrade(error)) alert(`Import failed: ${(error as Error).message}`);
    } finally {
      setImporting(false);
    }
  };

  // Search + activity filter over the rooms list; "/" focuses search
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<'all' | ActivityLevel>('all');
  const [sort, setSort] = useState<'recent' | 'name' | 'members'>('recent');
  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== '/' || (e.target as HTMLElement)?.matches?.('input, textarea, select')) return;
      e.preventDefault();
      searchRef.current?.focus();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // The server refuses a new room past the plan's room limit (403); offer the Pricing page instead of a bare error
  const offerUpgrade = (error: unknown): boolean => {
    if (!(error instanceof ApiError) || error.status !== 403) return false;
    if (window.confirm(`${error.message}\n\nOpen the Pricing page to upgrade?`)) router.push('/pricing');
    return true;
  };

  const handleCreateRoom = async () => {
    if (!newRoomDesc.trim()) return;
    setCreating(true);
    try {
      const room = await api.createRoom(newRoomDesc.trim(), newRoomRole, newRoomPassword || undefined);
      if (planWithMe) {
        api.kickoff(room.id, newRoomDesc.trim()).catch(error => alert(error instanceof Error ? `Planning didn't start: ${error.message}` : "Planning didn't start"));
      }
      setRooms([room, ...rooms]);
      setNewRoomDesc('');
      setNewRoomPassword('');
      router.push(`/room/${room.id}`);
    } catch (error) {
      console.error('Failed to create room:', error);
      if (!offerUpgrade(error)) alert(error instanceof Error ? `Couldn't create the room: ${error.message}` : "Couldn't create the room");
    } finally {
      setCreating(false);
    }
  };

  if (loading) {
    return (
      <AppShell active="rooms">
        <div className="flex flex-1 items-center justify-center">
          <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-primary" />
        </div>
      </AppShell>
    );
  }

  const liveCount = rooms.filter(r => activityOf(r) !== 'idle').length;
  const memberCount = new Set(rooms.flatMap(r => r.members.map(m => m.user_id))).size;
  const ownedCount = rooms.filter(r => r.owner_id === user?.id).length;
  const q = query.trim().toLowerCase();
  const visible = rooms.filter(
    r => (filter === 'all' || activityOf(r) === filter) && (!q || `${r.title} ${r.description ?? ''}`.toLowerCase().includes(q)),
  );
  // rooms is already newest-activity first; the other orders re-sort a copy
  if (sort === 'name') visible.sort((a, b) => a.title.localeCompare(b.title));
  if (sort === 'members') visible.sort((a, b) => b.members.length - a.members.length || lastActivity(b) - lastActivity(a));
  // Any narrowing or re-ordering drops the featured card so the list reads in the chosen order
  const filtering = filter !== 'all' || q !== '' || sort !== 'recent';
  const [featured, ...others] = filtering ? [undefined, ...visible] : visible;
  const counts: Record<'all' | ActivityLevel, number> = {
    all: rooms.length,
    active: rooms.filter(r => activityOf(r) === 'active').length,
    recent: rooms.filter(r => activityOf(r) === 'recent').length,
    idle: rooms.filter(r => activityOf(r) === 'idle').length,
  };

  const stats = [
    { label: 'Rooms', value: rooms.length, icon: DoorOpen },
    { label: 'Live now', value: liveCount, icon: Activity },
    { label: 'You own', value: ownedCount, icon: Crown },
    { label: 'Collaborators', value: memberCount, icon: Users },
  ];

  const hero = (
    <PageHero
      badge={
        <>
          <span className="relative flex h-2 w-2">
            {liveCount > 0 && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-75" />}
            <span className={`relative inline-flex h-2 w-2 rounded-full ${liveCount > 0 ? 'bg-primary' : 'bg-outline'}`} />
          </span>
          {liveCount} live room{liveCount === 1 ? '' : 's'} · refreshes every 15s
        </>
      }
      title="Your build rooms"
      lead="Jump back into a live session, start something new, or bring an existing project in. Every room keeps its plan, log and checkpoints."
      aside={
        <div className="grid grid-cols-2 gap-space-sm">
          {stats.map((s, i) => (
            <div
              key={s.label}
              className={`group ${CARD} animate-fade-up p-space-md transition-all duration-300 hover:-translate-y-0.5 hover:border-primary/40`}
              style={{ animationDelay: `${120 + i * 80}ms` }}
            >
              <div className="mb-space-sm flex items-center justify-between">
                <span className="font-code text-[11px] uppercase tracking-[0.18em] text-outline">{s.label}</span>
                <IconTile icon={s.icon} size="sm" />
              </div>
              <div className="font-headline text-3xl font-bold tabular-nums text-on-surface">
                <CountUp value={s.value} />
              </div>
            </div>
          ))}
        </div>
      }
    >
      <div className="flex flex-wrap items-center gap-space-sm">
        <button type="button" onClick={() => openCreate()} className={`group ${BTN_PRIMARY}`}>
          <Plus className="h-4 w-4 transition-transform duration-300 group-hover:rotate-90" /> New room
        </button>
        <button type="button" onClick={() => setJoinRoomId('')} className={BTN_GHOST}>
          <LogIn className="h-4 w-4" /> Join room
        </button>
        <div className="relative">
          <button
            type="button"
            onClick={() => setImportMenu(m => !m)}
            disabled={importing}
            aria-haspopup="menu"
            aria-expanded={importMenu}
            className={BTN_GHOST}
          >
            <FolderInput className="h-4 w-4" />
            {importing ? 'Importing…' : 'Import project'}
          </button>
          {importMenu && (
            <div role="menu" className="item-in absolute left-0 top-full z-30 mt-2 w-56 rounded-xl border border-white/10 bg-surface-container p-1 shadow-xl">
              <button type="button" role="menuitem" onClick={() => void importProject('folder')} className="flex w-full items-center gap-space-sm rounded-lg px-space-sm py-space-sm text-left text-body-md text-on-surface hover:bg-white/[0.06]">
                <FolderOpen className="h-4 w-4 text-primary" /> From a folder…
              </button>
              <button type="button" role="menuitem" onClick={() => void importProject('zip')} className="flex w-full items-center gap-space-sm rounded-lg px-space-sm py-space-sm text-left text-body-md text-on-surface hover:bg-white/[0.06]">
                <FileArchive className="h-4 w-4 text-secondary" /> From a .zip file…
              </button>
            </div>
          )}
        </div>
        <label className="flex items-center gap-1.5 text-label-md text-on-surface-variant" title="After creating or importing, read the project, ask a few questions and draft a plan">
          <input type="checkbox" checked={planWithMe} onChange={e => choosePlanWithMe(e.target.checked)} />
          Plan it with me
        </label>
        <Link href="/sandbox" className="flex items-center gap-1.5 px-space-sm text-label-md text-primary hover:underline">
          Browse templates <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>
    </PageHero>
  );

  return (
    <AppShell active="rooms" hero={hero}>
      {rooms.length === 0 ? (
        <EmptyState onCreate={() => openCreate()} />
      ) : (
        <>
          {/* Search + filters */}
          <div className="mb-space-lg flex flex-col gap-space-md md:flex-row md:items-center md:justify-between">
            <div className="group relative w-full md:max-w-sm">
              <div className="pointer-events-none absolute -inset-px rounded-xl bg-gradient-to-r from-primary/60 to-secondary/60 opacity-0 blur transition-opacity duration-300 group-focus-within:opacity-100" />
              <div className="relative flex items-center gap-space-sm rounded-xl border border-white/10 bg-surface-container-lowest px-space-md">
                <Search className="h-4 w-4 text-outline" />
                <input
                  ref={searchRef}
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  onKeyDown={e => e.key === 'Escape' && setQuery('')}
                  placeholder="Search rooms"
                  aria-label="Search rooms"
                  className="min-w-0 flex-1 bg-transparent py-2.5 text-body-md text-on-surface outline-none placeholder:text-outline"
                />
                {query ? (
                  <button type="button" onClick={() => setQuery('')} className="text-outline hover:text-on-surface" aria-label="Clear search"><X className="h-4 w-4" /></button>
                ) : (
                  <kbd className="hidden rounded border border-white/15 bg-white/[0.04] px-1.5 py-0.5 font-code text-[11px] text-on-surface sm:block">/</kbd>
                )}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {(['all', 'active', 'recent', 'idle'] as const).map(f => (
                <FilterChip key={f} on={filter === f} onClick={() => setFilter(f)}>
                  {f === 'all' ? 'All' : ACTIVITY_STYLE[f].label} <span className="tabular-nums text-outline">{counts[f]}</span>
                </FilterChip>
              ))}
              <label className="ml-1 flex items-center gap-1.5 rounded-full border border-white/10 py-0.5 pl-3 pr-1 font-code text-code-sm text-outline transition-colors focus-within:border-primary/50 hover:border-primary/40">
                <ArrowUpDown className="h-3.5 w-3.5" aria-hidden="true" />
                <span className="sr-only">Sort rooms</span>
                <select
                  value={sort}
                  onChange={e => setSort(e.target.value as typeof sort)}
                  className="cursor-pointer rounded-full bg-transparent py-0.5 pr-1 text-on-surface outline-none [&>option]:bg-surface-container"
                >
                  <option value="recent">Recently active</option>
                  <option value="name">Name A–Z</option>
                  <option value="members">Most members</option>
                </select>
              </label>
            </div>
          </div>

          {featured && (
            <Reveal delay={80}>
              <FeaturedRoom room={featured} currentUser={user} onDelete={setDeleting} />
            </Reveal>
          )}

          {others.length > 0 && (
            <div className="mb-space-xl grid grid-cols-1 gap-space-lg md:grid-cols-2 lg:grid-cols-3">
              {others.map((room, i) => (
                <Reveal key={room!.id} delay={Math.min(i, 6) * 70} className="h-full">
                  <RoomCard room={room!} currentUser={user} tagStyle={TAG_STYLES[i % TAG_STYLES.length]} onDelete={setDeleting} />
                </Reveal>
              ))}
            </div>
          )}

          {visible.length === 0 && (
            <div className={`item-in mb-space-xl ${CARD} p-space-xl text-center`}>
              <Search className="mx-auto mb-space-sm h-8 w-8 text-outline" />
              <p className="mb-space-sm text-body-lg text-on-surface">No rooms match{q ? ` “${query}”` : ''}</p>
              <button type="button" onClick={() => { setQuery(''); setFilter('all'); setSort('recent'); }} className="text-body-md text-primary hover:underline">Show all rooms</button>
            </div>
          )}
        </>
      )}

      {/* Activity + quick launch */}
      <div className="grid grid-cols-1 gap-space-lg lg:grid-cols-3">
        <Reveal className="lg:col-span-2">
        <div className={`h-full ${CARD} p-space-lg`}>
          <div className="mb-space-lg flex items-center justify-between gap-space-md">
            <div className="flex items-center gap-space-md">
              <IconTile icon={Activity} size="sm" />
              <div>
                <span className="font-code text-[11px] uppercase tracking-[0.18em] text-outline">Telemetry</span>
                <h3 className="font-headline text-headline-md text-on-surface">Live agent activity</h3>
              </div>
            </div>
            <span className="flex items-center gap-1.5 font-code text-code-sm tabular-nums text-outline" title="Refreshes every 15 seconds">
              <RefreshCw className={`h-3 w-3 ${refreshing ? 'animate-spin text-primary' : ''}`} />
              {(() => {
                const ago = Math.max(0, Math.round((Date.now() - lastRefresh) / 1000));
                return ago < 2 ? 'Updated just now' : `Updated ${ago}s ago`;
              })()}
            </span>
          </div>
          {rooms.length === 0 ? (
            <p className="font-code text-code-md text-outline">No room activity yet. Create a room to see agents at work.</p>
          ) : (
            <div className="relative space-y-space-sm font-code text-code-md">
              <span className="absolute bottom-3 left-[19px] top-3 w-px bg-gradient-to-b from-primary/50 via-secondary/30 to-transparent" aria-hidden="true" />
              {rooms.slice(0, 5).map((room, i) => {
                const activity = activityOf(room);
                const style = ACTIVITY_STYLE[activity];
                return (
                  <Link
                    key={room.id}
                    href={`/room/${room.id}`}
                    style={{ animationDelay: `${i * 70}ms` }}
                    className="item-in group relative flex items-center justify-between gap-space-md rounded-lg px-space-sm py-space-sm transition-all hover:translate-x-1 hover:bg-white/[0.04]"
                  >
                    <div className="flex min-w-0 items-center gap-space-md">
                      <span className="relative grid h-6 w-6 flex-none place-items-center rounded-full bg-surface ring-1 ring-white/10">
                        {activity === 'active' && <span className={`absolute inline-flex h-2 w-2 animate-ping rounded-full opacity-75 ${style.dot}`} />}
                        <span className={`relative inline-flex h-2 w-2 rounded-full ${style.dot}`} />
                      </span>
                      <div className="min-w-0">
                        <div className="truncate font-semibold text-on-surface group-hover:text-primary">
                          {room.title} <span className="font-normal text-outline">/ {room.members.length} member{room.members.length === 1 ? '' : 's'}</span>
                        </div>
                        <div className="truncate text-body-sm text-outline">
                          Last activity {formatDistanceToNow(lastActivity(room), { addSuffix: true })}
                        </div>
                      </div>
                    </div>
                    <span className={`flex-none rounded-full px-space-sm py-0.5 text-code-sm ${style.chip}`}>{style.label}</span>
                  </Link>
                );
              })}
            </div>
          )}
        </div>
        </Reveal>

        <Reveal delay={120}>
        <div className={`flex h-full flex-col justify-between ${CARD} p-space-lg`}>
          <div>
            <div className="mb-space-md flex items-center gap-space-md">
              <IconTile icon={Zap} size="sm" />
              <div>
                <span className="font-code text-[11px] uppercase tracking-[0.18em] text-outline">Templates</span>
                <h3 className="font-headline text-headline-md text-on-surface">Quick launch</h3>
              </div>
            </div>
            <div className="space-y-space-sm">
              {STARTER_TEMPLATES.slice(0, 3).map(template => (
                <button
                  key={template.id}
                  type="button"
                  onClick={() => openCreate(template.prompt)}
                  className="group flex w-full items-center justify-between gap-space-sm rounded-lg border border-white/10 bg-surface/60 p-space-md text-left transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:bg-surface-container"
                >
                  <div className="min-w-0">
                    <div className="font-headline text-headline-sm text-on-surface group-hover:text-primary">{template.name}</div>
                    <div className="truncate text-body-sm text-outline">{template.summary}</div>
                  </div>
                  <PlusCircle className="h-5 w-5 flex-none text-outline transition-transform duration-300 group-hover:rotate-90 group-hover:text-primary" />
                </button>
              ))}
            </div>
          </div>
          <Link href="/sandbox" className="group mt-space-lg flex items-center justify-center gap-1.5 border-t border-white/5 pt-space-md text-body-sm text-primary">
            All {STARTER_TEMPLATES.length} templates in Sandbox <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
          </Link>
        </div>
        </Reveal>
      </div>

      <DeleteRoomDialog
        room={deleting}
        onClose={() => setDeleting(null)}
        onDeleted={id => {
          setRooms(rs => rs.filter(r => r.id !== id));
          setDeleting(null);
        }}
      />

      {showExportPicker && (
        <ExportPicker
          rooms={rooms.filter(r => r.owner_id === user?.id)}
          onCancel={() => setShowExportPicker(false)}
          onCreate={() => { setShowExportPicker(false); openCreate(); }}
        />
      )}

      {showCreate && (
        <CreateRoomDialog
          description={newRoomDesc}
          role={newRoomRole}
          password={newRoomPassword}
          creating={creating}
          onDescriptionChange={setNewRoomDesc}
          onRoleChange={setNewRoomRole}
          onPasswordChange={setNewRoomPassword}
          planWithMe={planWithMe}
          onPlanWithMeChange={choosePlanWithMe}
          onCancel={() => { setShowCreate(false); setNewRoomDesc(''); setNewRoomPassword(''); }}
          onCreate={handleCreateRoom}
        />
      )}

      {joinRoomId !== null && (
        <JoinRoomDialog
          initialRoomId={joinRoomId}
          onCancel={() => setJoinRoomId(null)}
          onJoined={id => router.push(`/room/${id}`)}
        />
      )}
    </AppShell>
  );
}

function Initials({ user, size = 'sm' }: { user: User; size?: 'sm' | 'md' }) {
  const dims = size === 'md' ? 'h-8 w-8 border-2 border-surface' : 'h-6 w-6';
  return (
    <span
      className={`flex flex-none items-center justify-center rounded-full font-code text-[11px] font-bold text-[#0d1117] ${dims}`}
      style={{ background: user.color }}
      title={user.name}
    >
      {user.initials}
    </span>
  );
}

// Only the owner can delete a room; built-in sample rooms (demo mode) can't be deleted
function canDelete(room: Room, user: User | null): boolean {
  return room.owner_id === user?.id && !(isDemoMode() && isSampleRoom(room.id));
}

function DeleteRoomButton({ room, onDelete }: { room: Room; onDelete: (room: Room) => void }) {
  return (
    <button
      type="button"
      onClick={() => onDelete(room)}
      className="grid h-8 w-8 flex-none place-items-center rounded-lg text-outline transition-colors hover:bg-red-500/10 hover:text-red-400"
      aria-label={`Delete ${room.title}`}
      title="Delete room"
    >
      <Trash2 className="h-4 w-4" />
    </button>
  );
}

function FeaturedRoom({ room, currentUser, onDelete }: { room: Room; currentUser: User | null; onDelete: (room: Room) => void }) {
  const activity = activityOf(room);
  const me = room.members.find(m => m.user_id === currentUser?.id);

  return (
    <Spotlight className="group relative mb-space-xl overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-br from-primary/15 via-surface-container/80 to-secondary/10 p-space-xl backdrop-blur transition-colors duration-300 hover:border-primary/40">
      <span className="live-border" aria-hidden="true" />
      <div className="float-slow pointer-events-none absolute -right-16 -top-16 h-64 w-64 rounded-full bg-secondary/20 blur-3xl" />
      <div className="relative z-10 flex flex-col items-start justify-between gap-space-xl lg:flex-row lg:items-center">
        <div className="flex-1">
          <div className="mb-space-sm flex flex-wrap items-center gap-space-md">
            <span className={`flex items-center gap-1.5 rounded-full px-space-sm py-0.5 font-code text-code-sm uppercase tracking-wider ${ACTIVITY_STYLE[activity].chip}`}>
              <Sparkles className="h-3 w-3" /> {activity === 'idle' ? 'Most recent' : 'Active now'}
            </span>
            <span className="flex items-center gap-space-xs font-code text-code-sm text-outline">
              <Timer className="h-3.5 w-3.5" />
              updated {formatDistanceToNow(lastActivity(room), { addSuffix: true })}
            </span>
          </div>
          <h2 className="mb-space-xs font-headline text-3xl font-bold tracking-[-0.03em] text-on-surface md:text-4xl">{room.title}</h2>
          <p className="mb-space-lg max-w-2xl text-body-md text-on-surface-variant">{room.description || 'No description'}</p>
          <div className="flex flex-wrap items-center gap-space-lg">
            <div className="flex -space-x-2">
              {room.members.slice(0, 4).map(m => (
                <Initials key={m.user_id} user={m.user} size="md" />
              ))}
            </div>
            <span className="text-body-sm text-outline">
              {room.members.length} member{room.members.length === 1 ? '' : 's'} •{' '}
              {(room.budget_tokens_cap / 1_000_000).toFixed(1)}M token budget • {room.budget_runs_cap} builds
            </span>
          </div>
        </div>
        <div className="flex w-full flex-col items-stretch gap-space-md sm:flex-row sm:items-center lg:w-auto">
          <div className="rounded-xl border border-white/10 bg-surface/60 p-space-md text-left">
            <div className="font-code text-code-sm uppercase tracking-wider text-outline">Your seat</div>
            <div className="font-code text-code-md font-semibold text-on-surface">
              {me ? `${me.permission[0].toUpperCase()}${me.permission.slice(1)} · ${me.domain_role.toUpperCase()}` : 'Guest'} · {room.members.length}/8 steering
            </div>
          </div>
          <Link
            href={`/room/${room.id}`}
            className="btn-shine flex items-center justify-center gap-space-sm rounded-full bg-primary px-space-xl py-space-md text-label-md font-bold text-white transition-all hover:shadow-[0_0_25px_rgba(59,130,246,0.6)]"
          >
            <Terminal className="h-[18px] w-[18px]" />
            Open workspace
          </Link>
          {canDelete(room, currentUser) && <DeleteRoomButton room={room} onDelete={onDelete} />}
        </div>
      </div>
    </Spotlight>
  );
}

function RoomCard({ room, currentUser, tagStyle, onDelete }: { room: Room; currentUser: User | null; tagStyle: string; onDelete: (room: Room) => void }) {
  const activity = activityOf(room);
  const isOwner = room.owner_id === currentUser?.id;
  const shown = room.members.slice(0, 2);
  const extra = room.members.length - shown.length;

  return (
    <Spotlight className="group flex h-full flex-col justify-between rounded-xl border border-white/10 bg-surface-container/70 p-space-lg backdrop-blur transition-all duration-300 hover:-translate-y-1 hover:border-primary/40 hover:bg-surface-container">
      <div>
        <div className="mb-space-md flex items-start justify-between">
          <span className={`rounded-full px-space-sm py-0.5 font-code text-code-sm ${tagStyle}`}>{isDemoMode() && isSampleRoom(room.id) ? 'Sample' : isOwner ? 'Owner' : 'Shared'}</span>
          <span className="flex items-center gap-1.5 font-code text-[11px] uppercase tracking-[0.15em] text-outline">
            <span className={`flex h-2 w-2 rounded-full ${ACTIVITY_STYLE[activity].dot}`} />
            {ACTIVITY_STYLE[activity].label}
          </span>
        </div>
        <h3 className="mb-space-xs font-headline text-headline-md text-on-surface transition-colors group-hover:text-primary">{room.title}</h3>
        <p className="mb-space-lg line-clamp-2 text-body-md text-on-surface-variant">{room.description || 'No description'}</p>
        <div className="mb-space-lg flex items-center gap-space-sm">
          {shown.map(m => (
            <Initials key={m.user_id} user={m.user} />
          ))}
          {extra > 0 && (
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-surface-bright font-code text-[11px]">+{extra}</span>
          )}
          <span className="text-body-sm text-outline">
            {activity === 'idle' ? 'Idle' : `${room.members.length} member${room.members.length === 1 ? '' : 's'}`}
          </span>
        </div>
      </div>
      <div className="flex items-center justify-between border-t border-white/5 pt-space-md">
        <span className="font-code text-code-sm text-outline">
          {activity === 'idle' ? 'Idle' : 'Updated'} {formatDistanceToNow(lastActivity(room), { addSuffix: true })}
        </span>
        <span className="flex items-center gap-space-xs">
          {canDelete(room, currentUser) && <DeleteRoomButton room={room} onDelete={onDelete} />}
          <Link href={`/room/${room.id}`} className="flex items-center gap-space-xs rounded-lg px-space-sm py-1 text-body-sm text-primary transition-colors hover:bg-primary/10">
            {activity === 'idle' ? 'Reopen' : 'Join room'}
            <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
          </Link>
        </span>
      </div>
    </Spotlight>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="mb-space-xl">
      <CtaCard title="No rooms yet" text="Describe what you want to build and MUX opens a room with a plan, a coder agent and a live preview.">
        <button type="button" onClick={onCreate} className={BTN_PRIMARY}>
          <Plus className="h-4 w-4" /> Create your first room
        </button>
        <Link href="/sandbox" className={BTN_GHOST}>
          <Zap className="h-4 w-4" /> Start from a template
        </Link>
      </CtaCard>
    </div>
  );
}

// Opened from the header's "Export to GitHub": pick one of your rooms, then its export dialog opens
function ExportPicker({ rooms, onCancel, onCreate }: { rooms: Room[]; onCancel: () => void; onCreate: () => void }) {
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center glass-overlay p-4" onClick={onCancel}>
      <div
        className="item-in w-full max-w-md rounded-2xl glass-modal p-space-lg"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="export-picker-title"
      >
        <div className="mb-space-md flex items-center justify-between">
          <h2 id="export-picker-title" className="font-headline text-headline-md">Export a room to GitHub</h2>
          <button type="button" onClick={onCancel} className="rounded-md p-1 text-outline hover:text-on-surface" aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>
        {rooms.length === 0 ? (
          <div className="py-space-md text-center">
            <p className="mb-space-md text-body-md text-on-surface-variant">You don&apos;t own any rooms yet. Only a room&apos;s owner can export it.</p>
            <button type="button" onClick={onCreate} className="rounded-lg bg-primary px-space-md py-space-sm text-label-md font-bold text-on-primary hover:bg-primary/90">
              Create a room
            </button>
          </div>
        ) : (
          <>
            <p className="mb-space-md text-body-sm text-on-surface-variant">Choose which room to export. Its current checkpoint becomes a new repository.</p>
            <div className="space-y-space-sm">
              {rooms.map(room => (
                <Link
                  key={room.id}
                  href={`/room/${room.id}?export=1`}
                  className="group flex items-center justify-between rounded-lg border border-surface-container-highest bg-surface/80 p-space-md transition-all hover:translate-x-1 hover:border-primary/40"
                >
                  <div className="min-w-0">
                    <div className="truncate font-headline text-headline-sm text-on-surface group-hover:text-primary">{room.title}</div>
                    <div className="truncate text-body-sm text-outline">{room.description || 'No description'}</div>
                  </div>
                  <ArrowRight className="h-4 w-4 flex-none text-outline transition-transform group-hover:translate-x-1 group-hover:text-primary" />
                </Link>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

interface CreateRoomDialogProps {
  description: string;
  role: 'pm' | 'design' | 'eng';
  password: string;
  creating: boolean;
  onDescriptionChange: (value: string) => void;
  onRoleChange: (value: 'pm' | 'design' | 'eng') => void;
  onPasswordChange: (value: string) => void;
  planWithMe: boolean;
  onPlanWithMeChange: (on: boolean) => void;
  onCancel: () => void;
  onCreate: () => void;
}

const FIELD_CLASS =
  'w-full rounded-md border border-surface-container-highest bg-bg px-3 py-2 text-body-md text-on-surface outline-none transition-colors focus:border-primary focus:shadow-[0_0_0_3px_rgba(6,182,212,0.15)]';

function CreateRoomDialog({ description, role, password, creating, onDescriptionChange, onRoleChange, onPasswordChange, planWithMe, onPlanWithMeChange, onCancel, onCreate }: CreateRoomDialogProps) {
  const fieldClass = FIELD_CLASS;
  // The server wants at least 4 characters; empty means no password
  const passwordTooShort = password.length > 0 && password.length < 4;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center glass-overlay p-4" onClick={onCancel}>
      <div
        className="w-full max-w-md rounded-2xl glass-modal p-space-lg"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-room-title"
      >
        <div className="mb-space-lg flex items-center justify-between">
          <h2 id="create-room-title" className="font-headline text-headline-md">Create New Room</h2>
          <button type="button" onClick={onCancel} className="rounded-md p-1 text-outline hover:text-on-surface" aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="space-y-space-md">
          <div>
            <label htmlFor="room-desc" className="mb-1 block font-code text-label-md uppercase text-on-surface-variant">
              What do you want to build?
            </label>
            <textarea
              id="room-desc"
              value={description}
              onChange={e => onDescriptionChange(e.target.value)}
              placeholder="e.g., A booking app for yoga classes with Stripe payments"
              className={`${fieldClass} min-h-[100px] resize-y`}
              rows={4}
              autoFocus
            />
          </div>
          <div>
            <label htmlFor="room-role" className="mb-1 block font-code text-label-md uppercase text-on-surface-variant">
              Your role
            </label>
            <select id="room-role" value={role} onChange={e => onRoleChange(e.target.value as 'pm' | 'design' | 'eng')} className={fieldClass}>
              <option value="pm">Product Manager</option>
              <option value="design">Designer</option>
              <option value="eng">Engineer</option>
            </select>
          </div>
          <div>
            <label htmlFor="room-password" className="mb-1 block font-code text-label-md uppercase text-on-surface-variant">
              Room password (optional)
            </label>
            <input
              id="room-password"
              type="password"
              value={password}
              onChange={e => onPasswordChange(e.target.value)}
              placeholder="Lets people join with the Room ID"
              maxLength={128}
              autoComplete="new-password"
              className={fieldClass}
            />
            <p className={`mt-1 text-body-sm ${passwordTooShort ? 'text-error' : 'text-outline'}`}>
              {passwordTooShort ? 'Use at least 4 characters.' : 'You can set or change it later from Share.'}
            </p>
          </div>
          <label className="flex items-start gap-2 text-body-sm text-on-surface-variant">
            <input type="checkbox" className="mt-1" checked={planWithMe} onChange={e => onPlanWithMeChange(e.target.checked)} />
            <span><span className="font-bold text-on-surface">Plan it with me</span> — the agents ask a few questions, then draft a plan for you to approve.</span>
          </label>
          <div className="flex justify-end gap-space-sm border-t border-surface-container-highest pt-space-md">
            <button
              type="button"
              onClick={onCancel}
              className="rounded-lg bg-surface-container-high px-space-md py-space-sm text-label-md text-on-surface transition-colors hover:bg-surface-bright"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onCreate}
              disabled={creating || !description.trim() || passwordTooShort}
              className="rounded-lg bg-primary px-space-md py-space-sm text-label-md font-bold text-on-primary transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {creating ? 'Creating...' : 'Create Room'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

interface JoinRoomDialogProps {
  initialRoomId: string;
  onCancel: () => void;
  onJoined: (roomId: string) => void;
}

// Join with the Room ID and the password the owner set (or, with no password, a room you're invited to)
function JoinRoomDialog({ initialRoomId, onCancel, onJoined }: JoinRoomDialogProps) {
  const [roomId, setRoomId] = useState(initialRoomId);
  const [password, setPassword] = useState('');
  const [joining, setJoining] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const join = async (e: React.FormEvent) => {
    e.preventDefault();
    // Accept a pasted link as well as a bare id
    const id = roomId.trim().split('/room/').pop()?.split(/[?#/]/)[0] ?? '';
    if (!id) return;
    setJoining(true);
    setError(null);
    try {
      await api.joinRoom(id, password ? { password } : {});
      onJoined(id);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 404 ? 'No room with that ID.' : err instanceof Error ? err.message : 'Could not join');
      setJoining(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center glass-overlay p-4" onClick={onCancel}>
      <form
        onSubmit={join}
        className="w-full max-w-md rounded-2xl glass-modal p-space-lg"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="join-room-title"
      >
        <div className="mb-space-lg flex items-center justify-between">
          <h2 id="join-room-title" className="font-headline text-headline-md">Join a room</h2>
          <button type="button" onClick={onCancel} className="rounded-md p-1 text-outline hover:text-on-surface" aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="space-y-space-md">
          <div>
            <label htmlFor="join-room-id" className="mb-1 block font-code text-label-md uppercase text-on-surface-variant">
              Room ID
            </label>
            <input
              id="join-room-id"
              value={roomId}
              onChange={e => setRoomId(e.target.value)}
              placeholder="room_ab12cd34ef56"
              autoFocus={!initialRoomId}
              autoComplete="off"
              spellCheck={false}
              className={`${FIELD_CLASS} font-code`}
            />
          </div>
          <div>
            <label htmlFor="join-room-password" className="mb-1 block font-code text-label-md uppercase text-on-surface-variant">
              Password
            </label>
            <input
              id="join-room-password"
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="From the room's owner"
              autoFocus={Boolean(initialRoomId)}
              maxLength={128}
              autoComplete="off"
              className={FIELD_CLASS}
            />
            <p className="mt-1 text-body-sm text-outline">Leave it empty if you were invited by email or the room is open to anyone with the link.</p>
          </div>
          {error && <p className="text-body-sm text-error" role="alert">{error}</p>}
          <div className="flex justify-end gap-space-sm border-t border-surface-container-highest pt-space-md">
            <button
              type="button"
              onClick={onCancel}
              className="rounded-lg bg-surface-container-high px-space-md py-space-sm text-label-md text-on-surface transition-colors hover:bg-surface-bright"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={joining || !roomId.trim()}
              className="rounded-lg bg-primary px-space-md py-space-sm text-label-md font-bold text-on-primary transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {joining ? 'Joining...' : 'Join Room'}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
