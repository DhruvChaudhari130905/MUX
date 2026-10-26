'use client';

import React, { useEffect, useRef, useState } from 'react';
import '@xterm/xterm/css/xterm.css';
import type { Terminal as XTerm } from '@xterm/xterm';
import type { WebContainerProcess } from '@webcontainer/api';
import { attachRoom, onServerReady, pushRoomFiles, resolveServerLink, runningServers, updateRoomFs, webContainersSupported } from '@/lib/runtime';
import { notify } from '@/lib/notifications';
import { checkPackageJson, installWarning } from '@/lib/installHints';
import { Terminal as SimulatedTerminal, type TerminalFs } from './Terminal';

// A real shell for the room: jsh running in a WebContainer (Node, npm, npx, pnpm, git-less), shown with xterm.js.
// Falls back to the simulated shell when the browser can't run WebContainers.

// Lets the panel type commands into this shell (the "New project" menu)
export interface ShellControl {
  // Runs a command line in the shell; false when only the simulated shell is available
  run: (command: string) => boolean;
}

interface ShellTerminalProps {
  roomId: string;
  fs: TerminalFs;
  active: boolean;
  onExit: () => void;
  onControl?: (control: ShellControl | null) => void;
  // Folder (relative to the project) the shell starts in; '' = project root
  startDir: string;
}

const THEME = {
  background: '#010409',
  foreground: '#e6edf3',
  cursor: '#06b6d4',
  cursorAccent: '#010409',
  selectionBackground: '#3b82f655',
  black: '#484f58', red: '#ff7b72', green: '#3fb950', yellow: '#d29922', blue: '#58a6ff', magenta: '#bc8cff', cyan: '#39c5cf', white: '#b1bac4',
  brightBlack: '#6e7681', brightRed: '#ffa198', brightGreen: '#56d364', brightYellow: '#e3b341', brightBlue: '#79c0ff', brightMagenta: '#d2a8ff', brightCyan: '#56d4dd', brightWhite: '#ffffff',
};

// Commands that run at least this long notify when they finish (npm install, builds, tests)
const NOTIFY_AFTER_MS = 8000;
// jsh redraws its prompt ("~/project ❯") when a command finishes
const PROMPT_END = /❯\s*$/;
const stripAnsi = (s: string) => s.replace(/\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07/g, '');

const dim = (s: string) => `\x1b[2m${s}\x1b[0m`;
const yellow = (s: string) => `\x1b[33m${s}\x1b[0m`;
const cyan = (s: string) => `\x1b[36m${s}\x1b[0m`;
const green = (s: string) => `\x1b[32m${s}\x1b[0m`;

export function ShellTerminal({ roomId, fs, active, onExit, onControl, startDir }: ShellTerminalProps) {
  const startDirRef = useRef(startDir);
  startDirRef.current = startDir;
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<XTerm | null>(null);
  const fitRef = useRef<(() => void) | null>(null);
  const [fallback, setFallback] = useState<string | null>(() => {
    const support = webContainersSupported();
    return support.ok ? null : support.reason;
  });
  const onExitRef = useRef(onExit);
  onExitRef.current = onExit;
  const inputRef = useRef<((data: string) => void) | null>(null);
  const queuedRef = useRef<string[]>([]); // commands sent before the shell finished booting
  const fallbackRef = useRef(fallback);
  fallbackRef.current = fallback;

  useEffect(() => {
    onControl?.({
      run: command => {
        if (fallbackRef.current) return false;
        // Ctrl+U clears anything half-typed first
        if (inputRef.current) inputRef.current(`\x15${command}\r`);
        else queuedRef.current.push(command);
        termRef.current?.focus();
        return true;
      },
    });
    return () => onControl?.(null);
  }, [onControl]);

  // Room edits flow into the container while this terminal is open
  useEffect(() => {
    if (!fallback) updateRoomFs(roomId, fs);
  });
  useEffect(() => {
    if (!fallback) void pushRoomFiles(roomId, fs.files).catch(() => undefined);
  }, [roomId, fs.files, fallback]);

  useEffect(() => {
    if (fallback || !hostRef.current) return;
    let disposed = false;
    let shell: WebContainerProcess | null = null;
    let cleanup: (() => void)[] = [];

    (async () => {
      const [{ Terminal }, { FitAddon }, { WebLinksAddon }] = await Promise.all([
        import('@xterm/xterm'),
        import('@xterm/addon-fit'),
        import('@xterm/addon-web-links'),
      ]);
      if (disposed || !hostRef.current) return;

      const term = new Terminal({
        theme: THEME,
        fontFamily: 'var(--font-mono), ui-monospace, SFMono-Regular, Menlo, monospace',
        fontSize: 12.5,
        lineHeight: 1.25,
        cursorBlink: true,
        convertEol: true,
        scrollback: 5000,
        allowProposedApi: true,
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      // localhost links printed by dev servers point inside the WebContainer: open its URL for that port
      term.loadAddon(new WebLinksAddon((_e, uri) => window.open(resolveServerLink(uri), '_blank', 'noopener')));
      term.open(hostRef.current);
      termRef.current = term;
      const doFit = () => {
        if (!hostRef.current?.offsetWidth) return; // hidden tab
        try {
          fit.fit();
          shell?.resize({ cols: term.cols, rows: term.rows });
        } catch {
          // not laid out yet
        }
      };
      fitRef.current = doFit;
      doFit();
      const ro = new ResizeObserver(doFit);
      ro.observe(hostRef.current);
      cleanup.push(() => ro.disconnect());

      term.write(dim('Booting WebContainer (Node.js in your browser)…\r\n'));
      let wc;
      try {
        wc = await attachRoom(roomId, fs);
      } catch (err) {
        if (!disposed) setFallback(`WebContainer failed to start: ${(err as Error).message}`);
        return;
      }
      if (disposed) return;

      cleanup.push(
        onServerReady((port, url) => term.write(`\r\n${green('➜')} Server ready on port ${port}: ${cyan(url)}\r\n`)),
      );

      const startShell = async () => {
        // No env here: overriding the shell environment breaks npm's registry access in WebContainers
        // ("Protocol https: not supported"), so the shell starts exactly as WebContainer sets it up
        // Start in the project's folder if it exists in the container; the root otherwise
        const dir = startDirRef.current;
        const cwd = dir && (await wc.fs.readdir(dir).then(() => true, () => false)) ? dir : undefined;
        shell = await wc.spawn('jsh', { terminal: { cols: term.cols, rows: term.rows }, ...(cwd ? { cwd } : {}) });
        const proc = shell;
        // Track the typed command line and when it started, to notify when a long one finishes
        let typed = '';
        let running: { command: string; since: number } | null = null;
        let tail = '';
        proc.output.pipeTo(new WritableStream({
          write: data => {
            term.write(data);
            if (!running) return;
            tail = (tail + stripAnsi(data)).slice(-200);
            if (!PROMPT_END.test(tail)) return;
            const ms = Date.now() - running.since;
            if (ms >= NOTIFY_AFTER_MS) {
              notify({ category: 'terminal', title: `Finished: ${running.command}`, body: `Took ${Math.round(ms / 1000)}s in the terminal` });
            }
            running = null;
          },
        })).catch(() => {});
        const input = proc.input.getWriter();
        const sub = term.onData(data => {
          void input.write(data);
          for (const ch of data) {
            if (ch === '\r') {
              const command = typed.trim();
              typed = '';
              tail = '';
              running = command ? { command: /[\x00-\x1f]/.test(command) ? 'command' : command.slice(0, 60), since: Date.now() } : null;
            } else if (ch === '\x7f') typed = typed.slice(0, -1);
            else if (ch === '\x03' || ch === '\x15') typed = '';
            else typed += ch;
          }
        });
        inputRef.current = data => { void input.write(data); };
        // jsh needs a moment to print its prompt before it reads input
        setTimeout(() => {
          for (const c of queuedRef.current.splice(0)) inputRef.current?.(`${c}\r`);
        }, 300);
        proc.exit.then(code => {
          sub.dispose();
          inputRef.current = null;
          input.releaseLock();
          if (disposed || shell !== proc) return;
          // `exit` closes the session, like VS Code; a crash offers a restart
          if (code === 0) onExitRef.current();
          else {
            term.write(`\r\n${dim(`[shell exited with code ${code}] press any key to restart`)}\r\n`);
            const once = term.onData(() => { once.dispose(); void startShell(); });
          }
        });
      };

      term.write(dim(`Ready · real Node.js, npm, npx and node run here · files sync with the room\r\n`));
      installWarning(checkPackageJson(fs.files.get('package.json')?.content)).forEach(line => term.write(`${yellow(line)}\r\n`));
      runningServers(roomId).forEach(([port, url]) => term.write(`${green('➜')} Server running on port ${port}: ${cyan(url)}\r\n`));
      await startShell();
      if (active) term.focus();
    })();

    return () => {
      disposed = true;
      shell?.kill();
      cleanup.forEach(fn => fn());
      cleanup = [];
      termRef.current?.dispose();
      termRef.current = null;
    };
    // The shell lives for the whole session; fs updates are handled by the effect above
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId, fallback]);

  // Hidden tabs have no size: refit and focus when this session becomes visible
  useEffect(() => {
    if (!active) return;
    const id = requestAnimationFrame(() => {
      fitRef.current?.();
      termRef.current?.focus();
    });
    return () => cancelAnimationFrame(id);
  }, [active]);

  if (fallback) {
    return (
      <div className="flex h-full flex-col">
        <p className="border-b border-[var(--line)] px-3 py-1 font-sans text-[11px] text-[#d29922]" title={fallback}>
          Simulated shell: {fallback}. Use Chrome, Edge or Firefox on a page with COOP/COEP headers for a real Node.js terminal.
        </p>
        <div className="min-h-0 flex-1">
          <SimulatedTerminal fs={fs} active={active} onExit={onExit} />
        </div>
      </div>
    );
  }

  return <div ref={hostRef} className="xterm-host h-full w-full" onClick={() => termRef.current?.focus()} />;
}
