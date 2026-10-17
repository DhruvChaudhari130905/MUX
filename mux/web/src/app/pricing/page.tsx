'use client';

import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { Zap, Code2, Users, ShieldCheck, CheckCircle2, Minus, Check, ChevronDown, Calculator, PlayCircle, Mail, ArrowRight } from 'lucide-react';
import { api } from '@/lib/api';
import { getUser } from '@/lib/supabase';
import type { PlanName, UserPlan } from '@/types';
import { BTN_GHOST, BTN_PRIMARY, CARD, CtaCard, IconTile, PageHero, Reveal, ShaderBackground, SiteHeader, SiteFooter, Spotlight } from '@/components/shell';

// Pricing screen (stitch: mux_pricing_live_wallpaper_pro)

type Billing = 'monthly' | 'annual';

const COMPARISON: { feature: string; free: React.ReactNode; pro: React.ReactNode; enterprise: React.ReactNode }[] = [
  { feature: 'Active Rooms', free: '3 rooms', pro: 'Unlimited', enterprise: 'Unlimited + VPC' },
  { feature: 'AI tokens per room', free: '1M', pro: '5M', enterprise: '20M' },
  { feature: 'Builds per room', free: '100', pro: '500', enterprise: '2,000' },
  { feature: 'Build Workers', free: 'Standard', pro: 'Priority Nebius', enterprise: 'Dedicated Runners' },
  { feature: 'GitHub Integration', free: false, pro: true, enterprise: true },
  { feature: 'Real-Time Voice & Cursors', free: false, pro: true, enterprise: true },
  { feature: 'AI model choice per room', free: false, pro: true, enterprise: true },
  { feature: 'Private rooms & invite links', free: false, pro: true, enterprise: true },
  { feature: 'Room history', free: '7 days', pro: '90 days', enterprise: 'Unlimited' },
  { feature: 'Support', free: 'Community', pro: 'Priority email', enterprise: 'Dedicated manager' },
  { feature: 'Bring your own model keys', free: false, pro: false, enterprise: true },
  { feature: 'Admin console & roles', free: false, pro: false, enterprise: true },
  { feature: 'SAML / SSO & Audit Logs', free: false, pro: false, enterprise: true },
  { feature: 'Uptime SLA', free: false, pro: false, enterprise: '99.9%' },
];

const FAQS = [
  {
    q: 'What is a collaborative coding room?',
    a: 'A room is an isolated live development environment where multiple users and AI coding assistants can write code simultaneously, run builds, and vote on tasks in real time.',
  },
  {
    q: 'Can I bring my own API keys for AI models?',
    a: 'Yes! Team Pro and Enterprise plans allow you to configure custom API keys for OpenAI, Anthropic, or local model providers running on your infrastructure.',
  },
  {
    q: 'How do build workers function?',
    a: 'MUX spins up lightning-fast isolated build containers (powered by Nebius) to test your React, Vite, and Node applications instantly whenever code changes or tasks merge.',
  },
  {
    q: 'What happens when a room runs out of tokens or builds?',
    a: "The room pauses instead of overspending: the agent stops picking up new work, and nothing built so far is lost. Every room shows its token and build usage in its top bar, so you can see the limit coming.",
  },
  {
    q: 'Can I cancel or change my plan anytime?',
    a: 'Absolutely. You can upgrade, downgrade, or cancel your subscription directly from your billing dashboard with zero cancellation fees.',
  },
];

function Feature({ children, muted, iconClass = 'text-primary' }: { children: React.ReactNode; muted?: boolean; iconClass?: string }) {
  return (
    <div className={`flex items-center gap-space-sm text-body-md ${muted ? 'text-on-surface-variant' : 'text-on-surface'}`}>
      {muted ? <Minus className="h-[18px] w-[18px] flex-none text-outline" /> : <CheckCircle2 className={`h-[18px] w-[18px] flex-none ${iconClass}`} />}
      <span className={muted ? 'line-through' : ''}>{children}</span>
    </div>
  );
}

function Cell({ value, highlight }: { value: React.ReactNode; highlight?: boolean }) {
  if (value === true) return <Check className={`mx-auto h-5 w-5 ${highlight ? 'text-primary' : 'text-on-surface'}`} aria-label="Included" />;
  if (value === false) return <Minus className="mx-auto h-5 w-5 text-outline" aria-label="Not included" />;
  return <>{value}</>;
}

// Question that expands to show its answer, with the height animated
function FaqItem({ q, a }: { q: string; a: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`rounded-xl border bg-surface-container/70 backdrop-blur transition-colors ${open ? 'border-primary/40' : 'border-white/10 hover:border-white/20'}`}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-space-md p-space-lg text-left"
      >
        <h3 className="font-headline text-headline-sm text-on-surface">{q}</h3>
        <ChevronDown className={`h-4 w-4 flex-none text-outline transition-transform duration-300 ${open ? 'rotate-180 text-primary' : ''}`} />
      </button>
      <div className={`grid transition-[grid-template-rows] duration-300 ease-out ${open ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}>
        <div className="overflow-hidden">
          <p className="px-space-lg pb-space-lg text-body-md text-on-surface-variant">{a}</p>
        </div>
      </div>
    </div>
  );
}

const PRO_MONTHLY = 20;
const PRO_ANNUAL = 16;

// Team-size slider: what Team Pro costs, and how many tokens the team gets
function Estimator({ annual }: { annual: boolean }) {
  const [seats, setSeats] = useState(5);
  const rate = annual ? PRO_ANNUAL : PRO_MONTHLY;
  const monthly = seats * rate;
  const saved = seats * (PRO_MONTHLY - PRO_ANNUAL) * 12;
  return (
    <div className={`${CARD} grid gap-space-xl p-space-xl md:grid-cols-[1.2fr_1fr] md:items-center`}>
      <div>
        <div className="mb-space-md flex items-center gap-space-md">
          <IconTile icon={Calculator} size="sm" />
          <div>
            <span className="font-code text-[11px] uppercase tracking-[0.18em] text-outline">Team Pro estimate</span>
            <h3 className="font-headline text-headline-md text-on-surface">How big is your team?</h3>
          </div>
        </div>
        <div className="mb-space-sm flex items-baseline justify-between">
          <span className="text-body-md text-on-surface-variant">Seats</span>
          <span className="font-headline text-2xl font-bold tabular-nums text-on-surface">{seats}</span>
        </div>
        <input
          type="range"
          min={1}
          max={50}
          value={seats}
          onChange={e => setSeats(Number(e.target.value))}
          aria-label="Number of seats"
          className="w-full accent-[rgb(59,130,246)]"
        />
        <div className="mt-1 flex justify-between font-code text-[11px] text-outline">
          <span>1</span><span>8 per room</span><span>50</span>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-space-sm">
        <div className="rounded-xl border border-white/10 bg-surface/60 p-space-md">
          <div className="font-code text-[11px] uppercase tracking-[0.15em] text-outline">Per month</div>
          <div key={`${monthly}-${annual}`} className="item-in font-headline text-2xl font-bold tabular-nums text-primary">${monthly.toLocaleString()}</div>
        </div>
        <div className="rounded-xl border border-white/10 bg-surface/60 p-space-md">
          <div className="font-code text-[11px] uppercase tracking-[0.15em] text-outline">Every room gets</div>
          <div className="font-headline text-2xl font-bold tabular-nums text-secondary">5M tokens</div>
        </div>
        <div className="col-span-2 rounded-xl border border-white/10 bg-surface/60 p-space-md text-body-sm text-on-surface-variant">
          {annual ? (
            <>Annual billing saves your team <span className="font-bold text-primary">${saved.toLocaleString()}</span> a year.</>
          ) : (
            <>Switch to annual billing to save <span className="font-bold text-primary">${saved.toLocaleString()}</span> a year.</>
          )}
        </div>
      </div>
    </div>
  );
}

interface PlanButtonProps {
  target: PlanName;
  current: PlanName | null;
  signedIn: boolean;
  busy: PlanName | null;
  onPick: (plan: PlanName) => void;
  label: string;
  className: string;
}

// The button at the bottom of each plan card. No payment step yet: picking a plan applies it at once.
function PlanButton({ target, current, signedIn, busy, onPick, label, className }: PlanButtonProps) {
  if (!signedIn) {
    return <Link href="/login?next=/pricing" className={className}>{label}</Link>;
  }
  if (current === target) {
    return (
      <span className="flex w-full items-center justify-center gap-space-xs rounded-full border border-[#3fb950]/40 bg-[#3fb950]/10 py-space-md text-label-md font-bold text-[#56d364]">
        <Check className="h-4 w-4" /> Your current plan
      </span>
    );
  }
  return (
    <button type="button" onClick={() => onPick(target)} disabled={busy !== null} className={`${className} disabled:cursor-wait disabled:opacity-60`}>
      {busy === target ? 'Switching…' : label}
    </button>
  );
}

function formatCap(n: number): string {
  return n >= 1_000_000 ? `${n / 1_000_000}M` : n.toLocaleString();
}

export default function PricingPage() {
  const [billing, setBilling] = useState<Billing>('monthly');
  const annual = billing === 'annual';

  // The signed-in user's plan, so each card can show "current" or switch to it
  const [signedIn, setSignedIn] = useState(false);
  const [plan, setPlan] = useState<UserPlan | null>(null);
  const [busy, setBusy] = useState<PlanName | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => {
    getUser()
      .then(u => {
        if (!u) return;
        setSignedIn(true);
        api.getPlan().then(setPlan).catch(() => { /* backend down: the cards still work as links */ });
      })
      .catch(() => {});
  }, []);
  const pickPlan = async (target: PlanName) => {
    setBusy(target);
    setNotice(null);
    try {
      const next = await api.setPlan(target);
      setPlan(next);
      const rooms = next.room_limit === null ? 'unlimited rooms' : `up to ${next.room_limit} rooms`;
      setNotice({ ok: true, text: `You're on ${next.label} now: ${rooms}, ${formatCap(next.token_cap)} AI tokens and ${formatCap(next.run_cap)} builds per room.` });
    } catch (error) {
      setNotice({ ok: false, text: error instanceof Error ? `Couldn't switch plans: ${error.message}` : "Couldn't switch plans." });
    } finally {
      setBusy(null);
    }
  };
  const current = plan?.plan ?? null;

  // Sliding highlight behind the selected billing option
  const monthlyRef = useRef<HTMLButtonElement>(null);
  const annualRef = useRef<HTMLButtonElement>(null);
  const [pill, setPill] = useState({ left: 0, width: 0 });
  const measurePill = () => {
    const el = annual ? annualRef.current : monthlyRef.current;
    if (!el) return;
    const next = { left: el.offsetLeft, width: el.offsetWidth };
    setPill(prev => (prev.left === next.left && prev.width === next.width ? prev : next));
  };
  useLayoutEffect(measurePill);
  useEffect(() => {
    window.addEventListener('resize', measurePill);
    document.fonts?.ready.then(measurePill);
    return () => window.removeEventListener('resize', measurePill);
  });

  const toggleClass = (on: boolean) =>
    `relative z-10 flex items-center gap-space-xs rounded-full px-space-lg py-space-sm text-label-md transition-colors duration-300 ${
      on ? 'font-bold text-on-surface' : 'text-on-surface-variant hover:text-on-surface'
    }`;

  return (
    <div className="relative flex min-h-screen flex-col bg-surface font-ui text-body-md text-on-surface">
      <ShaderBackground className="fixed inset-0 z-0 opacity-20" />
      <SiteHeader active="pricing" />

      <main className="relative z-10 w-full bg-transparent pt-16">
        <PageHero
          centered
          badge={<><Zap className="h-4 w-4" /> Simple, predictable pricing</>}
          title="Scale your rooms without limits"
          lead="Start free, upgrade when your team does. Every plan gets instant sandboxes, AI coding agents and real-time collaboration."
        >
          <div className="relative flex items-center gap-space-md rounded-full border border-white/10 bg-surface-container/80 p-1.5 backdrop-blur" role="group" aria-label="Billing period">
            <span
              aria-hidden="true"
              className="absolute bottom-1.5 top-1.5 rounded-full bg-surface-container-high shadow-[0_0_20px_rgba(59,130,246,0.25)] ring-1 ring-primary/30 transition-all duration-300 ease-out"
              style={{ left: pill.left, width: pill.width }}
            />
            <button ref={monthlyRef} type="button" className={toggleClass(!annual)} aria-pressed={!annual} onClick={() => setBilling('monthly')}>
              Monthly
            </button>
            <button ref={annualRef} type="button" className={toggleClass(annual)} aria-pressed={annual} onClick={() => setBilling('annual')}>
              Annual
              <span className="rounded-full bg-primary/20 px-space-xs py-0.5 font-code text-code-sm font-bold text-primary">Save 20%</span>
            </button>
          </div>
        </PageHero>

        {/* Plans */}
        <section className="mx-auto box-content max-w-6xl px-gutter pb-20 md:px-space-xl">
          {notice && (
            <div
              role="status"
              className={`item-in mb-space-lg flex flex-wrap items-center justify-between gap-space-md rounded-2xl glass-modal px-space-lg py-space-md text-body-md ${notice.ok ? 'text-on-surface' : 'text-error'}`}
            >
              <span className="flex items-center gap-space-sm">
                {notice.ok && <CheckCircle2 className="h-5 w-5 flex-none text-[#56d364]" />}
                {notice.text}
              </span>
              {notice.ok && (
                <Link href="/dashboard" className="flex items-center gap-1.5 font-semibold text-primary-fixed hover:underline">
                  Go to your rooms <ArrowRight className="h-4 w-4" />
                </Link>
              )}
            </div>
          )}
          <div className="grid grid-cols-1 gap-space-lg md:grid-cols-3">
            <Reveal className="h-full">
            <Spotlight className="group flex h-full flex-col justify-between rounded-2xl border border-white/10 bg-surface-container/70 p-space-xl backdrop-blur transition-all duration-300 hover:-translate-y-1 hover:border-primary/40 hover:bg-surface-container">
              <div>
                <div className="mb-space-md flex items-center justify-between">
                  <span className="font-headline text-headline-md text-on-surface">Free Developer</span>
                  <IconTile icon={Code2} size="sm" />
                </div>
                <p className="mb-space-lg text-body-sm text-on-surface-variant">
                  Perfect for trying out real-time coding rooms and small personal projects.
                </p>
                <div className="mb-space-lg flex items-baseline gap-space-xs">
                  <span className="font-headline text-5xl font-bold tracking-[-0.04em] text-on-surface">$0</span>
                  <span className="text-body-sm text-on-surface-variant">/ month</span>
                </div>
                <div className="mb-space-xl space-y-space-md">
                  <Feature>Up to 3 active collaboration rooms</Feature>
                  <Feature>1M AI tokens &amp; 100 builds per room</Feature>
                  <Feature>Standard build workers</Feature>
                  <Feature muted>GitHub automatic PR sync</Feature>
                </div>
              </div>
              <PlanButton
                target="free" current={current} signedIn={signedIn} busy={busy} onPick={pickPlan}
                label={signedIn ? 'Switch to Free' : 'Get Started Free'}
                className="w-full rounded-full border border-white/15 py-space-md text-center text-label-md font-bold text-on-surface transition-colors hover:border-primary/50 hover:bg-white/[0.04]"
              />
            </Spotlight>
            </Reveal>

            <Reveal delay={100} className="relative z-10 h-full">
            <Spotlight className="group flex h-full flex-col justify-between rounded-2xl border border-primary/50 bg-gradient-to-br from-primary/15 via-surface-container/90 to-secondary/10 p-space-xl shadow-[0_20px_60px_-15px_rgba(59,130,246,0.5)] backdrop-blur transition-all duration-300 hover:-translate-y-1 md:scale-[1.02]">
              <span className="live-border" aria-hidden="true" />
              <div className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-gradient-to-r from-primary to-secondary px-space-md py-0.5 font-code text-code-sm font-bold uppercase tracking-wider text-on-primary shadow-[0_0_20px_rgba(59,130,246,0.6)]">
                Most Popular
              </div>
              <div>
                <div className="mb-space-md flex items-center justify-between">
                  <span className="font-headline text-headline-md text-on-surface">Team Pro</span>
                  <IconTile icon={Users} size="sm" />
                </div>
                <p className="mb-space-lg text-body-sm text-on-surface-variant">
                  For professional teams building fast with real-time AI and continuous sync.
                </p>
                <div className="mb-space-lg flex items-baseline gap-space-xs">
                  <span key={billing} className="stat-roll inline-block font-headline text-5xl font-bold tracking-[-0.04em] text-on-surface">{annual ? `$${PRO_ANNUAL}` : `$${PRO_MONTHLY}`}</span>
                  <span key={`${billing}-period`} className="item-in text-body-sm text-on-surface-variant">{annual ? '/ user / month, billed annually' : '/ user / month'}</span>
                </div>
                <div className="mb-space-xl space-y-space-md">
                  <Feature>Unlimited concurrent rooms</Feature>
                  <Feature>5M AI tokens &amp; 500 builds per room</Feature>
                  <Feature>Priority Nebius build workers</Feature>
                  <Feature>Full GitHub PR &amp; repo export</Feature>
                  <Feature>Live voice &amp; cursor presence</Feature>
                  <Feature>Choose the AI model for each room</Feature>
                  <Feature>Private rooms &amp; invite links</Feature>
                  <Feature>90 days of room history</Feature>
                  <Feature>Priority email support</Feature>
                </div>
              </div>
              <PlanButton
                target="pro" current={current} signedIn={signedIn} busy={busy} onPick={pickPlan}
                label="Upgrade to Team Pro"
                className="btn-shine w-full rounded-full bg-primary py-space-md text-center text-label-md font-bold text-on-primary shadow-md transition-all hover:bg-primary/90 hover:shadow-[0_0_25px_rgba(59,130,246,0.6)]"
              />
            </Spotlight>
            </Reveal>

            <Reveal delay={200} className="h-full">
            <Spotlight id="contact" className="flex h-full scroll-mt-24 flex-col justify-between rounded-lg bg-surface-container/90 p-space-xl shadow-lg backdrop-blur transition-all duration-300 hover:-translate-y-1 hover:bg-surface-container-high">
              <div>
                <div className="mb-space-md flex items-center justify-between">
                  <span className="font-headline text-headline-md text-on-surface">Enterprise</span>
                  <IconTile icon={ShieldCheck} size="sm" />
                </div>
                <p className="mb-space-lg text-body-sm text-on-surface-variant">
                  Advanced security, custom AI model routing, and dedicated infrastructure.
                </p>
                <div className="mb-space-lg flex items-baseline gap-space-xs">
                  <span className="font-headline text-5xl font-bold tracking-[-0.04em] text-on-surface">Custom</span>
                  <span className="text-body-sm text-on-surface-variant">/ tailored</span>
                </div>
                <div className="mb-space-xl space-y-space-md">
                  <Feature iconClass="text-secondary">Everything in Team Pro</Feature>
                  <Feature iconClass="text-secondary">20M AI tokens &amp; 2,000 builds per room</Feature>
                  <Feature iconClass="text-secondary">SSO / SAML &amp; Audit logs</Feature>
                  <Feature iconClass="text-secondary">Dedicated VPC &amp; custom runners</Feature>
                  <Feature iconClass="text-secondary">Bring your own model keys &amp; routing</Feature>
                  <Feature iconClass="text-secondary">Admin console with role-based access</Feature>
                  <Feature iconClass="text-secondary">Unlimited room history &amp; data retention controls</Feature>
                  <Feature iconClass="text-secondary">99.9% uptime SLA</Feature>
                  <Feature iconClass="text-secondary">Dedicated success manager &amp; onboarding</Feature>
                </div>
              </div>
              <PlanButton
                target="enterprise" current={current} signedIn={signedIn} busy={busy} onPick={pickPlan}
                label="Upgrade to Enterprise"
                className="w-full rounded-full border border-white/15 py-space-md text-center text-label-md font-bold text-on-surface transition-colors hover:border-primary/50 hover:bg-white/[0.04]"
              />
            </Spotlight>
            </Reveal>
          </div>
        </section>

        {/* Estimator */}
        <section className="mx-auto box-content max-w-6xl px-gutter pb-20 md:px-space-xl">
          <Reveal>
            <div className="relative">
              <span className="absolute -top-2.5 left-4 z-10 rounded-full bg-surface px-2 font-code text-[10px] uppercase tracking-[0.2em] text-secondary">Try it</span>
              <Estimator annual={annual} />
            </div>
          </Reveal>
        </section>

        {/* Comparison */}
        <section className="mx-auto box-content max-w-6xl px-gutter pb-20 md:px-space-xl">
          <Reveal>
          <div className="mb-space-xl text-center">
            <span className="font-code text-[11px] uppercase tracking-[0.18em] text-outline">Compare</span>
            <h2 className="mb-space-sm font-headline text-2xl font-bold tracking-tight text-on-surface md:text-3xl">Every feature, side by side</h2>
            <p className="text-body-md text-on-surface-variant">Detailed breakdown of what&apos;s included across all tiers.</p>
          </div>
          <div className="overflow-x-auto rounded-xl border border-white/10 bg-surface-container/70 backdrop-blur">
            <div className="min-w-[560px]">
              <div className="grid grid-cols-4 border-b border-white/10 bg-white/[0.03] p-space-lg font-headline text-headline-sm text-on-surface">
                <div>Feature</div>
                <div className="text-center">Free</div>
                <div className="text-center text-primary">Team Pro</div>
                <div className="text-center">Enterprise</div>
              </div>
              <div className="divide-y divide-white/5">
                {COMPARISON.map(row => (
                  <div key={row.feature} className="grid grid-cols-4 items-center p-space-lg text-body-md transition-colors hover:bg-primary/5">
                    <div className="font-medium text-on-surface">{row.feature}</div>
                    <div className="text-center text-on-surface-variant"><Cell value={row.free} /></div>
                    <div className="text-center font-bold text-primary"><Cell value={row.pro} highlight /></div>
                    <div className="text-center text-on-surface"><Cell value={row.enterprise} highlight={row.feature.startsWith('SAML')} /></div>
                  </div>
                ))}
              </div>
            </div>
          </div>
          </Reveal>
        </section>

        {/* FAQ */}
        <section className="mx-auto box-content max-w-4xl px-gutter pb-20 md:px-space-xl">
          <Reveal className="mb-space-xl text-center">
            <span className="font-code text-[11px] uppercase tracking-[0.18em] text-outline">FAQ</span>
            <h2 className="mb-space-sm font-headline text-2xl font-bold tracking-tight text-on-surface md:text-3xl">Questions, answered</h2>
            <p className="text-body-md text-on-surface-variant">Got questions about billing, rooms, or security? We&apos;ve got answers.</p>
          </Reveal>
          <div className="space-y-space-md">
            {FAQS.map((faq, i) => (
              <Reveal key={faq.q} delay={i * 80}>
                <FaqItem q={faq.q} a={faq.a} />
              </Reveal>
            ))}
          </div>
        </section>

        <section className="mx-auto box-content max-w-6xl px-gutter pb-24 md:px-space-xl">
          <Reveal>
            <CtaCard title="See it before you pay for it." text="Open the sample room, or start free. No card needed.">
              <Link href="/login" className={BTN_PRIMARY}>
                <Zap className="h-4 w-4" /> Get started free
              </Link>
              <Link href="/docs" className={BTN_GHOST}>
                <PlayCircle className="h-4 w-4" /> Read the docs
              </Link>
              <a href="mailto:sales@mux.dev?subject=MUX%20Enterprise" className={BTN_GHOST}>
                <Mail className="h-4 w-4" /> Talk to sales
              </a>
            </CtaCard>
          </Reveal>
        </section>
      </main>

      <SiteFooter />
    </div>
  );
}
