"""Uploads the current files, runs type-check, build, and tests from the starter image, and records the snapshot UUID (if the sandbox keeps one)."""

import json
import re
import shlex
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from mux.files.binary import file_bytes
from mux.files.manifest import Manifest
from mux.files.room_files import InvalidPath, check_path
from mux.sandbox import errors
from mux.sandbox.client import RunResult, SandboxClient, SandboxError

BUILD_CMD = "tsc --noEmit && vite build"  # DB9 proposal, pending P-Agent-B
TEST_CMD = "vitest run"  # the report script adds --reporter=json --outputFile=<file> itself
# The starter image ships `mux-report` (infra/sandbox-image/mux-report.mjs). It runs the command and
# prints at most 20 `file:line: message` lines, plus `MUX_SUMMARY {"passed": n, "failed": m}` for tests.
REPORT_SCRIPT = "mux-report"
SUMMARY_PREFIX = "MUX_SUMMARY "
TIMEOUT_S = 90  # a cold Docker start plus tsc and vite build on a laptop
PROJECT_DIR = "/app"  # assumption: template root inside the starter image (Q36)
PATTERN_RE = re.compile(r"^[\w./*-]{1,200}$")


class InvalidPattern(ValueError):
    """The test file pattern is not a plain path/glob."""


@dataclass
class BuildResult:
    """Outcome of a build: errors are `file:line: message` lines, at most five."""

    passed: bool
    duration_s: float
    errors: list[str]
    snapshot_uuid: str | None


@dataclass
class TestResult:
    """Outcome of a test run; counts are None when the run printed no summary."""

    __test__ = False  # not a pytest class

    passed: bool
    passed_count: int | None
    failed_count: int | None
    failures: list[str] = field(default_factory=list)


def wrap(mode: Literal["build", "test"], cmd: str, *args: str) -> str:
    """Run `cmd` under the report script in `mode`; `args` are passed on as separate quoted arguments."""
    return " ".join([REPORT_SCRIPT, mode, shlex.quote(cmd), *map(shlex.quote, args)])


def _error_lines(output: str) -> list[str]:
    """The script's report lines, deduped and capped; the last raw lines if it printed none."""
    lines = errors.dedupe(output)
    if lines:
        return lines
    return errors.fallback("\n".join(ln for ln in output.splitlines() if not ln.startswith(SUMMARY_PREFIX)))


def _summary(output: str) -> tuple[int, int] | None:
    for raw in reversed(output.splitlines()):
        if raw.startswith(SUMMARY_PREFIX):
            try:
                d = json.loads(raw[len(SUMMARY_PREFIX):])
                return int(d["passed"]), int(d["failed"])
            except (ValueError, KeyError, TypeError):
                return None
    return None


class Runner:
    """Builds and tests a room's files in the sandbox: a manifest (with `get_blob`) or the files themselves."""

    def __init__(
        self, client: SandboxClient, get_blob: Callable[[str], Awaitable[bytes]] | None = None, *, image: str
    ) -> None:
        self._client = client
        self._get_blob = get_blob
        self._image = image

    async def _files(self, m: Manifest) -> dict[str, bytes]:
        if self._get_blob is None:
            raise TypeError("building a manifest needs get_blob")
        paths = [check_path(p) for p in m]  # validate all before fetching any blob
        return {p: await self._get_blob(m[p].hash) for p in paths}

    async def _run(self, files: Mapping[str, bytes], command: str, *, disposable: bool) -> RunResult:
        """Run `command` on `files` (room path -> stored bytes) under PROJECT_DIR."""
        uploads = {f"{PROJECT_DIR}/{check_path(p)}": file_bytes(data) for p, data in files.items()}
        return await self._client.run(
            image=self._image, files=uploads, command=command, cwd=PROJECT_DIR,
            disposable=disposable, timeout_s=TIMEOUT_S,
        )

    async def build(self, m: Manifest) -> BuildResult:
        """`build_files` on the manifest's files."""
        return await self.build_files(await self._files(m))

    async def test(self, m: Manifest, pattern: str | None = None) -> TestResult:
        """`test_files` on the manifest's files."""
        return await self.test_files(await self._files(m), pattern)

    async def build_files(self, files: Mapping[str, bytes]) -> BuildResult:
        """Type-check and build; keeps the snapshot (`disposable=False`), and its UUID only on success.

        A sandbox failure (timeout, failed operation) is a failed build, not an exception.
        """
        try:
            res = await self._run(files, wrap("build", BUILD_CMD), disposable=False)
        except SandboxError as e:
            return BuildResult(passed=False, duration_s=0.0, errors=[f"sandbox: {e}"], snapshot_uuid=None)
        passed = res.exit_code == 0
        return BuildResult(
            passed=passed,
            duration_s=res.duration_s,
            errors=[] if passed else _error_lines(res.output),
            snapshot_uuid=res.snapshot_uuid if passed else None,
        )

    async def test_files(self, files: Mapping[str, bytes], pattern: str | None = None) -> TestResult:
        """Run Vitest (optionally for one file pattern) in a disposable container."""
        args: list[str] = []
        if pattern is not None:
            if not PATTERN_RE.fullmatch(pattern) or pattern.startswith("-"):
                raise InvalidPattern(pattern)
            args.append(pattern)
        try:
            res = await self._run(files, wrap("test", TEST_CMD, *args), disposable=True)
        except SandboxError as e:
            return TestResult(False, None, None, [f"sandbox: {e}"])
        passed = res.exit_code == 0
        failures = [] if passed else _error_lines(res.output)
        passed_count, failed_count = _summary(res.output) or (None, None)
        return TestResult(passed, passed_count, failed_count, failures)
