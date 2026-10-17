"""Tests for sandbox builds and tests (sandbox/runner.py, errors.py, client.py) against FakeSandbox."""

from collections.abc import Mapping, Sequence

import pytest

from mux.files.manifest import Entry, Manifest
from mux.replay.fake_sandbox import FakeSandbox
from mux.sandbox import errors
from mux.sandbox.client import RunResult, SandboxError, join_output
from mux.sandbox.runner import (
    BUILD_CMD,
    PROJECT_DIR,
    TEST_CMD,
    InvalidPath,
    InvalidPattern,
    Runner,
    wrap,
)

BLOBS = {"h-app": b"export const App = 1\n", "h-pkg": b"{}\n"}
MANIFEST: Manifest = {"src/App.tsx": Entry("h-app", 2), "package.json": Entry("h-pkg", 1)}
BUILD = wrap("build", BUILD_CMD)
TEST = wrap("test", TEST_CMD)


async def get_blob(h: str) -> bytes:
    return BLOBS[h]


def runner(results: Mapping[str, Sequence[RunResult]]) -> tuple[Runner, FakeSandbox]:
    fake = FakeSandbox(results)
    return Runner(fake, get_blob, image="mux-starter"), fake


def res(code: int, output: str = "") -> RunResult:
    return RunResult(exit_code=code, output=output, snapshot_uuid=None, duration_s=1.5)


class FailingSandbox:
    async def run(self, **_: object) -> RunResult:
        raise SandboxError("operation timed out")


# ---- errors.py ----


def test_dedupe_ignores_noise_colour_and_repeats() -> None:
    out = "\n".join([
        "> vite build",
        "\x1b[31msrc/App.tsx:3: Cannot find name 'x'\x1b[0m",
        "src/App.tsx:3:   cannot   find name 'X'",  # same after normalising
        "src/App.tsx:4: Cannot find name 'x'",
        "Found 2 errors",
    ])
    assert errors.dedupe(out) == ["src/App.tsx:3: Cannot find name 'x'", "src/App.tsx:4: Cannot find name 'x'"]


def test_dedupe_caps_at_five() -> None:
    out = "\n".join(f"a.ts:{i}: bad {i}" for i in range(1, 20))
    assert errors.dedupe(out) == [f"a.ts:{i}: bad {i}" for i in range(1, 6)]


def test_fallback_last_five_non_empty() -> None:
    assert errors.fallback("1\n\n2\n3\n4\n5\n6\n  \n") == ["2", "3", "4", "5", "6"]


def test_join_output_keeps_last_stdout_line_whole() -> None:
    assert join_output("report", "warn\n") == "report\nwarn\n"
    assert join_output(b"out\n", None) == "out\n"
    assert join_output(b"\xff", "") == "�"


# ---- wrap ----


def test_wrap_quotes_every_argument() -> None:
    assert wrap("test", "vitest run", "src/a b.test.ts") == "mux-report test 'vitest run' 'src/a b.test.ts'"


# ---- build ----


async def test_build_pass_keeps_snapshot_and_uploads_under_project_dir() -> None:
    r, fake = runner({BUILD: [res(0)]})
    out = await r.build(MANIFEST)
    assert (out.passed, out.errors, out.snapshot_uuid, out.duration_s) == (True, [], "fake-1", 1.5)
    [call] = fake.calls
    assert call["files"] == {f"{PROJECT_DIR}/src/App.tsx": BLOBS["h-app"], f"{PROJECT_DIR}/package.json": BLOBS["h-pkg"]}
    assert (call["disposable"], call["cwd"], call["image"]) == (False, PROJECT_DIR, "mux-starter")


async def test_build_fail_returns_parsed_errors_and_no_snapshot() -> None:
    r, _ = runner({BUILD: [res(1, "src/App.tsx:1: Type 'string' is not assignable\nbuild failed")]})
    out = await r.build(MANIFEST)
    assert (out.passed, out.snapshot_uuid) == (False, None)
    assert out.errors == ["src/App.tsx:1: Type 'string' is not assignable"]


async def test_build_fail_without_report_lines_uses_raw_tail() -> None:
    r, _ = runner({BUILD: [res(2, "npm ERR! something\nnpm ERR! exit 2")]})
    assert (await r.build(MANIFEST)).errors == ["npm ERR! something", "npm ERR! exit 2"]


async def test_sandbox_error_is_a_failed_build_not_an_exception() -> None:
    out = await Runner(FailingSandbox(), get_blob, image="mux-starter").build(MANIFEST)
    assert (out.passed, out.errors, out.snapshot_uuid) == (False, ["sandbox: operation timed out"], None)


@pytest.mark.parametrize("bad", ["../etc/passwd", "/etc/passwd", "src/../../x", "src//a.ts", "./a.ts", ""])
async def test_paths_that_escape_project_dir_are_refused(bad: str) -> None:
    r, fake = runner({BUILD: [res(0)]})
    with pytest.raises(InvalidPath):
        await r.build({**MANIFEST, bad: Entry("h-app", 1)})
    assert fake.calls == []  # checked before anything is uploaded


# ---- test ----


async def test_tests_pass_with_summary_and_disposable() -> None:
    r, fake = runner({TEST: [res(0, 'ok\nMUX_SUMMARY {"passed": 4, "failed": 0}')]})
    out = await r.test(MANIFEST)
    assert (out.passed, out.passed_count, out.failed_count, out.failures) == (True, 4, 0, [])
    assert fake.calls[0]["disposable"] is True


async def test_tests_fail_lists_failures_not_summary() -> None:
    out_text = 'src/App.test.tsx:9: expected 1 to be 2\nMUX_SUMMARY {"passed": 3, "failed": 1}'
    r, _ = runner({TEST: [res(1, out_text)]})
    out = await r.test(MANIFEST)
    assert (out.passed, out.passed_count, out.failed_count) == (False, 3, 1)
    assert out.failures == ["src/App.test.tsx:9: expected 1 to be 2"]


async def test_bad_summary_gives_none_counts() -> None:
    r, _ = runner({TEST: [res(1, "MUX_SUMMARY {not json\nboom")]})
    out = await r.test(MANIFEST)
    assert (out.passed_count, out.failed_count, out.failures) == (None, None, ["boom"])


async def test_pattern_is_passed_as_one_quoted_argument() -> None:
    r, fake = runner({TEST: [res(0)]})
    await r.test(MANIFEST, "src/App.test.tsx")
    assert fake.calls[0]["command"] == f"{TEST} src/App.test.tsx"


@pytest.mark.parametrize("bad", ["-u", "a.ts; rm -rf /", "$(id)", "a b.ts", "x" * 201])
async def test_unsafe_test_patterns_are_refused(bad: str) -> None:
    r, fake = runner({TEST: [res(0)]})
    with pytest.raises(InvalidPattern):
        await r.test(MANIFEST, bad)
    assert fake.calls == []


async def test_build_files_takes_the_files_themselves() -> None:
    fake = FakeSandbox({BUILD: [res(0)]})
    out = await Runner(fake, image="mux-starter").build_files({"src/a.ts": b"x", "logo.png": b"data:image/png;base64,iVBORw=="})
    assert out.passed and fake.calls[0]["files"] == {f"{PROJECT_DIR}/src/a.ts": b"x", f"{PROJECT_DIR}/logo.png": b"\x89PNG"}


async def test_build_files_refuses_bad_paths() -> None:
    with pytest.raises(InvalidPath):
        await Runner(FakeSandbox({}), image="mux-starter").build_files({"../x": b"x"})
