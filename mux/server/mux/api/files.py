"""Manual code editing: open a file for editing (takes the soft lock), save with version check, release the lock."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from mux.files.room_files import InvalidPath, check_path
from mux.api.deps import get_room_actor_dep, get_current_user, require_editor, require_owner, require_viewer, User
from mux.rooms.actor import RoomActor

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================

class FileCreateRequest(BaseModel):
    """Request to create a file."""
    path: str = Field(..., min_length=1, max_length=500, description="File path")
    content: str = Field("", description="Initial file content")
    file_type: Optional[str] = Field(None, description="MIME type or file extension")


class FileCreateResponse(BaseModel):
    """Response after creating a file."""
    file_id: str
    path: str
    name: str
    size: int
    sequence: int


class FileUpdateRequest(BaseModel):
    """Request to update a file."""
    path: str = Field(..., min_length=1, max_length=500, description="File path")
    content: str = Field(..., description="New file content")


class FileUpdateResponse(BaseModel):
    """Response after updating a file."""
    file_id: str
    path: str
    size: int
    sequence: int


class FileDeleteResponse(BaseModel):
    """Response after deleting a file."""
    path: str
    sequence: int
    deleted: bool


class FileReadResponse(BaseModel):
    """File content response."""
    file_id: Optional[str]
    path: str
    name: str
    content: str
    size: int
    file_type: Optional[str]


class FileListResponse(BaseModel):
    """List of files in the room."""
    files: list[dict]


class FileLockRequest(BaseModel):
    """Request to lock a file for editing."""
    path: str = Field(..., min_length=1, max_length=500, description="File path")


class FileLockResponse(BaseModel):
    """Response after locking a file."""
    path: str
    locked: bool
    locked_by: Optional[str]
    sequence: int


class FileUnlockResponse(BaseModel):
    """Response after unlocking a file."""
    path: str
    unlocked: bool
    sequence: int


class FileLockStatusResponse(BaseModel):
    """File lock status."""
    path: str
    locked: bool
    locked_by: Optional[str]
    acquired_at: Optional[float]


# =============================================================================
# Helper: Get RoomActor for a room
# =============================================================================

# Shared dependency: looks up (or rehydrates) an existing room; never creates one.
get_room_actor = get_room_actor_dep


# =============================================================================
# File CRUD Endpoints
# =============================================================================

@router.post("/{room_id}/files", response_model=FileCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_file(
    room_id: str,
    request: FileCreateRequest,
    current_user: User = Depends(require_editor),
    actor: RoomActor = Depends(get_room_actor),
) -> FileCreateResponse:
    """
    Create a new file in the room.
    Requires editor permission.
    """
    path = request.path.lstrip("/")
    try:
        check_path(path)
    except InvalidPath:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Not a file path in the project: {path}")
    if not actor.can_change(current_user.id, path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Outside your folders: save it from the room's editor to send it for approval",
        )
    if path in actor.manifest:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"File already exists: {path}"
        )

    file_id = await actor.create_file(path, request.content, current_user.id)

    manifest_entry = actor.manifest.get_by_id(file_id)
    name = path.split("/")[-1]
    size = manifest_entry.size if manifest_entry else len(request.content.encode())

    return FileCreateResponse(
        file_id=file_id,
        path=path,
        name=name,
        size=size,
        sequence=actor._state.sequence,
    )


@router.get("/{room_id}/files", response_model=FileListResponse)
async def list_files(
    room_id: str,
    current_user: User = Depends(require_viewer),
    actor: RoomActor = Depends(get_room_actor),
) -> FileListResponse:
    """
    List all files in the room.
    Requires viewer permission.
    """
    paths = await actor.list_files()
    files = []
    for path in paths:
        entry = actor.manifest.get_entry(path)
        files.append({
            "file_id": entry.file_id if entry else None,
            "path": path,
            "name": path.split("/")[-1],
            "size": entry.size if entry else 0,
            "file_type": entry.file_type if entry else None,
            "locked": await actor.is_locked(path),
            "locked_by": await actor.locked_by(path),
        })
    return FileListResponse(files=files)


# Registered before read_file: the {file_path:path} route would otherwise swallow '/lock'
@router.get("/{room_id}/files/{file_path:path}/lock", response_model=FileLockStatusResponse)
async def get_lock_status(
    room_id: str,
    file_path: str,
    current_user: User = Depends(require_viewer),
    actor: RoomActor = Depends(get_room_actor),
) -> FileLockStatusResponse:
    """
    Get lock status for a file.
    """
    path = file_path.lstrip("/")

    locked = await actor.is_locked(path)
    locked_by = await actor.locked_by(path) if locked else None

    # Get acquired time from lock manager
    acquired_at = None
    if locked:
        lock_info = await actor.locks.get_lock(path)
        if lock_info:
            acquired_at = lock_info.acquired_at

    return FileLockStatusResponse(
        path=path,
        locked=locked,
        locked_by=locked_by,
        acquired_at=acquired_at,
    )


@router.get("/{room_id}/files/{file_path:path}", response_model=FileReadResponse)
async def read_file(
    room_id: str,
    file_path: str,
    current_user: User = Depends(require_viewer),
    actor: RoomActor = Depends(get_room_actor),
) -> FileReadResponse:
    """
    Read a file's content.
    Requires viewer permission.
    """
    # Ensure path starts without leading slash
    path = file_path.lstrip("/")

    content = await actor.get_file(path)
    if content is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {path}"
        )

    entry = actor.manifest.get_entry(path)

    return FileReadResponse(
        file_id=entry.file_id if entry else None,
        path=path,
        name=path.split("/")[-1],
        content=content,
        size=entry.size if entry else len(content.encode()),
        file_type=entry.file_type if entry else None,
    )


@router.put("/{room_id}/files/{file_path:path}", response_model=FileUpdateResponse)
async def update_file(
    room_id: str,
    file_path: str,
    request: FileUpdateRequest,
    current_user: User = Depends(require_editor),
    actor: RoomActor = Depends(get_room_actor),
) -> FileUpdateResponse:
    """
    Update a file's content.
    Requires editor permission.
    Note: If file is locked by another user, this will fail.
    """
    path = file_path.lstrip("/")
    try:
        check_path(path)
    except InvalidPath:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Not a file path in the project: {path}")
    if not actor.can_change(current_user.id, path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Outside your folders: save it from the room's editor to send it for approval",
        )

    # Check if locked by another user
    if await actor.is_locked(path):
        locked_by = await actor.locked_by(path)
        if locked_by != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"File is locked by another user: {locked_by}"
            )

    await actor.update_file(path, request.content, current_user.id)

    entry = actor.manifest.get_entry(path)

    return FileUpdateResponse(
        file_id=entry.file_id if entry else "",
        path=path,
        size=entry.size if entry else len(request.content.encode()),
        sequence=actor._state.sequence,
    )


@router.delete("/{room_id}/files/{file_path:path}", response_model=FileDeleteResponse)
async def delete_file(
    room_id: str,
    file_path: str,
    current_user: User = Depends(require_owner),
    actor: RoomActor = Depends(get_room_actor),
) -> FileDeleteResponse:
    """
    Delete a file (owner only).
    """
    path = file_path.lstrip("/")

    # Check if file exists
    content = await actor.get_file(path)
    if content is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {path}"
        )

    await actor.delete_file(path, current_user.id)

    return FileDeleteResponse(
        path=path,
        sequence=actor._state.sequence,
        deleted=True,
    )


# =============================================================================
# File Lock Endpoints
# =============================================================================

@router.post("/{room_id}/files/{file_path:path}/lock", response_model=FileLockResponse)
async def lock_file(
    room_id: str,
    file_path: str,
    current_user: User = Depends(require_editor),
    actor: RoomActor = Depends(get_room_actor),
) -> FileLockResponse:
    """
    Lock a file for editing (soft lock).
    Requires editor permission.
    """
    path = file_path.lstrip("/")

    locked = await actor.lock_file(path, current_user.id)

    locked_by = await actor.locked_by(path) if locked else None

    return FileLockResponse(
        path=path,
        locked=locked,
        locked_by=locked_by,
        sequence=actor._state.sequence,
    )


@router.post("/{room_id}/files/{file_path:path}/unlock", response_model=FileUnlockResponse)
async def unlock_file(
    room_id: str,
    file_path: str,
    current_user: User = Depends(require_editor),
    actor: RoomActor = Depends(get_room_actor),
) -> FileUnlockResponse:
    """
    Unlock a file (only by the user who locked it, or owner).
    Requires editor permission.
    """
    path = file_path.lstrip("/")

    # Check if user can unlock
    locked_by = await actor.locked_by(path)
    if locked_by and locked_by != current_user.id:
        # Check if current user is owner
        if current_user.id != actor.owner_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the lock holder or room owner can unlock"
            )

    unlocked = await actor.unlock_file(path, current_user.id)

    return FileUnlockResponse(
        path=path,
        unlocked=unlocked,
        sequence=actor._state.sequence,
    )
