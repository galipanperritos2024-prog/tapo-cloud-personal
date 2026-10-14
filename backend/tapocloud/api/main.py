"""Backend application: REST API + upload queue + retention + static frontend.

Run with ``python -m tapocloud.api``.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from tapocloud import __version__
from tapocloud.api.routers import auth, cameras, events, google, internal, recordings, system
from tapocloud.config import get_settings
from tapocloud.core.db import init_db
from tapocloud.core.ratelimit import RateLimiter
from tapocloud.drive.uploader import uploader
from tapocloud.storage.retention import retention_loop

log = logging.getLogger("backend")

# Generous global limit: protects against runaway scripts, not normal UI usage.
api_limiter = RateLimiter(600, 60)

CSP = (
    "default-src 'self'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob: http: https:; "
    "connect-src 'self' http: https:; "  # WHEP/HLS live on the MediaMTX ports
    "script-src 'self' 'unsafe-inline'; "  # Next.js static export inlines bootstrap scripts
    "style-src 'self' 'unsafe-inline'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            ip = request.client.host if request.client else "unknown"
            if not api_limiter.allow(ip):
                return JSONResponse({"detail": "Demasiadas peticiones"}, status_code=429, headers={"Retry-After": "30"})
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if not path.startswith("/api/"):
            response.headers.setdefault("Content-Security-Policy", CSP)
        elif "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    uploader.start()
    retention = asyncio.create_task(retention_loop(), name="retention")
    log.info("Tapo Cloud Personal backend %s started", __version__)
    yield
    retention.cancel()
    await uploader.stop()


app = FastAPI(
    title="Tapo Cloud Personal API",
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)
app.add_middleware(SecurityMiddleware)

for r in (auth.router, cameras.router, events.router, recordings.router, google.router, system.router, internal.router):
    app.include_router(r)


@app.get("/api/health", include_in_schema=False)
def health() -> dict:
    return {"ok": True, "version": __version__}


# Experimental Tapo interoperability lab: mounted only when explicitly enabled.
if get_settings().tapo_lab_enabled:
    try:
        from tapo_lab.api import router as lab_router  # type: ignore[import-not-found]

        app.include_router(lab_router)
        log.warning("EXPERIMENTAL tapo-lab endpoints enabled under /api/lab")
    except ImportError:
        log.warning("TAPOCLOUD_TAPO_LAB_ENABLED=true but the tapo-lab package is not installed")


def _frontend_dir() -> Path | None:
    s = get_settings()
    candidates = [s.frontend_dir] if s.frontend_dir else []
    candidates.append(Path(__file__).resolve().parents[3] / "frontend" / "out")
    return next((c for c in candidates if c and (c / "index.html").exists()), None)


_fe = _frontend_dir()
if _fe:

    class SPAStatic(StaticFiles):
        """Serve the exported Next.js app; unknown routes fall back to 404.html."""

        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            if response.status_code == 404 and (_fe / "404.html").exists():
                return FileResponse(_fe / "404.html", status_code=404)
            return response

    app.mount("/", SPAStatic(directory=_fe, html=True), name="frontend")
