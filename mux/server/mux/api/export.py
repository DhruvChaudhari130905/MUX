"""Connect GitHub (separate OAuth step with repo scope) and export the current checkpoint to a repo."""

from __future__ import annotations

import logging
import time
from typing import Optional
from urllib.parse import urlencode

import httpx

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from mux.api.deps import get_room_actor_dep, get_current_user, require_owner, require_viewer, User
from mux.config import settings
from mux.rooms.actor import RoomActor
from mux.integrations.github import get_github_integration, GitHubExportResult

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================

class CheckpointCreateRequest(BaseModel):
    """Request to create a checkpoint."""
    description: Optional[str] = Field(None, max_length=500, description="Description of what this checkpoint saves")
    include_files: bool = Field(True, description="Include file contents in checkpoint")
    include_plan: bool = Field(True, description="Include plan in checkpoint")


class CheckpointCreateResponse(BaseModel):
    """Response after creating a checkpoint."""
    checkpoint_id: str
    description: Optional[str]
    sequence: int
    files_count: int
    plan_items: int
    created_at: float
    created_by: str


class CheckpointListResponse(BaseModel):
    """List of available checkpoints."""
    checkpoints: list[dict]


class ExportRequest(BaseModel):
    """Request to export a checkpoint to GitHub."""
    checkpoint_id: Optional[str] = Field(None, description="Specific checkpoint to export (latest if omitted)")
    github_owner: str = Field(..., description="GitHub username or organization")
    github_repo: str = Field(..., description="Repository name")
    branch: str = Field("main", description="Target branch")
    commit_message: Optional[str] = Field(None, description="Custom commit message")
    path_prefix: str = Field("", description="Path prefix in repo (e.g., 'mux-exports/')")


class ExportResponse(BaseModel):
    """Response after exporting to GitHub."""
    checkpoint_id: str
    github_url: str
    commit_sha: str
    files_exported: int
    exported_at: float


class GitHubConnectResponse(BaseModel):
    """Response after initiating GitHub OAuth."""
    auth_url: str
    state: str


class GitHubStatusResponse(BaseModel):
    """GitHub connection status for a user."""
    connected: bool
    username: Optional[str]
    scopes: list[str]


# =============================================================================
# Helper: Get RoomActor for a room
# =============================================================================

# Shared dependency: looks up (or rehydrates) an existing room; never creates one.
get_room_actor = get_room_actor_dep


# =============================================================================
# Checkpoint Endpoints
# =============================================================================

@router.post("/{room_id}/checkpoints", response_model=CheckpointCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_checkpoint(
    room_id: str,
    request: CheckpointCreateRequest,
    current_user: User = Depends(require_owner),
    actor: RoomActor = Depends(get_room_actor),
) -> CheckpointCreateResponse:
    """
    Create a checkpoint of the current room state (owner only).
    Includes plan, files, and current event sequence.
    """
    checkpoint_id = await actor.create_checkpoint(
        user_id=current_user.id,
        description=request.description,
        include_files=request.include_files,
        include_plan=request.include_plan,
    )

    # Get checkpoint data for response
    checkpoint_data = actor.manifest.get_checkpoint(checkpoint_id)
    files_count = len(checkpoint_data.get("files", {})) if checkpoint_data else 0
    plan_items = len(checkpoint_data.get("plan", [])) if checkpoint_data else 0
    checkpoint_sequence = checkpoint_data.get("sequence", 0) if checkpoint_data else 0

    return CheckpointCreateResponse(
        checkpoint_id=checkpoint_id,
        description=request.description,
        sequence=checkpoint_sequence,  # Sequence at the time of checkpoint creation
        files_count=files_count,
        plan_items=plan_items,
        created_at=checkpoint_data.get("created_at", 0) if checkpoint_data else 0,
        created_by=current_user.id,
    )


@router.get("/{room_id}/checkpoints", response_model=CheckpointListResponse)
async def list_checkpoints(
    room_id: str,
    current_user: User = Depends(require_viewer),
    actor: RoomActor = Depends(get_room_actor),
) -> CheckpointListResponse:
    """
    List all available checkpoints for a room.
    """
    checkpoints = []
    for cp_id, cp_data in actor.manifest.list_checkpoints().items():
        checkpoints.append({
            "checkpoint_id": cp_id,
            "description": cp_data.get("description"),
            "sequence": cp_data.get("sequence"),
            "files_count": len(cp_data.get("files", {})),
            "plan_items": len(cp_data.get("plan", [])),
            "created_at": cp_data.get("created_at"),
            "created_by": cp_data.get("created_by"),
            "parent_id": cp_data.get("parent_id"),
            "includes_files": cp_data.get("includes_files", True),
            "includes_plan": cp_data.get("includes_plan", True),
        })

    # Sort by created_at descending (newest first)
    checkpoints.sort(key=lambda x: x.get("created_at", 0), reverse=True)

    return CheckpointListResponse(checkpoints=checkpoints)


@router.get("/{room_id}/checkpoints/{checkpoint_id}")
async def get_checkpoint(
    room_id: str,
    checkpoint_id: str,
    current_user: User = Depends(require_viewer),
    actor: RoomActor = Depends(get_room_actor),
) -> dict:
    """
    Get full checkpoint data (plan + files).
    """
    checkpoint_data = actor.manifest.get_checkpoint(checkpoint_id)
    if not checkpoint_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Checkpoint not found: {checkpoint_id}"
        )
    return checkpoint_data


@router.post("/{room_id}/checkpoints/{checkpoint_id}/restore")
async def restore_checkpoint(
    room_id: str,
    checkpoint_id: str,
    current_user: User = Depends(require_owner),
    actor: RoomActor = Depends(get_room_actor),
) -> dict:
    """
    Restore room state from a checkpoint (owner only).
    """
    success = await actor.restore_checkpoint(checkpoint_id, current_user.id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Checkpoint not found or restore failed: {checkpoint_id}"
        )
    return {"restored": True, "checkpoint_id": checkpoint_id, "sequence": actor._state.sequence}


# =============================================================================
# GitHub Export Endpoints
# =============================================================================

@router.post("/{room_id}/export", response_model=ExportResponse)
async def export_checkpoint(
    room_id: str,
    request: ExportRequest,
    current_user: User = Depends(require_owner),
    actor: RoomActor = Depends(get_room_actor),
) -> ExportResponse:
    """
    Export a checkpoint to a GitHub repository (owner only).

    Requires GitHub OAuth to be connected with 'repo' scope.
    If checkpoint_id is omitted, exports the latest checkpoint.
    """
    # Determine which checkpoint to export
    if request.checkpoint_id:
        checkpoint_data = actor.manifest.get_checkpoint(request.checkpoint_id)
        if not checkpoint_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Checkpoint not found: {request.checkpoint_id}"
            )
        export_checkpoint_id = request.checkpoint_id
    else:
        # Use latest checkpoint
        checkpoints = list(actor.manifest.list_checkpoints().items())
        if not checkpoints:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No checkpoints available to export"
            )
        # Get most recent
        export_checkpoint_id, checkpoint_data = max(checkpoints, key=lambda x: x[1].get("created_at", 0))

    # Use GitHub integration to export
    github = get_github_integration()
    try:
        result: GitHubExportResult = await github.export_checkpoint(
            user_id=current_user.id,
            checkpoint_data=checkpoint_data,
            github_owner=request.github_owner,
            github_repo=request.github_repo,
            branch=request.branch,
            commit_message=request.commit_message,
            path_prefix=request.path_prefix,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.exception(f"GitHub export failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Export failed: {str(e)}"
        )

    exported_at = time.time()

    # Record export history (previously unreachable: it came after the return)
    actor.manifest.add_export_record({
        "checkpoint_id": export_checkpoint_id,
        "github_owner": request.github_owner,
        "github_repo": request.github_repo,
        "branch": request.branch,
        "commit_sha": result.commit_sha,
        "html_url": result.html_url,
        "files_exported": result.files_pushed,
        "exported_at": exported_at,
        "exported_by": current_user.id,
    })

    return ExportResponse(
        checkpoint_id=export_checkpoint_id,
        github_url=result.html_url,
        commit_sha=result.commit_sha,
        files_exported=result.files_pushed,
        exported_at=exported_at,
    )


# =============================================================================
# GitHub OAuth Endpoints
# =============================================================================

@router.get("/github/connect", response_model=GitHubConnectResponse)
async def github_connect(
    current_user: User = Depends(get_current_user),
) -> GitHubConnectResponse:
    """
    Initiate GitHub OAuth flow for the current user.
    Returns the authorization URL to redirect the user to.
    """
    github = get_github_integration()
    auth_url, state = github.get_authorization_url(current_user.id)
    return GitHubConnectResponse(auth_url=auth_url, state=state)


@router.get("/github/callback")
async def github_callback(
    code: str = Query(..., description="Authorization code from GitHub"),
    state: str = Query(..., description="State parameter from GitHub"),
) -> RedirectResponse:
    """
    Handle GitHub OAuth callback.
    Exchanges code for token, stores it with the user's GitHub login, and sends the browser back to
    the app page that started the flow (the profile page by default) with ?github=connected
    (or ?github=error&message=...).
    """
    github = get_github_integration()
    # The state must be one this server issued in /github/connect; the user id comes
    # from server-side storage, never from the (attacker-controllable) state value.
    entry = github.consume_state_and_return(state)
    return_to = (entry[1] if entry else None) or "/profile"

    def back_to_app(**params: str) -> RedirectResponse:
        joiner = "&" if "?" in return_to else "?"
        return RedirectResponse(f"{settings.web_app_url.rstrip('/')}{return_to}{joiner}{urlencode(params)}")

    if not entry:
        return back_to_app(github="error", message="The GitHub sign-in expired or was already used. Try connecting again.")
    user_id = entry[0]

    try:
        token_data = await github.exchange_code_for_token(code, state)
        profile = await github.get_user_info(token_data.access_token)
        github.store_token(user_id, token_data, username=profile.login)
    except (ValueError, httpx.HTTPError) as e:
        logger.warning(f"GitHub callback failed: {e}")
        return back_to_app(github="error", message=f"GitHub connection failed: {e}")
    return back_to_app(github="connected", username=profile.login)


@router.get("/github/status", response_model=GitHubStatusResponse)
async def github_status(
    current_user: User = Depends(get_current_user),
) -> GitHubStatusResponse:
    """
    Check if the current user has GitHub connected.
    """
    github = get_github_integration()
    status_data = github.get_connection_status(current_user.id)
    return GitHubStatusResponse(**status_data)


@router.post("/github/disconnect")
async def github_disconnect(
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Disconnect GitHub account (revoke stored token).
    """
    github = get_github_integration()
    github.delete_token(current_user.id)
    return {"disconnected": True}


# =============================================================================
# Export History
# =============================================================================

@router.get("/{room_id}/exports")
async def list_exports(
    room_id: str,
    current_user: User = Depends(require_viewer),
    actor: RoomActor = Depends(get_room_actor),
) -> dict:
    """
    List export history for a room.
    """
    # Get export history from manifest
    exports = actor.manifest.get_export_history()
    return {"exports": exports}
