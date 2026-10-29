"""Real Docker sandbox integration tests.

These run a real throwaway container per command against the `mux-sandbox` image.
They are slower than the scripted suite and need Docker + the image, so they are
gated behind the `docker` marker and run via `make test-docker` (required before
every release).

Local development: `pytest -q -m "not docker"` skips these for quick iteration.
"""

import pytest

from mux.sandbox.client import SandboxError
from mux.sandbox.docker import DockerSandboxClient, image_ready


pytestmark = pytest.mark.docker

IMAGE = "mux-sandbox"


def _docker_available() -> str | None:
    return image_ready(IMAGE)


@pytest.fixture
def client() -> DockerSandboxClient:
    problem = _docker_available()
    if problem:
        pytest.skip(f"real Docker sandbox unavailable: {problem}")
    return DockerSandboxClient()


async def test_real_docker_build_passes(client: DockerSandboxClient) -> None:
    files = {"/app/src/app.ts": b"export const x: number = 1\n"}
    res = await client.run(
        image=IMAGE,
        files=files,
        command="mux-report build 'tsc --noEmit'",
        cwd="/app",
        disposable=False,
        timeout_s=90,
    )
    assert res.exit_code == 0, res.output


async def test_real_docker_build_fails_reports_errors(client: DockerSandboxClient) -> None:
    files = {"/app/src/app.ts": b"export const x: number = 'not a number'\n"}
    res = await client.run(
        image=IMAGE,
        files=files,
        command="mux-report build 'tsc --noEmit'",
        cwd="/app",
        disposable=False,
        timeout_s=90,
    )
    assert res.exit_code != 0
    assert "app.ts" in res.output


async def test_real_docker_tests_pass(client: DockerSandboxClient) -> None:
    files = {
        "/app/src/math.ts": b"export const add = (a: number, b: number) => a + b\n",
        "/app/src/math.test.ts": (
            b"import { add } from './math'\n"
            b"import { expect, test } from 'vitest'\n"
            b"test('adds', () => expect(add(1, 2)).toBe(3))\n"
        ),
    }
    res = await client.run(
        image=IMAGE,
        files=files,
        command="mux-report test 'vitest run'",
        cwd="/app/src",
        disposable=True,
        timeout_s=90,
    )
    assert res.exit_code == 0, res.output
    assert "MUX_SUMMARY" in res.output


async def test_real_docker_tests_fail_reports_failures(client: DockerSandboxClient) -> None:
    files = {
        "/app/src/failing.test.ts": (
            b"import { expect, test } from 'vitest'\n"
            b"test('fails', () => expect(1).toBe(2))\n"
        ),
    }
    res = await client.run(
        image=IMAGE,
        files=files,
        command="mux-report test 'vitest run'",
        cwd="/app/src",
        disposable=True,
        timeout_s=90,
    )
    assert res.exit_code != 0
    assert "MUX_SUMMARY" in res.output


async def test_real_docker_timeout_is_sandbox_error(client: DockerSandboxClient) -> None:
    with pytest.raises(SandboxError, match="timed out"):
        await client.run(
            image=IMAGE,
            files={},
            command="sleep 10",
            cwd="/app",
            disposable=False,
            timeout_s=1,
        )


async def test_real_docker_missing_image_is_sandbox_error() -> None:
    client = DockerSandboxClient()
    with pytest.raises(SandboxError):
        await client.run(
            image="mux-sandbox-does-not-exist",
            files={},
            command="echo hi",
            cwd="/app",
            disposable=False,
            timeout_s=10,
        )
