"""`SandboxClient` backed by Docker on this machine: one throwaway `docker run` per command."""

import asyncio
import io
import shutil
import subprocess
import tarfile
import time
import uuid
from collections.abc import Mapping

from mux.sandbox.client import OUTPUT_LIMIT, RunResult, SandboxError, join_output

# The coder writes the code that runs here, so the container gets no network, capped memory, CPU and
# processes, and is removed afterwards. Packages come from the image (infra/sandbox-image).
LIMITS = ("--network", "none", "--memory", "1g", "--cpus", "1", "--pids-limit", "256")


def _tail(data: bytes) -> bytes:
    """The last OUTPUT_LIMIT bytes: the report script prints its summary last."""
    return data[-OUTPUT_LIMIT:]


def _tar(files: Mapping[str, bytes]) -> bytes:
    """`files` (absolute path -> bytes) as a tar with paths relative to /, for `tar -x -C /` inside."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path, data in files.items():
            info = tarfile.TarInfo(path.lstrip("/"))
            info.size, info.mode, info.mtime = len(data), 0o644, int(time.time())
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def image_ready(image: str, docker: str = "docker") -> str | None:
    """None when Docker answers and has `image`, else why builds can't run (for the startup log)."""
    if shutil.which(docker) is None:
        return "the docker command is not installed"
    try:
        res = subprocess.run([docker, "image", "inspect", image], stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"docker did not answer ({e})"
    if res.returncode == 0:
        return None
    err = res.stderr.decode("utf-8", errors="replace").strip()
    if "daemon" in err.lower():
        return "Docker is not running"
    return f"image {image} is missing (run `make sandbox-image`)"


class DockerSandboxClient:
    """Runs each command in a fresh container from `image` with the files piped in as a tar.

    There are no snapshots: every run starts from the image, so `snapshot_uuid` is always None.
    """

    def __init__(self, docker: str = "docker") -> None:
        self._docker = docker

    async def run(
        self,
        *,
        image: str,
        files: Mapping[str, bytes],
        command: str,
        cwd: str,
        disposable: bool,
        timeout_s: int,
    ) -> RunResult:
        name = f"mux-sandbox-{uuid.uuid4().hex[:12]}"
        # Unpack the files over the image's project (keeping its node_modules), then run the command
        script = f'tar -x --no-same-owner -m -C / && cd "$0" && exec sh -c "$1"'
        args = [self._docker, "run", "--rm", "-i", "--name", name, *LIMITS, image, "sh", "-c", script, cwd, command]
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            raise SandboxError(f"docker: {e}") from e
        try:
            out, err = await asyncio.wait_for(proc.communicate(_tar(files)), timeout_s)
        except asyncio.TimeoutError:
            await self._remove(name)
            proc.kill()
            await proc.wait()
            raise SandboxError(f"timed out after {timeout_s}s") from None
        # 125: docker itself failed (daemon down, image missing), not the command
        if proc.returncode == 125:
            raise SandboxError(f"docker: {err.decode('utf-8', errors='replace').strip()[-300:] or 'run failed'}")
        return RunResult(
            exit_code=proc.returncode if proc.returncode is not None else 1,
            output=join_output(_tail(out), _tail(err)),
            snapshot_uuid=None,
            duration_s=time.monotonic() - start,
        )

    async def _remove(self, name: str) -> None:
        """Stop a container that ran past its time; `--rm` then deletes it."""
        try:
            proc = await asyncio.create_subprocess_exec(
                self._docker, "rm", "-f", name, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), 15)
        except (OSError, asyncio.TimeoutError):
            pass
