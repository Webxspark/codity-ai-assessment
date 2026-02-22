"""Workspace configuration router.

CRUD for the single-row WorkspaceConfig, plus:
- Test GitHub connection
- Test Prometheus connection
- Sync GitHub commits → DeploymentLog
- Start/stop Prometheus polling
- Drop all data (reset workspace for a different repo/endpoint)
"""

from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, AsyncSessionLocal
from app.models.db_models import (
    WorkspaceConfig,
    MetricDataPoint,
    Anomaly,
    AnomalyCorrelation,
    DeploymentLog,
    ConfigChangeLog,
    ServiceRegistry,
    ChatConversation,
    ChatMessage,
)
from app.models.schemas import (
    WorkspaceConfigIn,
    WorkspaceConfigOut,
    GitHubSyncResult,
    ConnectionTestResult,
)
from app.services.github_service import GitHubService
from app.services.prometheus_poller import PrometheusPoller, start_polling, stop_polling

router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────

async def _get_config(db: AsyncSession) -> WorkspaceConfig | None:
    result = await db.execute(select(WorkspaceConfig).limit(1))
    return result.scalar_one_or_none()


# ── CRUD ─────────────────────────────────────────────────────────────

@router.get("/config", response_model=WorkspaceConfigOut | None)
async def get_workspace_config(db: AsyncSession = Depends(get_db)):
    """Get the current workspace configuration (or null if not set)."""
    config = await _get_config(db)
    return config


@router.put("/config", response_model=WorkspaceConfigOut)
async def upsert_workspace_config(
    body: WorkspaceConfigIn,
    db: AsyncSession = Depends(get_db),
):
    """Create or update workspace configuration (single-row upsert)."""
    config = await _get_config(db)

    data = body.model_dump(exclude_unset=True)
    # Serialize prometheus_queries list[PrometheusQueryConfig] → list[dict]
    if "prometheus_queries" in data and data["prometheus_queries"]:
        data["prometheus_queries"] = [
            q.model_dump() if hasattr(q, "model_dump") else q
            for q in data["prometheus_queries"]
        ]

    if config:
        for key, val in data.items():
            setattr(config, key, val)
        config.updated_at = datetime.utcnow()
    else:
        config = WorkspaceConfig(**data)
        db.add(config)

    await db.commit()
    await db.refresh(config)
    return config


@router.delete("/config")
async def delete_workspace_config(db: AsyncSession = Depends(get_db)):
    """Delete the workspace configuration (stops any polling)."""
    stop_polling()
    config = await _get_config(db)
    if config:
        await db.delete(config)
        await db.commit()
    return {"status": "deleted"}


# ── GitHub ───────────────────────────────────────────────────────────

@router.get("/github/rate-limit")
async def github_rate_limit(db: AsyncSession = Depends(get_db)):
    """Check current GitHub API rate limit status."""
    config = await _get_config(db)
    if not config or not config.github_repo:
        raise HTTPException(status_code=400, detail="GitHub repo not configured")

    svc = GitHubService(repo=config.github_repo, token=config.github_token)
    try:
        return await svc.get_rate_limit_status()
    finally:
        await svc.close()


@router.post("/github/test", response_model=ConnectionTestResult)
async def test_github_connection(db: AsyncSession = Depends(get_db)):
    """Test that GitHub credentials and repo are valid."""
    config = await _get_config(db)
    if not config or not config.github_repo:
        raise HTTPException(status_code=400, detail="GitHub repo not configured")

    svc = GitHubService(repo=config.github_repo, token=config.github_token)
    try:
        info = await svc.get_repo_info()
        return ConnectionTestResult(status="connected", details=info)
    except Exception as e:
        return ConnectionTestResult(status="error", details={"error": str(e)})
    finally:
        await svc.close()


@router.post("/github/sync", response_model=GitHubSyncResult)
async def sync_github_commits(
    hours_back: int = 48,
    limit: int = 30,
    db: AsyncSession = Depends(get_db),
):
    """Fetch recent commits from GitHub and store as DeploymentLog entries."""
    config = await _get_config(db)
    if not config or not config.github_repo:
        raise HTTPException(status_code=400, detail="GitHub repo not configured")

    svc = GitHubService(repo=config.github_repo, token=config.github_token)
    try:
        since = datetime.utcnow() - timedelta(hours=hours_back)
        branch = config.github_default_branch or "main"
        service_name = config.github_repo.split("/")[-1]  # use repo name as service

        new = await svc.sync_commits_to_deployments(
            db_session=db,
            service_name=service_name,
            branch=branch,
            since=since,
            limit=limit,
        )
        await db.commit()
        return GitHubSyncResult(synced=len(new), commits=new)
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=502, detail=f"GitHub sync failed: {e}")
    finally:
        await svc.close()


# ── Prometheus ───────────────────────────────────────────────────────

@router.post("/prometheus/test", response_model=ConnectionTestResult)
async def test_prometheus_connection(db: AsyncSession = Depends(get_db)):
    """Test Prometheus endpoint connectivity."""
    config = await _get_config(db)
    if not config or not config.prometheus_endpoint:
        raise HTTPException(status_code=400, detail="Prometheus endpoint not configured")

    poller = PrometheusPoller(endpoint=config.prometheus_endpoint)
    try:
        result = await poller.test_connection()
        return ConnectionTestResult(status=result["status"], details=result)
    finally:
        await poller.close()


@router.get("/prometheus/metrics")
async def discover_prometheus_metrics(db: AsyncSession = Depends(get_db)):
    """Discover available metrics from the Prometheus endpoint."""
    config = await _get_config(db)
    if not config or not config.prometheus_endpoint:
        raise HTTPException(status_code=400, detail="Prometheus endpoint not configured")

    poller = PrometheusPoller(endpoint=config.prometheus_endpoint)
    try:
        return await poller.discover_metrics()
    finally:
        await poller.close()


@router.post("/prometheus/poll-once")
async def poll_prometheus_once(db: AsyncSession = Depends(get_db)):
    """Execute a single poll cycle (for testing)."""
    config = await _get_config(db)
    if not config or not config.prometheus_endpoint:
        raise HTTPException(status_code=400, detail="Prometheus endpoint not configured")

    poller = PrometheusPoller(
        endpoint=config.prometheus_endpoint,
        queries=[q for q in (config.prometheus_queries or [])],
    )
    try:
        count, service_names = await poller.poll_once(db)
        await db.commit()
        return {"ingested": count, "services": sorted(service_names)}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=502, detail=f"Poll failed: {e}")
    finally:
        await poller.close()


@router.post("/prometheus/start-polling")
async def start_prometheus_polling(db: AsyncSession = Depends(get_db)):
    """Start background Prometheus polling."""
    config = await _get_config(db)
    if not config or not config.prometheus_endpoint:
        raise HTTPException(status_code=400, detail="Prometheus endpoint not configured")
    if not config.prometheus_queries:
        raise HTTPException(status_code=400, detail="No queries configured")

    config.is_polling = "true"
    await db.commit()

    # AsyncSessionLocal used as the factory for the background task
    start_polling(db_factory=AsyncSessionLocal, config_id=config.id)
    return {"status": "polling_started", "interval": config.prometheus_poll_interval_seconds}


@router.post("/prometheus/stop-polling")
async def stop_prometheus_polling(db: AsyncSession = Depends(get_db)):
    """Stop background Prometheus polling."""
    stop_polling()
    config = await _get_config(db)
    if config:
        config.is_polling = "false"
        await db.commit()
    return {"status": "polling_stopped"}


# ── Data Management ──────────────────────────────────────────────────

@router.delete("/data/all")
async def drop_all_data(db: AsyncSession = Depends(get_db)):
    """Drop all ingested data — allows re-configuring for a different repo/endpoint.

    Preserves the WorkspaceConfig itself.
    """
    stop_polling()

    # Order matters for FK constraints
    tables = [
        ChatMessage,
        ChatConversation,
        AnomalyCorrelation,
        Anomaly,
        MetricDataPoint,
        DeploymentLog,
        ConfigChangeLog,
        ServiceRegistry,
    ]
    counts = {}
    for model in tables:
        result = await db.execute(delete(model))
        counts[model.__tablename__] = result.rowcount

    await db.commit()
    return {"status": "cleared", "deleted_rows": counts}


@router.delete("/data/metrics")
async def drop_metrics_data(db: AsyncSession = Depends(get_db)):
    """Drop only metric data points and anomalies."""
    r1 = await db.execute(delete(AnomalyCorrelation))
    r2 = await db.execute(delete(Anomaly))
    r3 = await db.execute(delete(MetricDataPoint))
    await db.commit()
    return {
        "status": "cleared",
        "deleted_rows": {
            "anomaly_correlations": r1.rowcount,
            "anomalies": r2.rowcount,
            "metric_data_points": r3.rowcount,
        },
    }
