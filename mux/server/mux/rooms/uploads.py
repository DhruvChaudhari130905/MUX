"""Photos people share in the Team chat: <cwd>/.mux/uploads/<room_id>/<id>.<ext>.

The type comes from the file's first bytes, never its name or the browser's claim, and only raster images
are kept: an SVG or HTML file could run script when opened from the API's address.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from uuid import uuid4

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_ATTACHMENTS = 4

_ID = re.compile(r"^[0-9a-f]{32}$")
_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


def sniff_image(data: bytes) -> Optional[str]:
    """The file extension for PNG, JPEG, GIF or WebP bytes, else None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


class UploadStore:
    """One room's uploaded images."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, data: bytes) -> tuple[str, str]:
        """Store the image; returns (upload id, content type). ValueError if it isn't a supported image."""
        ext = sniff_image(data)
        if ext is None:
            raise ValueError("Only PNG, JPEG, GIF and WebP images can be uploaded")
        upload_id = uuid4().hex
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{upload_id}.{ext}").write_bytes(data)
        return upload_id, _TYPES[ext]

    def find(self, upload_id: str) -> Optional[tuple[Path, str]]:
        """(path, content type) for a stored id, else None. Ids are checked first, so no path escapes."""
        if not _ID.match(upload_id):
            return None
        for ext, content_type in _TYPES.items():
            path = self.root / f"{upload_id}.{ext}"
            if path.is_file():
                return path, content_type
        return None


def room_uploads(room_id: str) -> UploadStore:
    """The store for a room (room ids are checked against ROOM_ID_PATTERN before they get here)."""
    return UploadStore(Path.cwd() / ".mux" / "uploads" / room_id)
