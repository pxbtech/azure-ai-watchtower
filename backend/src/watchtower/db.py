import os
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select
from .config import get_settings
from .models import Base, SchemaMarker, SCHEMA_VERSION


def _ensure_sqlite_dir(db_path: str) -> str:
    path = Path(db_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        test = path.parent / ".write-test"
        test.write_text("ok")
        test.unlink()
    except (PermissionError, OSError):
        return str(Path.cwd() / "watchtower.db")
    return db_path


_settings = get_settings()

if _settings.database_url:
    # Postgres (production). Never wipes on deploy since it's a managed service.
    DATABASE_URL = _settings.database_url
    _is_postgres = True
else:
    # SQLite (local dev fallback)
    _db_path = _ensure_sqlite_dir(_settings.db_path)
    DATABASE_URL = f"sqlite+aiosqlite:///{_db_path}"
    _is_postgres = False

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Create tables. Only reset on schema-version mismatch AND only for SQLite (local dev).
    For Postgres we NEVER drop tables automatically. Bump SCHEMA_VERSION and add a proper
    migration if the schema evolves."""
    async with engine.begin() as conn:
        try:
            await conn.run_sync(Base.metadata.create_all)
        except Exception:
            pass

    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(select(SchemaMarker).limit(1))
            row = result.scalar_one_or_none()
        except Exception:
            row = None

        if row is None:
            session.add(SchemaMarker(id=1, version=SCHEMA_VERSION))
            await session.commit()
            return

        if row.version == SCHEMA_VERSION:
            return

        if _is_postgres:
            # Never drop Postgres. Log the mismatch and continue - manual migration required.
            print(f"WARNING: DB schema version {row.version} != code version {SCHEMA_VERSION}. "
                  "Postgres detected - no automatic drop. Run a manual migration.")
            return

        # SQLite path: safe to drop and recreate (local dev only)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncSessionLocal() as s2:
            s2.add(SchemaMarker(id=1, version=SCHEMA_VERSION))
            await s2.commit()


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
