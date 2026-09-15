"""Application factory and HTTP wiring."""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend import storage
from backend.account import router as account_router
from backend.admin import router as admin_router
from backend.auth import router as auth_router
from backend.config import settings
from backend.database import engine
from backend.errors import register_error_handlers
from backend.inbox import router as inbox_router
from backend.logging_config import configure_logging, get_logger, request_id_var
from backend.media import router as media_router
from backend.moderation import router as moderation_router
from backend.pairs import router as pairs_router
from backend.profile import router as profile_router
from backend.safety import router as safety_router
from backend.signaling import router as signaling_router

configure_logging(level=settings.log_level, json_output=settings.emit_json_logs)
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is Alembic's job. Startup only proves the database is reachable,
    # so a bad DATABASE_URL surfaces here instead of on a user's first request.
    if engine is not None:
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            log.info("database_connected")
        except Exception:
            log.exception("database_unreachable_at_startup")
            if settings.is_production:
                raise
    else:
        log.warning("database_not_configured")

    # Same discipline as the model layer: a misconfiguration that only shows
    # up when the first person tries to upload is one found too late.
    storage.check_configuration()

    worker_stopping = asyncio.Event()
    worker_task = None
    if settings.run_worker_in_api and engine is not None:
        from backend.runner import loop as job_loop

        worker_task = asyncio.create_task(job_loop(worker_stopping), name="job-loop")

    log.info(
        "app_started",
        environment=settings.environment,
        storage_provider=settings.storage_provider,
    )
    yield
    if worker_task is not None:
        # Let the job in hand finish rather than abandoning it half-written.
        worker_stopping.set()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(worker_task, timeout=20)
    if engine is not None:
        await engine.dispose()
    log.info("app_stopped")


class SPAStaticFiles(StaticFiles):
    """Serve the built SPA, falling back to index.html for client-side routes."""

    async def get_response(self, path, scope):
        def is_client_route() -> bool:
            # Never for the API, the socket or uploaded media. The fallback used
            # to answer a made-up `/api/...` path with the SPA's HTML and a 200,
            # which made a missing endpoint look like a working one returning
            # nonsense — the stale-server confusion in CLAUDE.md — and meant a
            # route that answered 404 on purpose stood out from ones that
            # simply do not exist.
            # `parts`, not a split on "/": Starlette hands this path through
            # `os.path.normpath`, which on Windows turns the separators into
            # backslashes — a "/" split matched nothing locally while it would
            # have worked on a Linux server.
            parts = Path(path).parts
            if parts and parts[0] in ("api", "ws", "media_uploads"):
                return False
            return "." not in Path(path).name

        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and is_client_route():
                return await super().get_response("index.html", scope)
            raise
        if response.status_code == 404 and is_client_route():
            return await super().get_response("index.html", scope)
        return response


def create_app() -> FastAPI:
    app = FastAPI(
        title="Overtone API",
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        # Not only the docs page: the schema behind it lists every route, and
        # `/api/admin` answering 404 to strangers means nothing if
        # `/openapi.json` names it for them.
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Microphone stays for the voice prompt. Camera is gone with the calls.
        response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' data: blob: https:; "
            "media-src 'self' blob: https:; "
            "connect-src 'self' https: wss:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "script-src 'self'"
        )
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    register_error_handlers(app)

    app.include_router(auth_router)
    app.include_router(account_router)
    app.include_router(profile_router)
    app.include_router(media_router)
    app.include_router(pairs_router)
    app.include_router(inbox_router)
    app.include_router(safety_router)
    app.include_router(moderation_router)
    app.include_router(admin_router)
    app.include_router(signaling_router)

    @app.get("/api/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        """Liveness: is the process up? Deliberately touches no dependency."""
        return {"status": "ok"}

    @app.get("/api/ready", include_in_schema=False)
    async def ready():
        """Readiness: can this instance actually serve traffic?"""
        if engine is None:
            return JSONResponse(status_code=503, content={"status": "no_database"})
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            log.exception("readiness_check_failed")
            return JSONResponse(status_code=503, content={"status": "database_unavailable"})
        return {"status": "ready"}

    os.makedirs(settings.media_root, exist_ok=True)
    app.mount("/media_uploads", StaticFiles(directory=settings.media_root), name="media")

    if Path("dist").is_dir():
        app.mount("/", SPAStaticFiles(directory="dist", html=True), name="frontend")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.app:app",
        host="0.0.0.0",  # noqa: S104 — containers must bind all interfaces
        port=int(os.environ.get("PORT", "8000")),
        reload=not settings.is_production,
        proxy_headers=True,
        log_config=None,
    )
