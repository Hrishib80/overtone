import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.config import ALLOWED_ORIGINS, IS_PRODUCTION, MEDIA_ROOT
from backend.database import engine, Base
from backend.auth import router as auth_router
from backend.matching import router as matching_router
from backend.chat import router as chat_router
from backend.signaling import router as signaling_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()

class SPAStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            is_client_route = exc.status_code == 404 and "." not in Path(path).name
            if is_client_route:
                return await super().get_response("index.html", scope)
            raise
        if response.status_code == 404 and "." not in Path(path).name:
            return await super().get_response("index.html", scope)
        return response

app = FastAPI(
    title="Overtone API",
    lifespan=lifespan,
    docs_url=None if IS_PRODUCTION else "/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=(self), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data: blob: https:; "
        "media-src 'self' blob: https:; "
        "connect-src 'self' https: wss:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "script-src 'self'"
    )
    if IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

app.include_router(auth_router)
app.include_router(matching_router)
app.include_router(chat_router)
app.include_router(signaling_router)

os.makedirs(MEDIA_ROOT, exist_ok=True)
app.mount("/media_uploads", StaticFiles(directory=MEDIA_ROOT), name="media")

@app.get("/api/health", include_in_schema=False)
async def health_check():
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return {"status": "ok"}

if os.path.exists("dist"):
    app.mount("/", SPAStaticFiles(directory="dist", html=True), name="frontend")

if __name__ == "__main__":
    uvicorn.run(
        "backend.app:app",
        host="0.0.0.0",
        port=8000,
        reload=not IS_PRODUCTION,
        proxy_headers=True,
    )
