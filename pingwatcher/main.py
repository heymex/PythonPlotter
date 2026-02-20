"""FastAPI application entry point.

Assembles the REST routers, mounts static files, provides the
WebSocket live-feed endpoint, and manages the APScheduler lifecycle.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pingwatcher import __version__
from pingwatcher.api.data import router as data_router
from pingwatcher.api.sessions import router as sessions_router
from pingwatcher.api.targets import router as targets_router
from pingwatcher.config import get_settings
from pingwatcher.db.models import SessionLocal, init_db
from pingwatcher.db.queries import list_targets
from pingwatcher.engine.scheduler import (
    latest_results,
    shutdown_scheduler,
    start_monitoring,
    start_scheduler,
    ws_subscribers,
)

logger = logging.getLogger(__name__)

_FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle handler.

    On startup the database tables are created, the scheduler is
    started, and any previously-active targets are re-registered.
    On shutdown the scheduler is stopped gracefully.
    """
    cfg = get_settings()
    logging.basicConfig(level=cfg.log_level, format="%(levelname)s %(name)s: %(message)s")
    logger.info("PingWatcher v%s starting up", __version__)

    # Ensure tables exist.
    init_db()

    # Start the background scheduler.
    start_scheduler()

    # Resume monitoring for targets that were active before shutdown.
    db = SessionLocal()
    try:
        for target in list_targets(db):
            if target.active:
                start_monitoring(
                    target_id=target.id,
                    host=target.host,
                    interval=target.trace_interval,
                    max_hops=target.max_hops,
                    timeout=target.timeout,
                )
    finally:
        db.close()

    yield

    shutdown_scheduler()
    logger.info("PingWatcher shut down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="PingWatcher",
    version=__version__,
    description="A PingPlotter-like network monitoring web application.",
    lifespan=lifespan,
)

# Mount API routers.
app.include_router(targets_router)
app.include_router(data_router)
app.include_router(sessions_router)

# Serve static assets (JS, CSS).
if (_FRONTEND_DIR / "static").is_dir():
    app.mount(
        "/static",
        StaticFiles(directory=str(_FRONTEND_DIR / "static")),
        name="static",
    )


# ---------------------------------------------------------------------------
# WebSocket live feed
# ---------------------------------------------------------------------------


@app.websocket("/ws/targets/{target_id}")
async def ws_live_feed(websocket: WebSocket, target_id: str):
    """Stream live traceroute results to a connected browser tab.

    Each time the scheduler finishes a sample for *target_id*, the
    payload is pushed to every subscriber via an in-process
    :class:`asyncio.Queue`.

    Args:
        websocket: The incoming WebSocket connection.
        target_id: UUID-style target identifier.
    """
    await websocket.accept()

    queue: asyncio.Queue = asyncio.Queue()
    ws_subscribers.setdefault(target_id, set()).add(queue)

    try:
        # Send the most recent cached result immediately if available.
        cached = latest_results.get(target_id)
        if cached:
            await websocket.send_json({"target_id": target_id, "hops": cached})

        while True:
            payload = await queue.get()
            await websocket.send_text(payload)
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected for %s", target_id)
    finally:
        ws_subscribers.get(target_id, set()).discard(queue)


# ---------------------------------------------------------------------------
# Frontend catch-all
# ---------------------------------------------------------------------------


@app.get("/")
async def serve_index():
    """Serve the single-page application entry point."""
    return FileResponse(str(_FRONTEND_DIR / "index.html"))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the application via Uvicorn when invoked as ``python -m pingwatcher.main``."""
    import uvicorn

    cfg = get_settings()
    uvicorn.run(
        "pingwatcher.main:app",
        host=cfg.host,
        port=cfg.port,
        log_level=cfg.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
