'use client';

import React from 'react';
import Link from 'next/link';
import { Zap, PlayCircle } from 'lucide-react';
import { Magnetic } from '@/components/shell';
import { AgentDiagram } from './AgentDiagram';

// Hero: the pitch on the left, and on the right the product in one picture: eight seats sending
// messages into one agent, which builds a live preview.

interface HeroStageProps {
  onOpenDemo: () => void;
}

export function HeroStage({ onOpenDemo }: HeroStageProps) {
  return (
    <div className="relative mx-auto grid w-full max-w-6xl items-center gap-12 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)] lg:gap-8">
      <div className="relative z-10 text-left">
        <h1 className="hero-title font-display font-bold text-on-surface">
          <span className="hero-line block" style={{ animationDelay: '0ms' }}>Eight people steering.</span>
          <span className="hero-line block text-on-surface/50" style={{ animationDelay: '110ms' }}>One agent building.</span>
        </h1>

        <p className="hero-in mt-space-xl max-w-lg text-pretty font-display text-lg leading-relaxed text-on-surface-variant md:text-xl" style={{ animationDelay: '240ms' }}>
          A shared room where your team chats, votes and steers while one AI agent writes the code, runs the builds and ships it to GitHub.
        </p>

        <div className="hero-in mt-space-xl flex flex-wrap items-center gap-space-md" style={{ animationDelay: '360ms' }}>
          <Magnetic>
            <Link
              href="/dashboard"
              className="btn-shine group flex items-center gap-space-sm rounded-full bg-primary px-7 py-3.5 font-display text-base font-semibold text-on-primary shadow-[0_12px_40px_-12px_rgba(59,130,246,0.9)] transition-shadow hover:shadow-[0_0_44px_rgba(59,130,246,0.75)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-primary"
            >
              <Zap className="h-[18px] w-[18px] transition-transform duration-300 group-hover:rotate-12 group-hover:scale-110" />
              Start a build room
            </Link>
          </Magnetic>
          <Magnetic>
            <button
              type="button"
              onClick={onOpenDemo}
              className="flex items-center gap-space-sm rounded-full border border-white/15 bg-white/[0.04] px-7 py-3.5 font-display text-base font-medium text-on-surface backdrop-blur-md transition-colors hover:border-secondary/60 hover:bg-white/[0.08] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-secondary"
            >
              <PlayCircle className="h-[18px] w-[18px]" />
              Open the demo room
            </button>
          </Magnetic>
        </div>
      </div>

      <div className="hero-in relative" style={{ animationDelay: '300ms' }}>
        <AgentDiagram />
      </div>
    </div>
  );
}
