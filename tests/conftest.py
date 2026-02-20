"""Shared pytest fixtures for the PingWatcher test suite.

Every test gets a fresh in-memory SQLite database so tests are fully
isolated and repeatable.
"""

import os

# Force an in-memory DB before any application code reads the setting.
os.environ["PINGWATCHER_DATABASE_URL"] = "sqlite://"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pingwatcher.db.models import Base, get_db
from pingwatcher.main import app


@pytest.fixture()
def db_engine():
    """Create an isolated in-memory SQLite engine with all tables.

    Uses :class:`StaticPool` so every connection returned by the pool
    points to the **same** in-memory database, ensuring tables created
    by ``create_all`` are visible to subsequent sessions.

    Yields:
        A :class:`sqlalchemy.engine.Engine`.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    """Provide a scoped database session for a single test.

    Yields:
        A :class:`sqlalchemy.orm.Session` that is rolled back and
        closed when the test finishes.
    """
    Session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    session = Session()
    yield session
    session.rollback()
    session.close()


@pytest.fixture()
def client(db_engine):
    """Return a FastAPI :class:`TestClient` wired to a test database.

    The ``get_db`` dependency is overridden so every request uses a
    session bound to the in-memory test engine.

    Yields:
        A :class:`httpx.Client`-like test client.
    """
    Session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

    def _override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
