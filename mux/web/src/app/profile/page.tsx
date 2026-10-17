'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { formatDistanceToNow, format } from 'date-fns';
import { DoorOpen, Users, Coins, Crown, Github, LogOut, ArrowRight, Check, Mail, Sparkles, Plus, Briefcase, Palette, Code2, CalendarDays, ShieldCheck } from 'lucide-react';
import { getUser, loginHref, signOut } from '@/lib/supabase';
import { isDemoMode } from '@/lib/demo';
import { api } from '@/lib/api';
import { colorForId, getDefaultRole, setDefaultRole } from '@/lib/preferences';
import { AppShell, CountUp, Reveal, Spotlight, trackHeroSpot } from '@/components/shell';
import type { DomainRole, Room, User, UserPlan } from '@/types';

// Profile screen. No stitch screen exists for it; it follows the Active Rooms layout.

const FREE_ROOM_LIMIT = 3;

const ROLE_OPTIONS: { value: DomainRole; label: string; detail: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { value: 'pm', label: 'Product Manager', detail: 'Your vote counts 2× on scope conflicts', icon: Briefcase },
  { value: 'design', label: 'Designer', detail: 'Your vote counts 2× on UI conflicts', icon: Palette },
  { value: 'eng', label: 'Engineer', detail: 'Your vote counts 2× on architecture conflicts', icon: Code2 },
];

// A room counts as live if something happened in it in the last three hours
const LIVE_WINDOW_MS = 3 * 3600_000;

const PLAN_PRICE: Record<string, string> = { free: '$0 / month', pro: '$20 / user / month', enterprise: 'Custom' };

function formatCap(n: number): string {
  return n >= 1_000_000 ? `${n / 1_000_000}M` : n.toLocaleString();
}

// Radial meter for the plan's room limit; a full ring with ∞ when the plan has none
function UsageRing({ value, max, ready }: { value: number; max: number | null; ready: boolean }) {
  const r = 30;
  const c = 2 * Math.PI * r;
  const pct = max === null ? 1 : Math.min(1, value / max);
  const full = max !== null && value >= max;
  return (
    <div className="relative h-20 w-20 flex-none">
      <svg viewBox="0 0 72 72" className="h-full w-full -rotate-90" aria-hidden="true">
        <defs>
          <linearGradient id="usage-ring" x1="0" x2="1">
            <stop offset="0%" stopColor="#3b82f6" />
            <stop offset="100%" stopColor="#06b6d4" />
          </linearGradient>
        </defs>
        <circle cx="36" cy="36" r={r} fill="none" stroke="rgba(255,255,255,.08)" strokeWidth="6" />
        <circle
          cx="36" cy="36" r={r} fill="none" strokeWidth="6" strokeLinecap="round"
          stroke={full ? '#f85149' : 'url(#usage-ring)'}
          strokeDasharray={c}
          strokeDashoffset={ready ? c * (1 - pct) : c}
          className="transition-[stroke-dashoffset] duration-1000 ease-out"
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-center">
        {max === null ? (
          <span className="font-headline text-2xl font-bold leading-none text-on-surface">∞</span>
        ) : (
          <span className="font-headline text-lg font-bold leading-none text-on-surface">{value}<span className="text-sm text-outline">/{max}</span></span>
        )}
      </div>
    </div>
  );
}

function lastActivity(room: Room): number {
  return new Date(room.updated_at || room.created_at).getTime();
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(n);
}

export default function ProfilePage() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [provider, setProvider] = useState('email');
  const [joinedAt, setJoinedAt] = useState<string | null>(null);
  const [rooms, setRooms] = useState<Room[]>([]);
  const [loading, setLoading] = useState(true);
  const [defaultRole, setDefaultRoleState] = useState<DomainRole>('pm');
  const [savedRole, setSavedRole] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [avatarFailed, setAvatarFailed] = useState(false);
  const [plan, setPlan] = useState<UserPlan | null>(null);
  // Set when GitHub's connect flow returns here (?github=connected&username=… or ?github=error&message=…)
  const [githubResult, setGithubResult] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const result = params.get('github');
    if (!result) return;
    setGithubResult(result === 'connected'
      ? { ok: true, text: `GitHub connected as @${params.get('username') ?? 'you'}. You can export rooms now.` }
      : { ok: false, text: params.get('message') ?? 'GitHub connection failed.' });
    window.history.replaceState(null, '', window.location.pathname);
  }, []);
  const [barReady, setBarReady] = useState(false); // lets the plan bar grow from 0 after first paint

  useEffect(() => {
    const load = async () => {
      try {
        const authUser = await getUser();
        if (!authUser) {
          router.push(loginHref());
          return;
        }
        const name = authUser.user_metadata.full_name || authUser.email?.split('@')[0] || 'User';
        setUser({
          id: authUser.id,
          email: authUser.email || '',
          name,
          avatar_url: authUser.user_metadata.avatar_url,
          initials: name.split(' ').map((p: string) => p[0]).join('').slice(0, 2).toUpperCase(),
          color: colorForId(authUser.id),
        });

        // Supabase users carry provider and signup time; the demo user has neither
        const meta = authUser as { app_metadata?: { provider?: string }; created_at?: string };
        setProvider(isDemoMode() ? 'demo' : meta.app_metadata?.provider || 'email');
        setJoinedAt(meta.created_at ?? null);
        setDefaultRoleState(getDefaultRole());

        setRooms(await api.listRooms());
        setPlan(await api.getPlan().catch(() => null));
        requestAnimationFrame(() => setTimeout(() => setBarReady(true), 150));
      } catch (error) {
        console.error('Failed to load profile:', error);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [router]);

  const handleSignOut = async () => {
    await signOut();
    router.push('/login');
  };

  const handleRoleChange = (role: DomainRole) => {
    setDefaultRoleState(role);
    setDefaultRole(role);
    setSavedRole(true);
    setTimeout(() => setSavedRole(false), 1500);
  };

  const handleConnectGitHub = async () => {
    setConnecting(true);
    try {
      const { url } = await api.connectGitHub();
      window.location.href = url;
    } catch (error) {
      console.error('Failed to connect GitHub:', error);
      alert('Could not start the GitHub connection. Is the backend running?');
      setConnecting(false);
    }
  };

  if (loading || !user) {
    return (
      <AppShell active="profile">
        <div className="flex flex-1 items-center justify-center">
          <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-primary" />
        </div>
      </AppShell>
    );
  }

  const owned = rooms.filter(r => r.owner_id === user.id);
  const shared = rooms.filter(r => r.owner_id !== user.id);
  const collaborators = new Map<string, User>();
  rooms.forEach(r => r.members.forEach(m => { if (m.user_id !== user.id) collaborators.set(m.user_id, m.user); }));
  const tokenBudget = owned.reduce((sum, r) => sum + r.budget_tokens_cap, 0);
  const roomLimit = plan ? plan.room_limit : FREE_ROOM_LIMIT;
  const sortedRooms = [...rooms].sort((a, b) => lastActivity(b) - lastActivity(a));

  const sharedCount = new Map<string, number>();
  rooms.forEach(r => r.members.forEach(m => { if (m.user_id !== user.id) sharedCount.set(m.user_id, (sharedCount.get(m.user_id) ?? 0) + 1); }));
  const people = Array.from(collaborators.values()).sort((a, b) => (sharedCount.get(b.id) ?? 0) - (sharedCount.get(a.id) ?? 0));
  const liveCount = rooms.filter(r => Date.now() - lastActivity(r) < LIVE_WINDOW_MS).length;
  const tint = (alpha: number) => `color-mix(in srgb, ${user.color} ${alpha}%, transparent)`;

  const stats = [
    { icon: Crown, label: 'Rooms you own', value: owned.length },
    { icon: DoorOpen, label: 'Shared with you', value: shared.length },
    { icon: Users, label: 'People you build with', value: collaborators.size },
    { icon: Coins, label: 'Token budget', value: formatTokens(tokenBudget) },
  ];

  return (
    <AppShell active="profile">
      {/* Cover + identity */}
      <section
        onPointerMove={trackHeroSpot}
        className="hero-in relative isolate mb-space-xl overflow-hidden rounded-3xl border border-white/10"
      >
        <div className="relative h-40 md:h-48" aria-hidden="true">
          <div className="absolute inset-0" style={{ background: `linear-gradient(120deg, ${tint(55)}, rgba(59,130,246,.35) 45%, rgba(163,113,247,.30))` }} />
          <div className="aurora absolute inset-0 opacity-80" />
          <div className="hero-grid absolute inset-0 opacity-60" />
          <div className="hero-spot absolute inset-0" />
          <div className="absolute inset-x-0 bottom-0 h-24 bg-gradient-to-b from-transparent to-[rgba(13,17,23,.9)]" />
        </div>

        <div className="glass-card relative -mt-px px-space-lg pb-space-lg md:px-space-xl md:pb-space-xl">
          <div className="flex flex-col gap-space-lg md:flex-row md:items-end">
            {/* Avatar overlapping the cover */}
            <div className="relative -mt-14 h-28 w-28 flex-none md:-mt-16 md:h-32 md:w-32">
              <span className="profile-ring absolute -inset-1.5 rounded-full" style={{ ['--ring' as string]: user.color }} aria-hidden="true" />
              <div
                className="relative grid h-full w-full place-items-center overflow-hidden rounded-full border-4 border-[#0d1117] font-headline text-4xl font-bold text-[#0d1117] shadow-[0_20px_50px_-15px_rgba(0,0,0,.9)]"
                style={{ background: user.color }}
              >
                {user.avatar_url && !avatarFailed ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={user.avatar_url} alt="" referrerPolicy="no-referrer" onError={() => setAvatarFailed(true)} className="h-full w-full object-cover" />
                ) : (
                  user.initials
                )}
              </div>
              <span className="absolute bottom-2 right-2 flex h-5 w-5" title="Online">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#3fb950] opacity-60" />
                <span className="relative inline-flex h-5 w-5 rounded-full border-[3px] border-[#0d1117] bg-[#3fb950]" />
              </span>
            </div>

            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-space-sm">
                <h1 className="truncate font-headline text-4xl font-bold tracking-[-0.035em] text-on-surface md:text-5xl">{user.name}</h1>
                <span className="rounded-full border border-white/10 bg-white/[0.06] px-3 py-1 font-headline text-body-sm font-semibold text-on-surface">
                  {plan?.label ?? 'Free Developer'}{provider === 'demo' ? ' · demo' : ''}
                </span>
              </div>
              <div className="mt-space-sm flex flex-wrap items-center gap-x-space-lg gap-y-1 text-body-md text-on-surface-variant">
                <span className="flex items-center gap-1.5"><Mail className="h-4 w-4" aria-hidden="true" />{user.email}</span>
                <span className="flex items-center gap-1.5"><ShieldCheck className="h-4 w-4" aria-hidden="true" />Signed in with {provider === 'demo' ? 'demo mode' : provider}</span>
                {joinedAt && <span className="flex items-center gap-1.5"><CalendarDays className="h-4 w-4" aria-hidden="true" />Member since {format(new Date(joinedAt), 'MMM yyyy')}</span>}
              </div>
              {people.length > 0 && (
                <div className="mt-space-md flex items-center gap-space-sm text-body-sm text-on-surface-variant">
                  <div className="flex -space-x-2">
                    {people.slice(0, 5).map(p => (
                      <span key={p.id} title={p.name} className="grid h-7 w-7 place-items-center rounded-full border-2 border-[#0d1117] font-code text-[10px] font-bold text-[#0d1117]" style={{ background: p.color }}>
                        {p.initials}
                      </span>
                    ))}
                  </div>
                  <span>
                    Building with {people.slice(0, 2).map(p => p.name.split(' ')[0]).join(', ')}
                    {people.length > 2 ? ` and ${people.length - 2} more` : ''}
                  </span>
                </div>
              )}
            </div>

            <div className="flex flex-wrap gap-space-sm md:flex-none">
              <Link href="/dashboard?new=1" className="btn-shine flex items-center gap-space-sm rounded-full bg-primary px-space-lg py-2.5 font-headline text-body-md font-semibold text-on-primary shadow-[0_10px_30px_-10px_rgba(59,130,246,0.9)] transition-shadow hover:shadow-[0_0_30px_rgba(59,130,246,0.6)]">
                <Plus className="h-4 w-4" /> New room
              </Link>
              <Link href="/dashboard" className="flex items-center gap-space-sm rounded-full border border-white/15 bg-white/[0.05] px-space-lg py-2.5 font-headline text-body-md text-on-surface transition-colors hover:bg-white/[0.1]">
                <DoorOpen className="h-4 w-4" /> My rooms
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* Stats */}
      <div className="mb-space-xl grid grid-cols-2 gap-space-md lg:grid-cols-4">
        {stats.map((stat, i) => {
          const Icon = stat.icon;
          return (
            <Reveal key={stat.label} delay={i * 70} className="h-full">
              <Spotlight className="glass-card group h-full rounded-2xl border border-white/10 p-space-lg transition-transform duration-300 hover:-translate-y-1">
                <span className="mb-space-md grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-primary to-secondary text-white shadow-[0_8px_24px_-8px_rgba(59,130,246,0.8)] transition-transform duration-300 group-hover:rotate-6 group-hover:scale-110">
                  <Icon className="h-5 w-5" />
                </span>
                <p className="font-headline text-4xl font-bold tracking-[-0.03em] text-on-surface">
                  <CountUp value={stat.value} duration={1100} />
                </p>
                <p className="mt-1 text-body-md text-on-surface-variant">{stat.label}</p>
              </Spotlight>
            </Reveal>
          );
        })}
      </div>

      <div className="grid grid-cols-1 gap-space-lg lg:grid-cols-3">
        <div className="space-y-space-lg lg:col-span-2">
          {/* Rooms */}
          <Reveal>
            <section className="glass-card rounded-2xl border border-white/10 p-space-lg">
              <div className="mb-space-lg flex items-center justify-between gap-space-md">
                <h2 className="font-headline text-2xl font-bold tracking-[-0.02em] text-on-surface">Your rooms</h2>
                <span className="flex items-center gap-2 text-body-sm text-on-surface-variant">
                  {liveCount > 0 && <span className="h-2 w-2 animate-pulse rounded-full bg-[#3fb950]" aria-hidden="true" />}
                  {liveCount} live · {rooms.length} total
                </span>
              </div>
              {sortedRooms.length === 0 ? (
                <div className="rounded-xl border border-dashed border-white/15 p-space-xl text-center">
                  <p className="mb-space-md text-body-lg text-on-surface">You&apos;re not in any rooms yet.</p>
                  <Link href="/dashboard?new=1" className="inline-flex items-center gap-space-sm rounded-full bg-primary px-space-lg py-2 font-semibold text-on-primary">
                    <Plus className="h-4 w-4" /> Create your first room
                  </Link>
                </div>
              ) : (
                <div className="space-y-2">
                  {sortedRooms.map((room, i) => {
                    const me = room.members.find(m => m.user_id === user.id);
                    const live = Date.now() - lastActivity(room) < LIVE_WINDOW_MS;
                    const hue = colorForId(room.id);
                    return (
                      <Link
                        key={room.id}
                        href={`/room/${room.id}`}
                        className="item-in group flex items-center gap-space-md rounded-xl border border-transparent p-space-sm transition-all hover:border-white/10 hover:bg-white/[0.04]"
                        style={{ animationDelay: `${Math.min(i, 8) * 50}ms` }}
                      >
                        <span
                          className="grid h-11 w-11 flex-none place-items-center rounded-xl font-headline text-lg font-bold text-white shadow-[inset_0_1px_0_rgba(255,255,255,.25)] transition-transform duration-300 group-hover:scale-105"
                          style={{ background: `linear-gradient(135deg, ${hue}, color-mix(in srgb, ${hue} 45%, #0d1117))` }}
                          aria-hidden="true"
                        >
                          {room.title.trim().charAt(0).toUpperCase() || '#'}
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="truncate font-headline text-body-lg font-semibold text-on-surface transition-colors group-hover:text-primary-fixed">{room.title}</span>
                            {live && (
                              <span className="flex flex-none items-center gap-1 rounded-full bg-[#3fb950]/15 px-2 py-0.5 text-[11px] font-semibold text-[#56d364]">
                                <span className="h-1.5 w-1.5 rounded-full bg-[#3fb950]" aria-hidden="true" /> Live
                              </span>
                            )}
                          </div>
                          <div className="truncate text-body-sm text-on-surface-variant">
                            Active {formatDistanceToNow(lastActivity(room), { addSuffix: true })}
                          </div>
                        </div>
                        <div className="hidden -space-x-1.5 sm:flex" aria-label={`${room.members.length} members`}>
                          {room.members.slice(0, 4).map(m => (
                            <span key={m.user_id} title={m.user?.name} className="grid h-6 w-6 place-items-center rounded-full border-2 border-[#11161d] font-code text-[9px] font-bold text-[#0d1117]" style={{ background: m.user?.color }}>
                              {m.user?.initials}
                            </span>
                          ))}
                          {room.members.length > 4 && (
                            <span className="grid h-6 w-6 place-items-center rounded-full border-2 border-[#11161d] bg-surface-container-highest font-code text-[9px] text-on-surface">+{room.members.length - 4}</span>
                          )}
                        </div>
                        {me && (
                          <span className={`hidden flex-none rounded-full px-2.5 py-0.5 text-[11px] font-semibold capitalize md:inline ${me.permission === 'owner' ? 'bg-primary/15 text-primary-fixed' : 'bg-white/[0.06] text-on-surface-variant'}`}>
                            {me.permission}
                          </span>
                        )}
                        <ArrowRight className="h-4 w-4 flex-none text-outline transition-transform group-hover:translate-x-1 group-hover:text-primary" aria-hidden="true" />
                      </Link>
                    );
                  })}
                </div>
              )}
            </section>
          </Reveal>

          {/* People */}
          {people.length > 0 && (
            <Reveal>
              <section className="glass-card rounded-2xl border border-white/10 p-space-lg">
                <h2 className="mb-space-lg font-headline text-2xl font-bold tracking-[-0.02em] text-on-surface">People you build with</h2>
                <div className="grid grid-cols-1 gap-space-sm sm:grid-cols-2">
                  {people.map(p => {
                    const n = sharedCount.get(p.id) ?? 0;
                    return (
                      <div key={p.id} className="flex items-center gap-space-md rounded-xl border border-white/[0.06] bg-white/[0.025] p-space-sm transition-colors hover:bg-white/[0.05]">
                        <span className="grid h-10 w-10 flex-none place-items-center rounded-full font-code text-xs font-bold text-[#0d1117] shadow-[0_0_0_3px_rgba(255,255,255,.05)]" style={{ background: p.color }}>
                          {p.initials}
                        </span>
                        <div className="min-w-0">
                          <div className="truncate font-headline text-body-md font-semibold text-on-surface">{p.name}</div>
                          <div className="text-body-sm text-on-surface-variant">{n} shared room{n === 1 ? '' : 's'}</div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </section>
            </Reveal>
          )}
        </div>

        <div className="space-y-space-lg">
          {/* Plan */}
          <Reveal>
            <section className="glass-card relative overflow-hidden rounded-2xl border border-white/10 p-space-lg">
              <span className="live-border" aria-hidden="true" />
              <div className="mb-space-md flex items-center gap-space-sm">
                <Sparkles className="h-5 w-5 text-secondary" aria-hidden="true" />
                <h2 className="font-headline text-xl font-bold text-on-surface">{plan?.label ?? 'Free Developer'}</h2>
                <span className="ml-auto font-code text-code-sm text-outline">{PLAN_PRICE[plan?.plan ?? 'free']}</span>
              </div>
              <div className="mb-space-lg flex items-center gap-space-md">
                <UsageRing value={owned.length} max={roomLimit} ready={barReady} />
                <div className="text-body-sm text-on-surface-variant">
                  <p className="font-headline text-body-md font-semibold text-on-surface">
                    {roomLimit === null
                      ? `Unlimited rooms · ${owned.length} owned`
                      : owned.length >= roomLimit ? 'Room limit reached' : `${roomLimit - owned.length} room${roomLimit - owned.length === 1 ? '' : 's'} left`}
                  </p>
                  <p>
                    {formatCap(plan?.token_cap ?? 1_000_000)} AI tokens and {formatCap(plan?.run_cap ?? 100)} builds per room.
                  </p>
                </div>
              </div>
              <Link
                href="/pricing"
                className="btn-shine block w-full rounded-full bg-gradient-to-r from-primary to-secondary py-2.5 text-center font-headline text-body-md font-semibold text-white shadow-[0_10px_30px_-10px_rgba(6,182,212,0.8)]"
              >
                {plan && plan.plan !== 'free' ? 'Change plan' : 'Upgrade to Team Pro'}
              </Link>
            </section>
          </Reveal>

          {/* Default role */}
          <Reveal>
            <section className="glass-card rounded-2xl border border-white/10 p-space-lg">
              <div className="mb-1 flex items-center justify-between">
                <h2 className="font-headline text-xl font-bold text-on-surface">Default role</h2>
                {savedRole && (
                  <span className="item-in flex items-center gap-1 text-body-sm font-semibold text-[#56d364]">
                    <Check className="h-3.5 w-3.5" /> Saved
                  </span>
                )}
              </div>
              <p className="mb-space-md text-body-sm text-on-surface-variant">Used when you create a new room.</p>
              <div className="space-y-2" role="radiogroup" aria-label="Default role">
                {ROLE_OPTIONS.map(opt => {
                  const selected = defaultRole === opt.value;
                  const Icon = opt.icon;
                  return (
                    <button
                      key={opt.value}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      onClick={() => handleRoleChange(opt.value)}
                      className={`flex w-full items-center gap-space-md rounded-xl border p-space-sm text-left transition-all ${
                        selected
                          ? 'border-primary/60 bg-gradient-to-r from-primary/15 to-secondary/10 shadow-[0_0_0_3px_rgba(59,130,246,0.12)]'
                          : 'border-white/[0.07] bg-white/[0.02] hover:border-white/20 hover:bg-white/[0.04]'
                      }`}
                    >
                      <span className={`grid h-9 w-9 flex-none place-items-center rounded-lg transition-colors ${selected ? 'bg-gradient-to-br from-primary to-secondary text-white' : 'bg-white/[0.06] text-on-surface-variant'}`}>
                        <Icon className="h-4 w-4" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block font-headline text-body-md font-semibold text-on-surface">{opt.label}</span>
                        <span className="block text-body-sm text-on-surface-variant">{opt.detail}</span>
                      </span>
                      <span className={`grid h-5 w-5 flex-none place-items-center rounded-full border-2 transition-colors ${selected ? 'border-primary bg-primary' : 'border-white/20'}`} aria-hidden="true">
                        {selected && <Check className="h-3 w-3 text-white" />}
                      </span>
                    </button>
                  );
                })}
              </div>
            </section>
          </Reveal>

          {/* Account */}
          <Reveal>
            <section className="glass-card rounded-2xl border border-white/10 p-space-lg">
              <h2 className="mb-space-md font-headline text-xl font-bold text-on-surface">Account</h2>
              {githubResult && (
                <p
                  role="status"
                  className={`mb-space-sm flex items-start gap-space-sm rounded-xl border p-space-sm text-body-sm ${githubResult.ok ? 'border-primary/40 bg-primary/10 text-on-surface' : 'border-error-strong/40 bg-error-strong/10 text-error'}`}
                >
                  {githubResult.ok && <Check className="mt-0.5 h-4 w-4 shrink-0 text-primary" />}
                  {githubResult.text}
                </p>
              )}
              <button
                type="button"
                onClick={handleConnectGitHub}
                disabled={connecting}
                className="group mb-space-sm flex w-full items-center gap-space-md rounded-xl border border-white/[0.07] bg-white/[0.03] p-space-sm text-left transition-colors hover:bg-white/[0.06] disabled:opacity-50"
              >
                <span className="grid h-9 w-9 flex-none place-items-center rounded-lg bg-white text-[#0d1117]"><Github className="h-5 w-5" /></span>
                <span className="min-w-0 flex-1">
                  <span className="block font-headline text-body-md font-semibold text-on-surface">{connecting ? 'Connecting…' : 'Connect GitHub'}</span>
                  <span className="block text-body-sm text-on-surface-variant">Needed to export rooms to a repository</span>
                </span>
                <ArrowRight className="h-4 w-4 text-outline transition-transform group-hover:translate-x-1" aria-hidden="true" />
              </button>
              <button
                type="button"
                onClick={handleSignOut}
                className="flex w-full items-center justify-center gap-space-sm rounded-xl border border-error-strong/30 py-2.5 text-body-md font-semibold text-error transition-colors hover:bg-error-strong/10"
              >
                <LogOut className="h-4 w-4" />
                Sign out
              </button>
            </section>
          </Reveal>
        </div>
      </div>
    </AppShell>
  );
}
