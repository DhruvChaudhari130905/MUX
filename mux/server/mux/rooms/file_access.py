"""Coder roles and folder access: who may change a file directly, and who may approve a change to it.

Everyone in a room can read every file. The owner gives coders a role (with folders) and/or folders of their
own; a coder's area is the union of the two. A change outside your area becomes a change request that the
owner, or a coder whose area holds the file, approves. Files in no one's area need the owner. An editor with
no role and no folders is unrestricted, so rooms that never set this up work as before.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal, Optional

from mux.files.room_files import InvalidPath, check_path

Via = Literal["person", "agent"]
RequestStatus = Literal["pending", "approved", "rejected", "withdrawn", "stale"]


def normalize_folder(folder: str) -> str:
    """`" ./web/ "` -> `"web"`. Raises ValueError for empty folders or ones that would leave the project."""
    f = folder.strip()
    while f.startswith("./"):
        f = f[2:]
    f = f.strip("/")
    try:
        return check_path(f)
    except InvalidPath:
        raise ValueError(f"Invalid folder {folder!r}") from None


def covers(folder: str, path: str) -> bool:
    """A folder covers itself and everything under it (`web` covers `web/a.ts`, not `webhooks/a.ts`)."""
    return path == folder or path.startswith(folder + "/")


@dataclass
class AccessRules:
    roles: dict[str, list[str]] = field(default_factory=dict)  # role name -> folders
    member_role: dict[str, str] = field(default_factory=dict)  # user_id -> role name
    member_folders: dict[str, list[str]] = field(default_factory=dict)  # user_id -> personal folders

    def area(self, user_id: str) -> list[str]:
        role = self.member_role.get(user_id, "")
        return [*self.roles.get(role, []), *self.member_folders.get(user_id, [])]

    def is_restricted(self, user_id: str, owner_id: str) -> bool:
        if user_id == owner_id:
            return False
        return user_id in self.member_role or bool(self.member_folders.get(user_id))

    def _in_area(self, user_id: str, path: str) -> bool:
        return any(covers(f, path) for f in self.area(user_id))

    def can_change(self, user_id: str, path: str, owner_id: str) -> bool:
        return not self.is_restricted(user_id, owner_id) or self._in_area(user_id, path)

    def approvers(self, path: str, owner_id: str, editors: Iterable[str]) -> set[str]:
        """The owner plus every coder whose area holds `path`."""
        return {owner_id} | {u for u in editors if self.is_restricted(u, owner_id) and self._in_area(u, path)}

    def can_approve(self, user_id: str, path: str, owner_id: str) -> bool:
        return user_id == owner_id or (self.is_restricted(user_id, owner_id) and self._in_area(user_id, path))


@dataclass
class ChangeRequest:
    """One held change to one file (`content` None deletes it)."""

    id: str
    path: str
    content: Optional[str]
    base_version: Optional[int]
    requested_by: str
    via: Via = "person"
    task_id: Optional[str] = None
    status: RequestStatus = "pending"
    decided_by: Optional[str] = None
    created_at: str = ""


class ChangeHeld(Exception):
    """A change was outside the person's folders, so it was stored as a change request instead."""

    def __init__(self, request: ChangeRequest) -> None:
        super().__init__(f"{request.path} is outside {request.requested_by}'s folders; sent for approval")
        self.request = request
