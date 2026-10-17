'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import { Reveal, ShaderBackground, SiteHeader, SiteFooter, SplitHeading } from '@/components/shell';
import {
  ActivityTicker,
  FeatureSlides,
  FinalCta,
  HeroStage,
  HowItWorks,
  LiveRoomPreview,
  LiveStats,
  Testimonials,
  TiltOnScroll,
} from '@/components/landing';
import { setDemoMode } from '@/lib/demo';

// Overview / landing screen. The page behaves like a room you just joined: the agent edits the
// headline and your own pointer carries a "You" tag.

export default function OverviewPage() {
  const router = useRouter();

  const openDemo = () => {
    setDemoMode(true);
    router.push('/room/demo-yoga');
  };

  return (
    <div className="landing flex min-h-screen flex-col bg-surface font-ui text-body-md text-on-surface">
      <SiteHeader active="overview" />

      <main className="w-full overflow-x-clip bg-surface pt-16">
        {/* Hero */}
        <section className="relative isolate overflow-hidden px-gutter pb-24 pt-20 md:px-16 md:pt-28 lg:px-24">
          <ShaderBackground interactive className="absolute inset-0 -z-10 opacity-60" />
          <div className="aurora pointer-events-none absolute inset-0 -z-10" aria-hidden="true" />
          <div className="hero-grid pointer-events-none absolute inset-0 -z-10" aria-hidden="true" />
          <div className="pointer-events-none absolute inset-x-0 bottom-0 -z-10 h-64 bg-gradient-to-b from-transparent to-surface" />

          <HeroStage onOpenDemo={openDemo} />

          <div className="hero-in relative z-10 mx-auto mt-20 max-w-6xl md:mt-28" style={{ animationDelay: '620ms' }}>
            <TiltOnScroll>
              <LiveRoomPreview />
            </TiltOnScroll>
          </div>
        </section>

        {/* Live numbers */}
        <section className="px-gutter pb-space-xl md:px-16 lg:px-24">
          <Reveal>
            <LiveStats />
          </Reveal>
        </section>

        {/* Live activity ticker */}
        <section className="border-y border-white/5 bg-surface-container-lowest/60">
          <ActivityTicker />
        </section>

        {/* Features: intro, then pinned sliding pages */}
        <section className="px-gutter pb-8 pt-28 md:px-16 lg:px-24">
          <div className="mx-auto max-w-6xl">
            <SplitHeading
              text="Everything a team needs to build with one agent, without stepping on each other."
              className="max-w-4xl font-display text-4xl font-bold leading-[1.02] tracking-[-0.035em] text-on-surface md:text-6xl"
            />
            <Reveal delay={200}>
              <p className="mt-space-lg max-w-xl font-display text-lg text-on-surface-variant">
                Live editing, votes when people disagree, a sandbox for every room and a one-click export when it&apos;s ready.
              </p>
            </Reveal>
          </div>
        </section>
        <FeatureSlides />

        <HowItWorks />
        <Testimonials />
        <FinalCta />
      </main>

      <SiteFooter />
    </div>
  );
}
