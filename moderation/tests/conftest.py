import sys
import uuid
from pathlib import Path

# Ensure `shared` package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.models import Moderator, ModeratorRole
from shared.auth.jwt import create_access_token


@pytest_asyncio.fixture
async def test_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Patch JSONB -> JSON for SQLite compatibility
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine):
    async_session = async_sessionmaker(test_engine, expire_on_commit=False)
    async with async_session() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def moderator(db_session) -> Moderator:
    """Create a test moderator."""
    mod = Moderator(
        email="moderator@test.com",
        hashed_password=hash_password("testpass1234"),
        first_name="Test",
        last_name="Moderator",
        role=ModeratorRole.MODERATOR,
        is_active=True,
    )
    db_session.add(mod)
    await db_session.flush()
    return mod


@pytest_asyncio.fixture
async def moderator_token(moderator: Moderator) -> str:
    """JWT token for the test moderator."""
    return create_access_token(
        subject=str(moderator.id),
        secret_key=settings.secret_key,
        algorithm=settings.algorithm,
        expires_minutes=settings.access_token_expire_minutes,
    )


@pytest_asyncio.fixture
async def auth_headers(moderator_token: str) -> dict[str, str]:
    """Headers with Authorization bearer token."""
    return {"Authorization": f"Bearer {moderator_token}"}


@pytest_asyncio.fixture
async def second_moderator(db_session) -> Moderator:
    """A second moderator for concurrent tests."""
    mod = Moderator(
        email="moderator2@test.com",
        hashed_password=hash_password("testpass1234"),
        first_name="Second",
        last_name="Mod",
        role=ModeratorRole.MODERATOR,
        is_active=True,
    )
    db_session.add(mod)
    await db_session.flush()
    return mod


@pytest_asyncio.fixture
async def admin_moderator(db_session) -> Moderator:
    """An admin moderator for admin-only endpoint tests."""
    mod = Moderator(
        email="admin@test.com",
        hashed_password=hash_password("adminpass1234"),
        first_name="Admin",
        last_name="User",
        role=ModeratorRole.ADMIN,
        is_active=True,
    )
    db_session.add(mod)
    await db_session.flush()
    return mod


@pytest_asyncio.fixture
async def admin_token(admin_moderator: Moderator) -> str:
    return create_access_token(
        subject=str(admin_moderator.id),
        secret_key=settings.secret_key,
        algorithm=settings.algorithm,
        expires_minutes=settings.access_token_expire_minutes,
    )


@pytest_asyncio.fixture
async def admin_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest_asyncio.fixture
async def second_auth_headers(second_moderator: Moderator) -> dict[str, str]:
    token = create_access_token(
        subject=str(second_moderator.id),
        secret_key=settings.secret_key,
        algorithm=settings.algorithm,
        expires_minutes=settings.access_token_expire_minutes,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
