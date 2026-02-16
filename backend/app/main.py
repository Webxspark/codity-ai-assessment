"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db
from app.routers import metrics, anomalies, code_context, chat


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    await init_db()
    yield


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

# ── Routes ───────────────────────────────────────────────────
app.include_router(metrics.router, prefix="/api/metrics", tags=["Metrics"])
app.include_router(anomalies.router, prefix="/api/anomalies", tags=["Anomalies"])
app.include_router(code_context.router, prefix="/api/code-context", tags=["Code Context"])
app.include_router(chat.router, prefix="/api/chat", tags=["AI Chat"])


@app.get("/api/health", response_model=dict)
async def health_check():
    return {
        "status": "ok",
        "version": "1.0.0",
        "environment": settings.APP_ENV,
    }
