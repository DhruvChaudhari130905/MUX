'use client';

import React, { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { Share2, Code2, User, Menu, X, Check } from 'lucide-react';
import { Logo } from './Logo';
import { formatClock, useTicker } from './Motion';
import { SpotlightTracker, YouCursor } from './LiveChrome';
import { DemoBanner } from './DemoBanner';
import { getUser } from '@/lib/supabase';
import { colorForId, getLastRoom, LAST_ROOM_CHANGED, type LastRoom } from '@/lib/preferences';

export type NavKey = 'overview' | 'rooms' | 'sandbox' | 'pricing' | 'docs';

export const NAV_ITEMS: { key: NavKey; label: string; href: string }[] = [
  { key: 'overview', label: 'Overview', href: '/' },
  { key: 'rooms', label: 'Rooms', href: '/dashboard' },
  { key: 'sandbox', label: 'Sandbox', href: '/sandbox' },
  { key: 'pricing', label: 'Pricing', href: '/pricing' },
  { key: 'docs', label: 'Docs', href: '/docs' },
];

// Pill for the last room you opened, with a live "time since opened" clock; links back into it
export function SessionPill() {
  const [last, setLast] = useState<LastRoom | null>(null);
  const [ready, setReady] = useState(false);
  useTicker(1000); // re-render each second for the clock

  useEffect(() => {
    const read = () => setLast(getLastRoom());
    read();
    setReady(true);
    window.addEventListener(LAST_ROOM_CHANGED, read);
    return () => window.removeEventListener(LAST_ROOM_CHANGED, read);
  }, []);

  const pill = 'flex items-center gap-space-sm rounded-full border border-white/[0.07] bg-white/[0.04] px-space-md py-1.5 text-body-sm transition-colors hover:bg-white/[0.08]';
  if (!ready) return <span className={`${pill} h-7 w-56 animate-pulse`} aria-hidden="true" />;

  if (!last) {
    return (
      <Link href="/dashboard?new=1" className={pill}>
        <span className="flex h-2 w-2 rounded-full bg-outline" />
        <span className="font-code text-code-sm text-on-surface-variant">No active room</span>
        <span className="rounded bg-primary/20 px-space-xs font-code text-code-sm text-primary">Start one</span>
      </Link>
    );
  }

  const seconds = Math.max(0, Math.floor((Date.now() - last.openedAt) / 1000));
  return (
    <Link href={`/room/${last.id}`} className={pill} title={`Back to ${last.title}`}>
      <span className="flex h-2 w-2 rounded-full bg-primary animate-pulse" />
      <span className="max-w-[180px] truncate font-code text-code-sm text-on-surface">{last.title}</span>
      <span className="font-code text-code-sm tabular-nums text-outline">• {formatClock(seconds % 360000)}</span>
      <span className="rounded bg-primary/20 px-space-xs font-code text-code-sm text-primary">Resume</span>
    </Link>
  );
}

// Signed-in user's initials for the avatar, or null when signed out
function useHeaderUser() {
  const [me, setMe] = useState<{ initials: string; color: string; name: string } | null>(null);
  useEffect(() => {
    let cancelled = false;
    getUser()
      .then(u => {
        if (cancelled || !u) return;
        const name: string = u.user_metadata?.full_name || u.email?.split('@')[0] || 'User';
        const initials = name.split(' ').map(p => p[0]).join('').slice(0, 2).toUpperCase();
        setMe({ initials, color: colorForId(u.id), name });
      })
      .catch(() => {
        // not signed in / auth unavailable: keep the generic avatar
      });
    return () => { cancelled = true; };
  }, []);
  return me;
}

export function HeaderActions({ profileActive = false }: { profileActive?: boolean }) {
  const [copied, setCopied] = useState(false);
  const me = useHeaderUser();

  // Copy the page link; falls back to a hidden textarea where the Clipboard API is unavailable (plain http)
  const handleShare = async () => {
    const url = window.location.href;
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      const area = document.createElement('textarea');
      area.value = url;
      area.style.position = 'fixed';
      area.style.opacity = '0';
      document.body.appendChild(area);
      area.select();
      document.execCommand('copy');
      area.remove();
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex items-center gap-space-md">
      <div className="flex items-center gap-space-xs">
        <button
          type="button"
          onClick={handleShare}
          className="flex items-center gap-space-xs rounded-full border border-white/[0.08] bg-white/[0.05] px-space-md py-2 font-ui text-label-md text-on-surface transition-colors hover:bg-white/[0.1]"
        >
          {copied ? <Check className="h-4 w-4" /> : <Share2 className="h-4 w-4" />}
          <span className="hidden sm:inline">{copied ? 'Copied' : 'Share'}</span>
        </button>
        <Link
          href="/dashboard?export=1"
          className="flex items-center gap-space-xs rounded-full bg-gradient-to-b from-[#4f8ff7] to-[#2563eb] px-space-md py-2 font-ui text-label-md font-bold text-on-primary shadow-[0_6px_20px_-6px_rgba(59,130,246,0.9),inset_0_1px_0_rgba(255,255,255,0.25)] transition-shadow hover:shadow-[0_0_24px_rgba(59,130,246,0.6),inset_0_1px_0_rgba(255,255,255,0.25)]"
        >
          <Code2 className="h-4 w-4" />
          <span className="hidden sm:inline">Export to GitHub</span>
        </Link>
      </div>
      <Link
        href={me ? '/profile' : '/login'}
        aria-label={me ? 'Profile' : 'Sign in'}
        title={me ? `${me.name} · Profile` : 'Sign in'}
        aria-current={profileActive ? 'page' : undefined}
        className={`grid h-8 w-8 place-items-center rounded-full font-code text-[11px] font-bold transition-all hover:scale-110 hover:shadow-[0_0_15px_rgba(59,130,246,0.5)] ${
          profileActive ? 'ring-2 ring-secondary ring-offset-2 ring-offset-surface' : ''
        } ${me ? 'text-[#0d1117]' : 'bg-primary'}`}
        style={me ? { background: me.color } : undefined}
      >
        {me ? me.initials : <User className="h-[18px] w-[18px] text-on-primary" />}
      </Link>
    </div>
  );
}

function GlassNav({ active }: { active?: NavKey }) {
  const navRef = useRef<HTMLElement>(null);
  const [hovered, setHovered] = useState<NavKey | null>(null);
  const [pill, setPill] = useState<{ left: number; width: number } | null>(null);
  const target = hovered ?? active ?? null;

  useEffect(() => {
    const measure = () => {
      const el = target ? navRef.current?.querySelector<HTMLElement>(`[data-key="${target}"]`) : null;
      setPill(el ? { left: el.offsetLeft, width: el.offsetWidth } : null);
    };
    measure();
    document.fonts?.ready.then(measure);
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [target]);

  return (
    <nav ref={navRef} className="relative hidden items-center md:flex" onMouseLeave={() => setHovered(null)}>
      <span
        aria-hidden="true"
        className="glass-pill absolute inset-y-0 my-auto h-8 rounded-full transition-all duration-300 ease-[cubic-bezier(.2,.8,.2,1)]"
        style={pill ? { left: pill.left, width: pill.width, opacity: 1 } : { left: 0, width: 0, opacity: 0 }}
      />
      {NAV_ITEMS.map(item => (
        <Link
          key={item.key}
          data-key={item.key}
          href={item.href}
          aria-current={item.key === active ? 'page' : undefined}
          onMouseEnter={() => setHovered(item.key)}
          onFocus={() => setHovered(item.key)}
          onBlur={() => setHovered(null)}
          className={`relative z-10 rounded-full px-3.5 py-1.5 text-body-md transition-colors duration-200 ${
            item.key === target ? 'text-on-surface' : 'text-on-surface-variant hover:text-on-surface'
          } ${item.key === active ? 'font-semibold' : ''}`}
        >
          {item.label}
        </Link>
      ))}
    </nav>
  );
}

interface SiteHeaderProps {
  active?: NavKey;
  profileActive?: boolean;
}

export function SiteHeader({ active, profileActive = false }: SiteHeaderProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  return (
    <>
    {active !== 'overview' && <YouCursor />}
    <SpotlightTracker />
    <DemoBanner />
    <header className="pointer-events-none fixed inset-x-0 top-0 z-50 px-2.5 pt-2.5 font-ui">
      <div
        onPointerMove={e => {
          const r = e.currentTarget.getBoundingClientRect();
          e.currentTarget.style.setProperty('--gx', `${e.clientX - r.left}px`);
        }}
        className={`glass-bar pointer-events-auto mx-auto transition-[max-width,background-color,box-shadow] duration-500 ${scrolled ? 'is-scrolled max-w-6xl' : 'max-w-[1400px]'}`}
      >
        <div className="relative flex h-[52px] w-full items-center justify-between gap-space-md pl-3 pr-2">
          <div className="flex items-center gap-space-lg">
            <button
              type="button"
              className="-ml-1 rounded-lg p-1.5 text-on-surface-variant transition-colors hover:bg-white/[0.06] hover:text-on-surface md:hidden"
              onClick={() => setMenuOpen(o => !o)}
              aria-label={menuOpen ? 'Close menu' : 'Open menu'}
              aria-expanded={menuOpen}
            >
              {menuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </button>
            <Logo />
            <GlassNav active={active} />
          </div>
          <div className="flex items-center gap-space-sm">
            <div className="hidden lg:block">
              <SessionPill />
            </div>
            <HeaderActions profileActive={profileActive} />
          </div>
        </div>
        {menuOpen && (
          <nav className="item-in relative grid gap-1 border-t border-white/[0.06] px-2 py-2 md:hidden">
            {NAV_ITEMS.map(item => (
              <Link
                key={item.key}
                href={item.href}
                onClick={() => setMenuOpen(false)}
                aria-current={item.key === active ? 'page' : undefined}
                className={`rounded-xl px-space-md py-2.5 text-body-md transition-colors ${
                  item.key === active ? 'bg-white/[0.08] font-semibold text-on-surface' : 'text-on-surface-variant hover:bg-white/[0.04] hover:text-on-surface'
                }`}
              >
                {item.label}
              </Link>
            ))}
          </nav>
        )}
      </div>
    </header>
    </>
  );
}
