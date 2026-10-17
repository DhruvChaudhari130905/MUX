#!/usr/bin/env node
// mux-report build|test <command> [args...]
//
// Runs a build or test command in the project (the current directory) and prints what the backend
// reads (mux/server/mux/sandbox/runner.py): at most 20 `file:line: message` lines, or the last raw
// lines when none parse, then for tests `MUX_SUMMARY {"passed": n, "failed": m}`. Exits with the
// command's exit code.

import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { join, relative } from "node:path";

const MAX_LINES = 20;
const RAW_LINES = 40;
const SKIP_DIRS = new Set(["node_modules", "dist", ".git"]);
const ANSI = /\x1b\[[0-9;]*[A-Za-z]/g;
const TSC_PREFIX = "tsc --noEmit && ";
const VITEST_JSON = "/tmp/mux-vitest.json";
const STACK_FRAME = /^\s*at\s/;

const [mode, command, ...args] = process.argv.slice(2);
if ((mode !== "build" && mode !== "test") || !command) {
  console.error("usage: mux-report build|test <command> [args...]");
  process.exit(2);
}

const cwd = process.cwd();
const rel = (p) => (p.startsWith("/") ? relative(cwd, p) : p.replace(/^\.\//, ""));
const quote = (s) => `'${s.replaceAll("'", `'\\''`)}'`;

function hasTypeScript(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (!SKIP_DIRS.has(entry.name) && hasTypeScript(join(dir, entry.name))) return true;
    } else if (/\.tsx?$/.test(entry.name)) {
      return true;
    }
  }
  return false;
}

function run(cmd) {
  const res = spawnSync("sh", ["-c", cmd], {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
    env: { ...process.env, CI: "1", FORCE_COLOR: "0", NO_COLOR: "1" },
  });
  const output = `${res.stdout ?? ""}\n${res.stderr ?? ""}`.replace(ANSI, "");
  return { code: res.status ?? 1, output };
}

function printLines(lines, output) {
  const unique = [...new Set(lines)].slice(0, MAX_LINES);
  if (unique.length) {
    console.log(unique.join("\n"));
  } else {
    // Stack frames and lines from inside packages say nothing about the project's own code
    const raw = output.split("\n").map((l) => l.trimEnd())
      .filter((l) => l && !STACK_FRAME.test(l) && !l.includes("node_modules/"));
    if (raw.length) console.log(raw.slice(-RAW_LINES).join("\n"));
  }
}

// tsc: `src/App.tsx(3,5): error TS2322: message`
const TSC_LINE = /^(.+?)\((\d+),\d+\): error (TS\d+: .*)$/;
// esbuild / vite / most tools: `/app/src/App.tsx:3:10: ERROR: message` or `src/a.ts:3: message`
const FILE_LINE = /^\s*(?:file:\s*)?([^\s:()]+\.[A-Za-z]+):(\d+)(?::\d+)?:?\s+(?:ERROR:\s*|error:\s*)?(.*\S.*)$/;

function buildLines(output) {
  const lines = [];
  for (const raw of output.split("\n")) {
    const line = raw.trim();
    let m = TSC_LINE.exec(line);
    if (m) {
      lines.push(`${rel(m[1])}:${m[2]}: ${m[3]}`);
      continue;
    }
    m = FILE_LINE.exec(line);
    if (m && !m[1].includes("node_modules")) lines.push(`${rel(m[1])}:${m[2]}: ${m[3]}`);
  }
  return lines;
}

function failureLine(file, message) {
  // The first stack frame inside the failing file gives the line when the reporter has no location
  const esc = file.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = new RegExp(`${esc}:(\\d+)(?::\\d+)?`).exec(message);
  return m ? Number(m[1]) : 1;
}

function testLines(report) {
  const lines = [];
  for (const file of report.testResults ?? []) {
    const name = rel(file.name ?? "");
    const failed = (file.assertionResults ?? []).filter((a) => a.status === "failed");
    for (const a of failed) {
      const message = (a.failureMessages ?? []).join("\n");
      const line = a.location?.line ?? failureLine(file.name ?? "", message);
      const first = message.split("\n").find((l) => l.trim()) ?? "failed";
      lines.push(`${name}:${line}: ${a.fullName ?? a.title}: ${first.trim()}`);
    }
    if (!failed.length && file.status === "failed") {
      const first = (file.message ?? "failed").split("\n").find((l) => l.trim()) ?? "failed";
      lines.push(`${name}:1: ${first.trim()}`);
    }
  }
  return lines;
}

if (mode === "build") {
  // A plain HTML/CSS/JS site has nothing for tsc to check (and tsc fails with no inputs)
  const cmd = command.startsWith(TSC_PREFIX) && !hasTypeScript(cwd) ? command.slice(TSC_PREFIX.length) : command;
  const { code, output } = run([cmd, ...args.map(quote)].join(" "));
  if (code !== 0) printLines(buildLines(output), output);
  process.exit(code);
} else {
  rmSync(VITEST_JSON, { force: true });
  const flags = ["--reporter=json", `--outputFile=${VITEST_JSON}`, "--includeTaskLocation", "--passWithNoTests"];
  const { code, output } = run([command, ...flags, ...args.map(quote)].join(" "));
  let report = null;
  try {
    report = existsSync(VITEST_JSON) ? JSON.parse(readFileSync(VITEST_JSON, "utf8")) : null;
  } catch {
    report = null;
  }
  if (code !== 0) printLines(report ? testLines(report) : [], output);
  if (report) {
    console.log(`MUX_SUMMARY ${JSON.stringify({ passed: report.numPassedTests ?? 0, failed: report.numFailedTests ?? 0 })}`);
  }
  process.exit(code);
}
