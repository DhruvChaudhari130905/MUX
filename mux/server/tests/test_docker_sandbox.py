"""The Docker sandbox client (sandbox/docker.py) against a fake `docker` command, so no Docker is needed."""

import io
import stat
import tarfile
from pathlib import Path

import pytest

from mux.sandbox.client import OUTPUT_LIMIT, SandboxError
from mux.sandbox.docker import DockerSandboxClient, _tar, image_ready


def fake_docker(tmp_path: Path, body: str) -> str:
    """A `docker` stand-in: saves its arguments and stdin under tmp_path, then runs `body`."""
    script = tmp_path / "docker"
    script.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{tmp_path}/args"\ncat > "{tmp_path}/stdin"\n{body}\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


async def run(client: DockerSandboxClient, timeout_s: int = 10):
    return await client.run(image="mux-sandbox", files={"/app/src/a.ts": b"export {}\n"}, command="mux-report build x",
                            cwd="/app", disposable=False, timeout_s=timeout_s)


def test_tar_holds_the_files_relative_to_root() -> None:
    with tarfile.open(fileobj=io.BytesIO(_tar({"/app/src/a.ts": b"one", "/app/b.png": b"\x89PNG"}))) as tar:
        names = tar.getnames()
        a = tar.extractfile("app/src/a.ts")
        assert names == ["app/src/a.ts", "app/b.png"] and a is not None and a.read() == b"one"


async def test_runs_one_locked_down_throwaway_container(tmp_path: Path) -> None:
    res = await run(DockerSandboxClient(fake_docker(tmp_path, 'echo built; echo "src/a.ts:1: bad" >&2; exit 3')))
    assert (res.exit_code, res.output, res.snapshot_uuid) == (3, "built\nsrc/a.ts:1: bad\n", None)
    args = (tmp_path / "args").read_text().splitlines()
    assert args[:3] == ["run", "--rm", "-i"] and args[3] == "--name" and args[4].startswith("mux-sandbox-")
    assert "--network" in args and args[args.index("--network") + 1] == "none"
    assert args[-2:] == ["/app", "mux-report build x"] and "mux-sandbox" in args
    with tarfile.open(tmp_path / "stdin") as tar:
        assert tar.getnames() == ["app/src/a.ts"]


async def test_output_keeps_the_end_where_the_summary_is(tmp_path: Path) -> None:
    res = await run(DockerSandboxClient(fake_docker(tmp_path, f"head -c {OUTPUT_LIMIT * 2} /dev/zero | tr '\\0' x; echo; echo MUX_SUMMARY")))
    assert res.output.endswith("MUX_SUMMARY\n") and len(res.output) <= OUTPUT_LIMIT + 1


async def test_docker_failure_is_a_sandbox_error(tmp_path: Path) -> None:
    client = DockerSandboxClient(fake_docker(tmp_path, 'echo "Cannot connect to the Docker daemon" >&2; exit 125'))
    with pytest.raises(SandboxError, match="Docker daemon"):
        await run(client)


async def test_timeout_is_a_sandbox_error(tmp_path: Path) -> None:
    client = DockerSandboxClient(fake_docker(tmp_path, '[ "$1" = rm ] && exit 0; sleep 5'))
    with pytest.raises(SandboxError, match="timed out"):
        await run(client, timeout_s=1)


async def test_missing_docker_is_a_sandbox_error(tmp_path: Path) -> None:
    with pytest.raises(SandboxError, match="docker"):
        await run(DockerSandboxClient(str(tmp_path / "no-docker")))


def test_image_ready_says_why_not(tmp_path: Path) -> None:
    assert image_ready("mux-sandbox", docker=str(tmp_path / "no-docker")) == "the docker command is not installed"
    down = fake_docker(tmp_path, 'echo "Cannot connect to the Docker daemon" >&2; exit 1')
    assert image_ready("mux-sandbox", docker=down) == "Docker is not running"
    missing = fake_docker(tmp_path, 'echo "No such image" >&2; exit 1')
    assert "make sandbox-image" in (image_ready("mux-sandbox", docker=missing) or "")
    assert image_ready("mux-sandbox", docker=fake_docker(tmp_path, "exit 0")) is None
