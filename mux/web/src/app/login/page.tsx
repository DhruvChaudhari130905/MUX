'use client';

import React, { useEffect, useState } from 'react';
import { Github, Chrome, Mail, Terminal } from 'lucide-react';
import { getSession, signInWithProvider, signInWithEmail, signUpWithEmail, isSupabaseConfigured, safeNext } from '@/lib/supabase';
import { onAuthStateChange } from '@/lib/supabase';
import { useRouter } from 'next/navigation';
import { ShaderBackground, SiteFooter, SiteHeader, HeroBackdrop } from '@/components/shell';

// Sign-in screen, built in the stitch Developer Workspace style (no stitch screen exists for it)

export default function LoginPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  // Set by /auth/callback when the provider or Supabase refused the sign-in
  const [signInError, setSignInError] = useState<string | null>(null);
  // Where to go after signing in (e.g. the room or template the user was opening)
  const nextPath = () => safeNext(new URLSearchParams(window.location.search).get('next'));

  useEffect(() => {
    setSignInError(new URLSearchParams(window.location.search).get('error'));

    const { data: { subscription } } = onAuthStateChange((event, session) => {
      if (session) {
        router.push(nextPath());
      }
    });

    // Check for existing session
    getSession().then((session) => {
      if (session) {
        router.push(nextPath());
      } else {
        setLoading(false);
      }
    });

    return () => subscription.unsubscribe();
  }, [router]);

  const handleSignIn = async (provider: 'google' | 'github') => {
    // Without real Supabase settings the OAuth redirect points at a placeholder host that doesn't exist
    if (!isSupabaseConfigured) return;
    setLoading(true);
    try {
      await signInWithProvider(provider, nextPath());
    } catch (error) {
      console.error('Sign in error:', error);
      setLoading(false);
      alert('Failed to sign in. Please try again.');
    }
  };

  // Email and password: sign in, or create an account (confirmed by email when the project requires it)
  const [mode, setMode] = useState<'signin' | 'signup'>('signin');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [emailNotice, setEmailNotice] = useState<string | null>(null);

  const handleEmail = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!isSupabaseConfigured) return;
    setSubmitting(true);
    setEmailError(null);
    setEmailNotice(null);
    try {
      if (mode === 'signin') {
        await signInWithEmail(email.trim(), password); // onAuthStateChange redirects
      } else {
        const { needsConfirmation } = await signUpWithEmail(email.trim(), password, name.trim(), nextPath());
        if (needsConfirmation) {
          setEmailNotice(`Check ${email.trim()} for a confirmation link, then sign in.`);
          setMode('signin');
          setPassword('');
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Something went wrong. Please try again.';
      setEmailError(
        /invalid login credentials/i.test(message) ? 'Wrong email or password.'
          : /email not confirmed/i.test(message) ? 'Confirm your email first: open the link we sent you, then sign in.'
            : /already registered/i.test(message) ? 'An account with this email already exists. Sign in instead.'
              : message,
      );
    } finally {
      setSubmitting(false);
    }
  };

  const inputClass =
    'w-full rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-space-md py-space-sm text-body-md text-on-surface placeholder:text-outline focus:border-primary/60 focus:outline-none';

  const providerButton =
    'flex w-full items-center justify-center gap-space-sm rounded-lg border border-outline-variant/40 bg-surface-container-high px-space-md py-space-md text-label-md font-medium text-on-surface transition-all hover:border-primary/50 hover:bg-surface-bright disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-outline-variant/40 disabled:hover:bg-surface-container-high';

  return (
    <div className="relative flex min-h-screen flex-col bg-surface font-ui text-body-md text-on-surface">
      <ShaderBackground interactive className="fixed inset-0 z-0 opacity-60" />
      <div className="pointer-events-none fixed inset-0 z-0 bg-gradient-to-b from-primary/10 via-transparent to-transparent" />

      <SiteHeader />

      <main className="relative isolate z-10 flex flex-[1_0_auto] items-center justify-center overflow-hidden px-gutter pb-space-xl pt-24">
        <HeroBackdrop />
        {loading ? (
          <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-primary" />
        ) : (
          <div className="hero-in relative z-10 w-full max-w-md overflow-hidden rounded-2xl glass-modal p-space-xl">
            <span className="live-border" aria-hidden="true" />
            <div className="float-slow pointer-events-none absolute -bottom-24 -right-24 h-64 w-64 rounded-full bg-primary/10 blur-3xl" />

            <div className="relative mb-space-xl text-center">
              <div className="mb-space-md inline-flex items-center gap-space-sm rounded-full border border-outline-variant/30 bg-surface-container-high px-space-md py-space-xs text-body-sm text-on-surface-variant">
                <span className="flex h-2 w-2 rounded-full bg-primary animate-pulse" />
                <span className="font-code text-code-sm text-primary">Multiplayer rooms</span>
              </div>
              <h1 className="mb-space-sm font-headline text-4xl font-bold tracking-[-0.035em]">Welcome to MUX</h1>
              <p className="text-body-md text-on-surface-variant">Eight people steering, one agent building</p>
            </div>

            {!isSupabaseConfigured && (
              <div className="relative mb-space-md rounded-lg border border-outline-variant/40 bg-surface-container-lowest p-space-md text-body-sm text-on-surface-variant">
                Sign-in isn&apos;t configured. To enable it, copy{' '}
                <code className="font-code text-secondary">.env.example</code> to <code className="font-code text-secondary">.env.local</code>, set{' '}
                <code className="font-code text-secondary">NEXT_PUBLIC_SUPABASE_URL</code> and{' '}
                <code className="font-code text-secondary">NEXT_PUBLIC_SUPABASE_ANON_KEY</code>, then restart the dev server.
              </div>
            )}

            {signInError && (
              <div role="alert" className="relative mb-space-md rounded-lg border border-error/40 bg-error/10 p-space-md text-body-sm text-on-surface">
                Sign-in failed: {signInError}
              </div>
            )}

            <div className="relative space-y-space-sm">
              <button
                className={providerButton}
                onClick={() => handleSignIn('google')}
                disabled={loading || !isSupabaseConfigured}
                type="button"
              >
                <Chrome className="h-5 w-5" />
                Continue with Google
              </button>

              <button
                className={providerButton}
                onClick={() => handleSignIn('github')}
                disabled={loading || !isSupabaseConfigured}
                type="button"
              >
                <Github className="h-5 w-5" />
                Continue with GitHub
              </button>

              <div className="flex items-center gap-space-sm py-space-xs font-code text-code-sm text-outline">
                <span className="h-px flex-1 bg-outline-variant/40" />
                or
                <span className="h-px flex-1 bg-outline-variant/40" />
              </div>

              <form className="space-y-space-sm" onSubmit={handleEmail}>
                {mode === 'signup' && (
                  <input
                    className={inputClass}
                    type="text"
                    autoComplete="name"
                    placeholder="Your name"
                    value={name}
                    onChange={e => setName(e.target.value)}
                    maxLength={100}
                  />
                )}
                <input
                  className={inputClass}
                  type="email"
                  autoComplete="email"
                  placeholder="you@example.com"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  required
                />
                <input
                  className={inputClass}
                  type="password"
                  autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
                  placeholder={mode === 'signin' ? 'Password' : 'Password (at least 6 characters)'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  minLength={mode === 'signup' ? 6 : undefined}
                  required
                />

                {emailError && (
                  <p role="alert" className="rounded-lg border border-error/40 bg-error/10 p-space-sm text-body-sm text-on-surface">
                    {emailError}
                  </p>
                )}
                {emailNotice && (
                  <p role="status" className="rounded-lg border border-primary/40 bg-primary/10 p-space-sm text-body-sm text-on-surface">
                    {emailNotice}
                  </p>
                )}

                <button
                  className="btn-shine flex w-full items-center justify-center gap-space-sm rounded-lg bg-primary px-space-md py-space-md text-label-md font-bold text-on-primary shadow-lg shadow-primary/20 transition-all hover:-translate-y-0.5 hover:bg-primary/90 hover:shadow-[0_0_25px_rgba(59,130,246,0.6)] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
                  disabled={submitting || !isSupabaseConfigured}
                  type="submit"
                >
                  <Mail className="h-5 w-5" />
                  {submitting ? 'Please wait…' : mode === 'signin' ? 'Sign in with email' : 'Create account'}
                </button>

                <p className="text-center text-body-sm text-on-surface-variant">
                  {mode === 'signin' ? 'New to MUX?' : 'Already have an account?'}{' '}
                  <button
                    type="button"
                    className="font-medium text-primary hover:underline"
                    onClick={() => {
                      setMode(mode === 'signin' ? 'signup' : 'signin');
                      setEmailError(null);
                      setEmailNotice(null);
                    }}
                  >
                    {mode === 'signin' ? 'Create an account' : 'Sign in'}
                  </button>
                </p>
              </form>
            </div>

            <div className="relative mt-space-lg flex items-center justify-center gap-space-xs font-code text-code-sm text-outline">
              <Terminal className="h-3.5 w-3.5" />
              Free tier includes 3 active rooms
            </div>

            <p className="relative mt-space-md text-center text-body-sm text-on-surface-variant">
              By continuing, you agree to our Terms of Service and Privacy Policy.
            </p>
          </div>
        )}
      </main>

      <SiteFooter />
    </div>
  );
}
