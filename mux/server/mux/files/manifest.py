"""Manifests (path to hash), version stamps, and diffs between manifests."""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
import difflib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from uuid import UUID, uuid4
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from mux.db.tables import ManifestRow
from mux.events.log import scoped
from mux.events.models import CheckpointCreated, EventEnvelope, FileChanged, RoomId, RoomRewound


@dataclass
class FileEntry:
    """Entry in the file manifest."""
    file_id: str
    path: str
    size: int
    hash: str
    file_type: Optional[str] = None
    version: int = 0


class FileManifest:
    """Tracks file metadata: path -> file_id, size, hash."""

    def __init__(self) -> None:
        self._entries: Dict[str, FileEntry] = {}  # path -> FileEntry
        self._by_id: Dict[str, FileEntry] = {}    # file_id -> FileEntry
        self._checkpoints: Dict[str, Dict[str, Any]] = {}  # checkpoint_id -> checkpoint_data
        self.active_checkpoint_id: Optional[str] = None
        self._export_history: List[Dict[str, Any]] = []    # export history records
        self._room_metadata: Dict[str, Any] = {}           # room name, description
        self._high_water: Dict[str, int] = {}              # path -> highest version ever issued
        self._lock = asyncio.Lock()

    def add(self, file_id: str, path: str, size: int, hash: Optional[str] = None, file_type: Optional[str] = None) -> None:
        """Add or update a file entry."""
        entry = FileEntry(file_id=file_id, path=path, size=size, hash=hash or "", file_type=file_type,
                          version=self._bump(path))
        self._entries[path] = entry
        self._by_id[file_id] = entry

    def _bump(self, path: str) -> int:
        """Issue the next version for a path (never reused, even after a delete or rewind)."""
        version = self._high_water.get(path, 0) + 1
        self._high_water[path] = version
        return version

    def update(self, path: str, size: int, hash: Optional[str] = None) -> None:
        """Update an existing file entry."""
        if path in self._entries:
            entry = self._entries[path]
            entry.size = size
            if hash:
                entry.hash = hash
            entry.version = self._bump(path)

    def remove(self, path: str) -> bool:
        """Remove a file entry. Returns True if existed."""
        if path in self._entries:
            entry = self._entries.pop(path)
            self._by_id.pop(entry.file_id, None)
            return True
        return False

    def get_id(self, path: str) -> Optional[str]:
        """Get file_id for a path."""
        entry = self._entries.get(path)
        return entry.file_id if entry else None

    def get_entry(self, path: str) -> Optional[FileEntry]:
        """Get full entry for a path."""
        return self._entries.get(path)

    def get_by_id(self, file_id: str) -> Optional[FileEntry]:
        """Get entry by file_id."""
        return self._by_id.get(file_id)

    def list_paths(self) -> List[str]:
        """List all tracked file paths."""
        return list(self._entries.keys())

    def list_entries(self) -> List[FileEntry]:
        """List all entries."""
        return list(self._entries.values())

    # Checkpoint methods
    def add_checkpoint(self, checkpoint_id: str, checkpoint_data: Dict[str, Any]) -> None:
        """Store a checkpoint."""
        self._checkpoints[checkpoint_id] = checkpoint_data
        self.active_checkpoint_id = checkpoint_id

    def set_active_checkpoint(self, checkpoint_id: Optional[str]) -> None:
        """Move the room's working branch head without deleting other checkpoints."""
        self.active_checkpoint_id = checkpoint_id

    def get_checkpoint(self, checkpoint_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a checkpoint."""
        return self._checkpoints.get(checkpoint_id)

    def list_checkpoints(self) -> Dict[str, Dict[str, Any]]:
        """List all checkpoints."""
        return dict(self._checkpoints)

    # Room metadata methods
    def set_room_metadata(self, name: Optional[str] = None, description: Optional[str] = None) -> None:
        """Set room name and description."""
        if name is not None:
            self._room_metadata["name"] = name
        if description is not None:
            self._room_metadata["description"] = description

    def get_room_metadata(self) -> Dict[str, Optional[str]]:
        """Get room name and description."""
        return {
            "name": self._room_metadata.get("name"),
            "description": self._room_metadata.get("description"),
        }

    # Export history methods
    def add_export_record(self, record: Dict[str, Any]) -> None:
        """Add an export record to history."""
        self._export_history.append(record)

    def get_export_history(self) -> List[Dict[str, Any]]:
        """Get export history."""
        return list(self._export_history)

    def reset_files(self) -> None:
        """Forget all file entries but keep checkpoints, export history and room metadata."""
        self._entries.clear()
        self._by_id.clear()

    def rebuild_from_checkpoint(self, files_data: Dict[str, str]) -> None:
        """Rebuild manifest from checkpoint file data.

        Regenerates file_ids and computes metadata from content.
        """
        self._entries.clear()
        self._by_id.clear()
        for path, content in files_data.items():
            file_id = str(uuid4())
            size = len(content.encode())
            hash_val = hashlib.sha256(content.encode()).hexdigest()[:16]
            file_type = mimetypes.guess_type(path)[0]
            entry = FileEntry(file_id=file_id, path=path, size=size, hash=hash_val, file_type=file_type,
                              version=self._bump(path))
            self._entries[path] = entry
            self._by_id[file_id] = entry

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, path: str) -> bool:
        return path in self._entries

    @property
    def entries(self) -> dict[str, FileEntry]:
        """Return all file entries as a dict of path -> FileEntry."""
        return dict(self._entries)


# --- Postgres-backed manifests (checkpoints, rewind, room files, coder tools, sandbox runner) ---

@dataclass(frozen=True)
class Entry:
    """One file in a manifest."""

    hash: str
    version: int


Manifest = dict[str, Entry]


@dataclass
class ManifestDiff:
    """Paths only (DB11), sorted."""

    added: list[str]
    removed: list[str]
    changed: list[str]


def diff(a: Manifest, b: Manifest) -> ManifestDiff:
    """Paths added, removed, or with a different hash going from `a` to `b`."""
    return ManifestDiff(
        added=sorted(b.keys() - a.keys()),
        removed=sorted(a.keys() - b.keys()),
        changed=sorted(p for p in a.keys() & b.keys() if a[p].hash != b[p].hash),
    )


def restore_versions(
    live: Manifest, target: Manifest, high_water: Mapping[str, int] | None = None
) -> dict[str, int]:
    """Versions for a rewind (DB4): each target path whose content changes gets high-water + 1.

    Paths with an unchanged hash are left out, so they keep their live version and a coder's
    `base_version` stays valid. Pass `LiveFiles.high_water` so deleted paths count too.
    """
    hw = high_water or {}
    return {
        p: max(hw.get(p, 0), live[p].version if p in live else 0) + 1
        for p, e in target.items()
        if p not in live or live[p].hash != e.hash
    }


def diff_summary(old: str, new: str, max_lines: int = 20) -> str:
    """`+N −M` line counts followed by a unified diff capped at `max_lines` lines (DB11)."""
    lines = [
        ln for ln in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=1)
        if not ln.startswith(("---", "+++"))
    ]
    plus = sum(ln.startswith("+") for ln in lines)
    minus = sum(ln.startswith("-") for ln in lines)
    return "\n".join([f"+{plus} −{minus}", *lines[:max_lines]])


@dataclass
class LiveFiles:
    """The room's live manifest plus per-path version high-water marks (DB4)."""

    manifest: Manifest = field(default_factory=dict)
    high_water: dict[str, int] = field(default_factory=dict)

    def bump(self, path: str, new_hash: str | None) -> int:
        """Record a live edit and return its version. None deletes the path, keeping its high-water mark."""
        if new_hash is None:
            self.manifest.pop(path, None)
            return self.high_water.get(path, 0)
        version = self.high_water.get(path, 0) + 1
        self.manifest[path] = Entry(new_hash, version)
        self.high_water[path] = version
        return version

    def apply_file_changed(self, p: FileChanged) -> None:
        """Replay a `file.changed`: take the recorded version, never recompute it (R2, R3)."""
        if p.deleted or p.hash is None:
            self.manifest.pop(p.path, None)
        else:
            self.manifest[p.path] = Entry(p.hash, p.version)
        self.high_water[p.path] = max(self.high_water.get(p.path, 0), p.version)

    def apply_rewound(self, p: RoomRewound, target: Manifest) -> None:
        """Replay a `room.rewound`: hashes from `target`, versions from the payload."""
        new: Manifest = {}
        for path, e in target.items():
            version = p.versions.get(path)
            if version is None:
                version = self.manifest[path].version if path in self.manifest else e.version
            new[path] = Entry(e.hash, version)
            self.high_water[path] = max(self.high_water.get(path, 0), version)
        self.manifest = new

    @classmethod
    async def replay(
        cls, events: Sequence[EventEnvelope], load_manifest: Callable[[UUID], Awaitable[Manifest]]
    ) -> "LiveFiles":
        """Rebuild from seq 0, applying every event, greyed or not (R3)."""
        live = cls()
        manifest_of: dict[UUID, UUID] = {}
        for e in sorted(events, key=lambda e: e.seq):
            if e.type == "checkpoint.created":
                cc = CheckpointCreated.model_validate(e.payload)
                manifest_of[cc.checkpoint_id] = cc.manifest_id
                if cc.parent_id is None and not live.manifest:  # C0 seeds the live manifest
                    live.manifest = dict(await load_manifest(cc.manifest_id))
                    live.high_water = {p: en.version for p, en in live.manifest.items()}
            elif e.type == "file.changed":
                live.apply_file_changed(FileChanged.model_validate(e.payload))
            elif e.type == "room.rewound":
                rr = RoomRewound.model_validate(e.payload)
                live.apply_rewound(rr, await load_manifest(manifest_of[rr.checkpoint_id]))
        return live


async def save(room_id: RoomId, m: Manifest, *, session: AsyncSession | None = None) -> UUID:
    """Persist a manifest (checkpoints only) and return its id."""
    mid = uuid4()
    async with scoped(session) as s:
        s.add(ManifestRow(id=mid, room_id=room_id, entries={p: {"hash": e.hash, "version": e.version} for p, e in m.items()}))
        await s.flush()
    return mid


async def load(manifest_id: UUID, *, session: AsyncSession | None = None) -> Manifest:
    """Load a manifest by id; raises KeyError if absent."""
    async with scoped(session) as s:
        entries = await s.scalar(select(ManifestRow.entries).where(ManifestRow.id == manifest_id))
    if entries is None:
        raise KeyError(manifest_id)
    return {p: Entry(v["hash"], v["version"]) for p, v in entries.items()}
