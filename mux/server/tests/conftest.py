"""Shared fixtures: test database URL, Alembic-migrated schema, per-test session."""

import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

os.environ.setdefault("TEST_DATABASE_URL", "postgresql+asyncpg://mux:mux@localhost:5433/mux_test")

from mux.config import settings  # noqa: E402  (after the env default above)
from mux.db.session import make_engine  # noqa: E402
from mux.db.tables import Base  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

SERVER_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_legacy_secret_settings(monkeypatch):
    """Tests set ROOM_SECRETS_KEY / ALLOW_PRIVATE_URLS; the old names (still read as fallbacks) start empty."""
    monkeypatch.setattr(settings, "mcp_encryption_key", "")
    monkeypatch.setattr(settings, "mcp_allow_private_urls", False)


@pytest.fixture(autouse=True)
def _no_sandbox(monkeypatch):
    """App startup never reaches the real Docker or Nebius: tests that build pass their own fake runner."""
    monkeypatch.setattr(settings, "sandbox_provider", "none")


@pytest.fixture(autouse=True)
def _env_file_in_tmp(tmp_path, monkeypatch):
    """Server startup may write a new ROOM_SECRETS_KEY to .env: never the real one in tests."""
    import mux.secrets
    monkeypatch.setattr(mux.secrets, "ENV_FILE", tmp_path / ".env")


def check_test_database_url(url: str, database_url: str) -> str:
    """Return `url` only if it is safe to wipe: a database whose name contains "test", not DATABASE_URL.

    The fixtures below run `alembic downgrade base` and TRUNCATE every table, so a mistyped .env must fail
    loudly instead of dropping a real database.
    """
    name = make_url(url).database or "" if url else ""
    if not name or "test" not in name.lower():
        raise RuntimeError(f"TEST_DATABASE_URL must name a test database (got {name or 'nothing'!r})")
    if database_url and make_url(url) == make_url(database_url):
        raise RuntimeError("TEST_DATABASE_URL must not be the same database as DATABASE_URL")
    return url


def safe_test_database_url() -> str:
    """The checked test database URL."""
    return check_test_database_url(settings.test_database_url, settings.database_url)


def alembic_config() -> Config:
    """Alembic config pointed at the test database."""
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", safe_test_database_url())
    return cfg


@pytest.fixture
def room_id() -> UUID:
    """A fresh room id."""
    return uuid4()


@pytest.fixture(scope="session")
def migrated() -> None:
    """Rebuild the test schema from migrations (downgrade then upgrade). Sync: env.py runs its own loop."""
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture
async def db_session(migrated: None) -> AsyncIterator[AsyncSession]:
    """A session on a NullPool engine; all tables are truncated after the test."""
    engine = make_engine(safe_test_database_url(), poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
        names = ", ".join(t.name for t in Base.metadata.sorted_tables)
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {names} CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture
async def room(db_session: AsyncSession, room_id: UUID) -> UUID:
    """Insert a room row (events, manifests etc. have FKs to it) and return its id."""
    from mux.db.tables import Room

    db_session.add(Room(id=room_id, owner_id=uuid4(), title="t"))
    await db_session.flush()
    return room_id
