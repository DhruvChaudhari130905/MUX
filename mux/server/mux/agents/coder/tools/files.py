"""Coder file tools with line reads and version checks.

Versions are the manifest's numbers (v1, v2, ...) everywhere (Q52):

- `RoomFileTools` is the real one: every change goes through the room's `RoomFiles`, so it is stored as a
  blob, emitted as `file.changed`, and seen by checkpoints, rewind, the code view and other people.
- `FileTools` works on a local folder, for development on a laptop and for tests. Its versions are kept in
  memory: a file starts at v1 the first time it is seen and goes up by one on every change.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from mux.files.binary import data_url_bytes
from mux.files.manifest import LiveFiles
from mux.files.room_files import InvalidPath, RoomFiles, check_path
from mux.rooms.file_access import ChangeHeld

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", "dist"}
CODER = "coder"


class FileToolError(Exception):
    """Base error for coder file tools."""


class StaleVersionError(FileToolError):
    """Raised when a file changed after the coder last read it."""


class FileAlreadyExistsError(FileToolError):
    """Raised when write_file targets an existing file."""


class MissingFileError(FileToolError):
    """Raised when a requested file does not exist."""


class NoMatchError(FileToolError):
    """Raised when an edit find string is not present."""


def _read_result(path: str, content: str, version: int, start_line: int | None, end_line: int | None) -> dict[str, Any]:
    lines = content.splitlines()
    start = 1 if start_line is None else max(1, start_line)
    end = len(lines) if end_line is None else max(start, end_line)
    return {
        "path": path,
        "content": "\n".join(lines[start - 1 : end]),
        "version": version,
        "start_line": start,
        "end_line": min(end, len(lines)),
    }


EDITS_FORMAT = 'edits must be a list of {"find": ..., "replace": ...} objects'


def _normalize_edits(edits: Any) -> list[dict[str, str]] | None:
    """Models sometimes send the edits list JSON-encoded as a string, or one edit on its own; accept both."""
    if isinstance(edits, str):
        try:
            edits = json.loads(edits)
        except ValueError:
            return None
    if isinstance(edits, dict):
        edits = [edits]
    if not isinstance(edits, list) or not all(isinstance(e, dict) for e in edits):
        return None
    return edits


def apply_edits(path: str, content: str, edits: Iterable[dict[str, str]], version: int) -> str | dict[str, Any]:
    """The edited text, or an error result. Each find must match exactly one place."""
    normalized = _normalize_edits(edits)
    if normalized is None:
        return {"ok": False, "error": EDITS_FORMAT, "path": path, "version": version}
    updated = content
    for edit in normalized:
        find = edit.get("find", "")
        replace = edit.get("replace", "")
        if not find:
            raise NoMatchError(f"empty find string for: {path}")
        matches = updated.count(find)
        if matches == 0:
            return {"ok": False, "error": "no match", "path": path, "find": find, "version": version}
        if matches > 1:
            return {
                "ok": False,
                "error": f"find matches {matches} places; include more surrounding text",
                "path": path,
                "find": find,
                "version": version,
            }
        updated = updated.replace(find, replace, 1)
    return updated


def _stale(path: str, current: int | None, error: str = "stale: re-read first") -> dict[str, Any]:
    return {"ok": False, "error": error, "path": path, "current_version": current}


def _list_under(paths: Iterable[str], path: str) -> dict[str, Any]:
    prefix = "" if path in ("", ".", "./") else path.strip("/") + "/"
    return {"ok": True, "files": sorted(p for p in paths if p.startswith(prefix))}


class RoomFileTools:
    """Coder file tools over the room's live files (production)."""

    def __init__(self, files: RoomFiles) -> None:
        self.files = files

    async def _text(self, path: str) -> tuple[str, int]:
        try:
            data, version = await self.files.read(path)
        except InvalidPath as exc:
            raise FileToolError(f"path outside repository: {path}") from exc
        except KeyError as exc:
            raise MissingFileError(f"file not found: {path}") from exc
        try:
            return data.decode("utf-8"), version
        except UnicodeDecodeError as exc:
            raise FileToolError(f"binary file: {path}") from exc

    async def _save(self, path: str, data: bytes | None, base_version: int | None) -> Any:
        try:
            return await self.files.save(path, data, base_version, CODER)
        except InvalidPath as exc:
            raise FileToolError(f"path outside repository: {path}") from exc

    async def read_file(self, path: str, start_line: int | None = None, end_line: int | None = None) -> dict[str, Any]:
        """Read a file and return its content plus version."""
        content, version = await self._text(path)
        return _read_result(path, content, version, start_line, end_line)

    async def write_file(self, path: str, content: str) -> dict[str, Any]:
        """Create a new file. Refuse to overwrite an existing file."""
        res = await self._save(path, content.encode("utf-8"), None)
        if not res.ok:
            raise FileAlreadyExistsError(f"file already exists: {path}")
        return {"ok": True, "path": path, "version": res.version}

    async def edit_file(self, path: str, base_version: int, edits: Iterable[dict[str, str]]) -> dict[str, Any]:
        """Apply find/replace edits only when base_version is current. Nothing changes otherwise."""
        content, current = await self._text(path)
        if base_version != current:
            return _stale(path, current)
        updated = apply_edits(path, content, edits, current)
        if isinstance(updated, dict):
            return updated
        res = await self._save(path, updated.encode("utf-8"), current)
        if not res.ok:  # a person saved between our read and write
            return _stale(path, res.version)
        return {"ok": True, "path": path, "version": res.version, "previous_version": current}

    async def delete_file(self, path: str, base_version: int) -> dict[str, Any]:
        """Delete a file only when its version is still current."""
        current = self.files.version(check_path(path))
        if current is None:
            raise MissingFileError(f"file not found: {path}")
        res = await self._save(path, None, base_version)
        if not res.ok:
            return _stale(path, res.version, "stale")
        return {"ok": True, "path": path, "deleted": True}

    def list_files(self, path: str = ".") -> dict[str, Any]:
        """Return the room's files under path."""
        return _list_under(self.files.live.manifest, path)


class RoomActorFiles(Protocol):
    """The room actor's file API that ActorFileTools needs (mux.rooms.actor.RoomActor)."""

    async def get_file(self, path: str) -> str | None: ...
    async def file_version(self, path: str) -> int | None: ...
    async def list_files(self) -> list[str]: ...
    async def save_checked(self, path: str, content: str | None, base_version: int | None, user_id: str,
                           on_behalf_of: Iterable[str] = (), task_id: str | None = None) -> tuple[str, int | None]: ...


class ActorFileTools:
    """Coder file tools over a running room actor.

    Saves go through `save_checked`, the same atomic, version-checked path as a person's save, so the
    coder can never overwrite a newer human edit, never writes a file someone holds the lock on, and
    every change reaches the room live as file.changed.
    """

    def __init__(
        self, actor: RoomActorFiles, user_id: str = CODER,
        on_behalf_of: Callable[[], Iterable[str]] | None = None, task_id: str | None = None,
    ) -> None:
        self.actor = actor
        self.user_id = user_id
        # Everyone the task is for, asked at every write: a teammate merging in mid-task adds their limits
        self.on_behalf_of = on_behalf_of or (lambda: ())
        self.task_id = task_id

    async def _text(self, path: str) -> tuple[str, int]:
        check = _checked(path)
        content = await self.actor.get_file(check)
        version = await self.actor.file_version(check)
        # A change this task sent for approval reads back as made, so later edits build on it (same request)
        held = self.actor.held_change(self.task_id, check) if hasattr(self.actor, "held_change") else None
        if held is not None and held.content is not None:
            content, version = held.content, version if version is not None else 0
        if content is None or version is None:
            raise MissingFileError(f"file not found: {path}")
        if data_url_bytes(content) is not None:
            raise FileToolError(f"binary file: {path}")
        return content, version

    async def _save(self, path: str, content: str | None, base_version: int | None) -> tuple[str, int | str | None]:
        """("held", request id) when the file is outside the requester's folders and went for approval."""
        try:
            return await self.actor.save_checked(_checked(path), content, base_version, self.user_id,
                                                 on_behalf_of=self.on_behalf_of(), task_id=self.task_id)
        except ChangeHeld as held:
            return "held", held.request.id

    async def read_file(self, path: str, start_line: int | None = None, end_line: int | None = None) -> dict[str, Any]:
        """Read a file and return its content plus version."""
        content, version = await self._text(path)
        return _read_result(path, content, version, start_line, end_line)

    async def write_file(self, path: str, content: str) -> dict[str, Any]:
        """Create a new file. Refuse to overwrite an existing file."""
        outcome, version = await self._save(path, content, None)
        if outcome == "held":
            return _held(path, version)
        if outcome == "stale":
            raise FileAlreadyExistsError(f"file already exists: {path}")
        if outcome == "locked":
            return {"ok": False, "error": "locked: a teammate is editing this file", "path": path}
        return {"ok": True, "path": path, "version": version}

    async def edit_file(self, path: str, base_version: int, edits: Iterable[dict[str, str]]) -> dict[str, Any]:
        """Apply find/replace edits only when base_version is current. Nothing changes otherwise."""
        content, current = await self._text(path)
        if base_version != current:
            return _stale(path, current)
        updated = apply_edits(path, content, edits, current)
        if isinstance(updated, dict):
            return updated
        outcome, version = await self._save(path, updated, current)
        if outcome == "held":
            return _held(path, version)
        if outcome == "locked":
            return {"ok": False, "error": "locked: a teammate is editing this file", "path": path}
        if outcome == "stale":  # a person saved between our read and write
            return _stale(path, version)
        return {"ok": True, "path": path, "version": version, "previous_version": current}

    async def delete_file(self, path: str, base_version: int) -> dict[str, Any]:
        """Delete a file only when its version is still current."""
        if await self.actor.file_version(_checked(path)) is None:
            raise MissingFileError(f"file not found: {path}")
        outcome, version = await self._save(path, None, base_version)
        if outcome == "held":
            return _held(path, version)
        if outcome == "locked":
            return {"ok": False, "error": "locked: a teammate is editing this file", "path": path}
        if outcome == "stale":
            return _stale(path, version, "stale")
        return {"ok": True, "path": path, "deleted": True}

    async def list_files(self, path: str = ".") -> dict[str, Any]:
        """Return the room's files under path."""
        return _list_under(await self.actor.list_files(), path)


def _held(path: str, request_id: Any) -> dict[str, Any]:
    return {
        "ok": True, "held": True, "path": path, "request_id": request_id,
        "message": "This file is outside the folders of the person who asked for this task, so the change was "
                   "sent to the room for approval. Do not retry it or work around it; carry on with the rest of the task.",
    }


def _checked(path: str) -> str:
    try:
        return check_path(path)
    except InvalidPath as exc:
        raise FileToolError(f"path outside repository: {path}") from exc


class FileTools:
    """Coder file tools on a local folder (development and tests only)."""

    def __init__(self, root: str | Path = ".") -> None:
        self.root = Path(root).resolve()
        self._versions = LiveFiles()  # path -> (content hash, version), as last seen on disk

    def _version(self, path: str, content: str | None) -> int | None:
        """The file's version, bumped if the disk content changed since it was last seen."""
        h = None if content is None else hashlib.sha256(content.encode("utf-8")).hexdigest()
        entry = self._versions.manifest.get(path)
        if (entry.hash if entry else None) != h:
            self._versions.bump(path, h)
        entry = self._versions.manifest.get(path)
        return entry.version if entry else None

    def read_file(self, path: str, start_line: int | None = None, end_line: int | None = None) -> dict[str, Any]:
        """Read a file and return its content plus version."""
        file_path = self._resolve(path)
        if not file_path.is_file():
            raise MissingFileError(f"file not found: {path}")
        content = file_path.read_text(encoding="utf-8")
        return _read_result(path, content, self._version(path, content) or 0, start_line, end_line)

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        """Create a new file. Refuse to overwrite an existing file."""
        file_path = self._resolve(path)
        if file_path.exists():
            raise FileAlreadyExistsError(f"file already exists: {path}")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return {"ok": True, "path": path, "version": self._version(path, content)}

    def edit_file(self, path: str, base_version: int, edits: Iterable[dict[str, str]]) -> dict[str, Any]:
        """Apply find/replace edits only when base_version is current. Nothing changes otherwise."""
        file_path = self._resolve(path)
        if not file_path.is_file():
            raise MissingFileError(f"file not found: {path}")
        content = file_path.read_text(encoding="utf-8")
        current = self._version(path, content) or 0
        if base_version != current:
            return _stale(path, current)
        updated = apply_edits(path, content, edits, current)
        if isinstance(updated, dict):
            return updated
        file_path.write_text(updated, encoding="utf-8")
        return {"ok": True, "path": path, "version": self._version(path, updated), "previous_version": current}

    def list_files(self, path: str = ".") -> dict[str, Any]:
        """Return files under path, relative to the repository root."""
        directory = self._resolve(path)
        if not directory.is_dir():
            raise MissingFileError(f"directory not found: {path}")
        files = []
        for file_path in directory.rglob("*"):
            if not file_path.is_file():
                continue
            relative = file_path.relative_to(self.root)
            if any(part in _SKIP_DIRS for part in relative.parts):
                continue
            files.append(relative.as_posix())
        files.sort()
        return {"ok": True, "files": files}

    def delete_file(self, path: str, base_version: int) -> dict[str, Any]:
        """Delete a file only when its version is still current."""
        file_path = self._resolve(path)
        if not file_path.is_file():
            raise MissingFileError(f"file not found: {path}")
        current = self._version(path, file_path.read_text(encoding="utf-8"))
        if base_version != current:
            return _stale(path, current, "stale")
        file_path.unlink()
        self._version(path, None)
        return {"ok": True, "path": path, "deleted": True}

    def _resolve(self, path: str) -> Path:
        """Resolve a path and prevent access outside the repository."""
        candidate = (self.root / path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise FileToolError(f"path outside repository: {path}") from exc
        return candidate
