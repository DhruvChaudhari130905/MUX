"""FastAPI app factory. Mounts the API routers and the WebSocket endpoint, and starts the room registry on startup."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mux import dbsession
from mux.api import rooms, files, commands, export, ws, mcp, ai, skills, account, uploads
from mux.api.ws import emit_event
from mux.events.file_log import FileEventLog, events_root
from mux.events.log import EventLog
from mux.events.models import BaseEvent
import mux.rooms.registry as room_registry
from mux.config import settings
from mux.events.bus import event_bus
from mux.rooms.actor import RoomActor
from mux.rooms.runtime import RoomRuntime
from mux.sandbox.runner import Runner
from mux.secrets import ensure_secrets_key

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Per-room event logs storage
_room_event_logs: dict[str, EventLog] = {}

def get_event_log(room_id: str) -> EventLog:
    """Get or create an event log for a specific room. Events are kept on disk, so rooms survive restarts."""
    if room_id not in _room_event_logs:
        _room_event_logs[room_id] = FileEventLog(room_id, events_root())
    return _room_event_logs[room_id]

# Make get_event_log available for dependency injection
room_registry.get_event_log = get_event_log

STARTER_TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "fullstack-starter"


def sandbox_runner() -> Optional[Runner]:
    """The sandbox for the coder's builds and tests (SANDBOX_PROVIDER), or None when it can't run: the
    coder is then told to check its work by re-reading instead."""
    provider = settings.sandbox_provider.strip().lower()
    if provider == "docker":
        from mux.sandbox.docker import DockerSandboxClient, image_ready

        image = settings.sandbox_image or "mux-sandbox"
        if problem := image_ready(image):
            logger.warning("Docker sandbox is off: %s. The coder can't run builds or tests", problem)
            return None
        logger.info("Builds and tests run in Docker (image %s)", image)
        return Runner(DockerSandboxClient(), image=image)
    if provider == "nebius":
        if not (settings.sandbox_api_key and settings.sandbox_base_url and settings.sandbox_image):
            logger.warning("Nebius sandbox needs SANDBOX_API_KEY, SANDBOX_BASE_URL and SANDBOX_IMAGE: builds are off")
            return None
        from mux.sandbox.client import TokenFactorySandboxClient

        return Runner(TokenFactorySandboxClient(settings.sandbox_api_key, settings.sandbox_base_url),
                      image=settings.sandbox_image)
    if provider != "none":
        logger.warning("Unknown SANDBOX_PROVIDER %r (docker, nebius or none): builds are off", provider)
    return None


def agent_runtime_factory() -> Callable[[RoomActor], RoomRuntime]:
    """The room agents. Each room uses its owner's AI provider, else Token Factory when configured; a room
    with neither has its agents off (RoomLLM.available() is False)."""
    from mux.agents.coder.prompts import load_conventions
    from mux.agents.llm import TokenFactoryLLM
    from mux.agents.room_llm import RoomLLM
    from mux.integrations.tavily import TavilySearch

    default = None
    if settings.token_factory_api_key and settings.token_factory_base_url and settings.model_super:
        default = TokenFactoryLLM()
    else:
        logger.warning("Token Factory is not configured (TOKEN_FACTORY_* / MODEL_SUPER): only rooms with their own AI key run agents")
    conventions = load_conventions(STARTER_TEMPLATE)
    runner = sandbox_runner()

    def make(actor: RoomActor) -> RoomRuntime:
        search = TavilySearch() if settings.tavily_api_key else None  # one per room: the cache is per room
        return RoomRuntime(actor, RoomLLM(actor, default), search=search, runner=runner, conventions=conventions,
                           publish=event_bus.publish_json)

    return make


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting MUX server...")
    ensure_secrets_key()
    await dbsession.init_db()
    logger.info("Database initialized")

    # Initialize room registry with event bus callback for real-time WebSocket broadcasts
    async def on_event_callback(event: BaseEvent) -> None:
        """Send actor events to the room's sockets as catalog envelopes."""
        await emit_event(event.room_id, event)

    # EventLog is per-room; on_event publishes to event bus
    registry = await room_registry.init_registry(on_event=on_event_callback, runtime_factory=agent_runtime_factory())
    await registry.start()
    logger.info("Room registry started")
    yield
    logger.info("Shutting down MUX server...")
    await room_registry.shutdown_registry()
    logger.info("Room registry stopped")
    await dbsession.close_db()

def create_app() -> FastAPI:
    app = FastAPI(
        title="MUX API",
        description="AI-powered collaborative coding platform",
        version="0.1.0",
        lifespan=lifespan
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        # Credentials can't be combined with a wildcard origin; auth uses bearer tokens
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        """Domain validation errors (bad plan items, unknown ids, ...) are client errors."""
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)})

    # The web app's API (architecture.md): /rooms/..., /github/connect, and the room socket /rooms/{id}/ws
    app.include_router(rooms.router, prefix="/rooms", tags=["rooms"])
    app.include_router(account.router, prefix="/me", tags=["account"])
    app.include_router(mcp.router, prefix="/rooms", tags=["mcp"])
    app.include_router(ai.router, prefix="/rooms", tags=["ai"])
    app.include_router(skills.router, prefix="/rooms", tags=["skills"])
    app.include_router(uploads.router, prefix="/rooms", tags=["uploads"])
    app.include_router(rooms.github_router, tags=["github"])
    app.include_router(ws.router, tags=["websocket"])
    # Operator APIs
    app.include_router(files.router, prefix="/api/files", tags=["files"])
    app.include_router(commands.router, prefix="/api/commands", tags=["commands"])
    app.include_router(export.router, prefix="/api/export", tags=["export"])

    @app.get("/")
    async def root():
        return {"message": "MUX API is running"}

    @app.get("/health")
    async def health_check():
        return {"status": "healthy"}

    return app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "mux.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
