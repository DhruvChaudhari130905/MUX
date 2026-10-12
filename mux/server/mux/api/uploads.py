"""Photos for the Team chat (mux/rooms/uploads.py): editors upload, anyone in the room views."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from mux.api.deps import User, get_room_actor_dep, require_editor, require_viewer
from mux.rooms.actor import RoomActor
from mux.rooms.uploads import MAX_UPLOAD_BYTES, room_uploads

router = APIRouter()


class UploadResult(BaseModel):
    id: str
    content_type: str


@router.post("/{room_id}/uploads", response_model=UploadResult)
async def upload_image(
    room_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(require_editor),
    actor: RoomActor = Depends(get_room_actor_dep),
) -> UploadResult:
    """Store a PNG, JPEG, GIF or WebP image of at most 5 MB; send its id with a Team message."""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Images can be at most 5 MB")
    try:
        upload_id, content_type = room_uploads(actor.room_id).save(data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(e))
    return UploadResult(id=upload_id, content_type=content_type)


@router.get("/{room_id}/uploads/{upload_id}")
async def get_image(
    room_id: str,
    upload_id: str,
    current_user: User = Depends(require_viewer),
    actor: RoomActor = Depends(get_room_actor_dep),
) -> FileResponse:
    """The image, for people in the room only."""
    found = room_uploads(actor.room_id).find(upload_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    path, content_type = found
    return FileResponse(path, media_type=content_type, headers={
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=86400",
    })
