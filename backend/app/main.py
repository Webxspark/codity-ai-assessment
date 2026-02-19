"""FastAPI application entrypoint."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import engine, init_db
from app.routers import metrics, anomalies, code_context, chat, seed

# ── Static file paths ────────────────────────────────────────────────
# In Docker the Vite build is copied to /app/static.
# During local dev the folder won't exist which is fine — we skip mounting.
STATIC_DIR = Path(os.getenv("STATIC_DIR", "/app/static"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    await init_db()
    yield
    # Cleanly return all pooled connections to the server on shutdown.
    await engine.dispose()


app = FastAPI(
    title="CodityAI - Metrics Anomaly Detection & Code Insight",
    description="AI-assisted anomaly detection with code context correlation",
    version="1.0.0",
    lifespan=lifespan,
)

settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API Routes ───────────────────────────────────────────────────
app.include_router(metrics.router, prefix="/api/metrics", tags=["Metrics"])
app.include_router(anomalies.router, prefix="/api/anomalies", tags=["Anomalies"])
app.include_router(code_context.router, prefix="/api/code-context", tags=["Code Context"])
app.include_router(chat.router, prefix="/api/chat", tags=["AI Chat"])
app.include_router(seed.router, prefix="/api/seed", tags=["Mock Data Seeding"])


@app.get("/api/health", response_model=dict)
async def health_check():
    return {
        "status": "ok",
        "version": "1.0.0",
        "environment": settings.APP_ENV,
    }


# ── Serve Frontend Static Assets ────────────────────────────────────
# Mount the Vite build output so that the backend acts as the single
# HTTP server in production (no nginx required).

if STATIC_DIR.is_dir():
    # Serve hashed assets (JS, CSS, images) under /assets
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="assets",
        )

    # Serve any other static file at root (favicon.ico, robots.txt etc.)
    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        """Serve static files; fall back to index.html for SPA routing."""
        file_path = STATIC_DIR / full_path
        if full_path and file_path.is_file():
            return FileResponse(str(file_path))
        # SPA fallback
        index = STATIC_DIR / "index.html"
        if index.is_file():
            return FileResponse(str(index))
        return HTMLResponse("Not Found", status_code=404)
