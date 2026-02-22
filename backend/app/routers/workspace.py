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

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, AsyncSessionLocal
from app.models.db_models import (
    WorkspaceConfig,
    DeploymentLog,
)
from app.models.schemas import (
    WorkspaceConfigIn,
    WorkspaceConfigOut,
    GitHubSyncResult,
    ConnectionTestResult,
)
from app.services.github_service import GitHubService
from app.services.prometheus_poller import (
    PrometheusPoller,
    start_polling,
    stop_polling,
    _auto_register_services,
)

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
    simulate_recent: bool = Query(
        False,
        description="Re-timestamp commits to appear as recent deployments. "
        "Useful for demos where real commits are days old but you "
        "want them to correlate with live simulation anomalies.",
    ),
    db: AsyncSession = Depends(get_db),
):
    """Fetch recent commits from GitHub and store as DeploymentLog entries.

    When `simulate_recent=true`, commits are evenly spaced across the
    last 2 hours so they fall within the anomaly correlation window.
    """
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

        # Re-timestamp commits to appear recent for demo/simulation
        if simulate_recent and new:
            from app.models.db_models import DeploymentLog

            now = datetime.utcnow()
            spread_hours = 2.0  # spread commits across last 2h
            shas = [c["sha"] for c in new]
            result = await db.execute(
                select(DeploymentLog).where(DeploymentLog.commit_sha.in_(shas))
            )
            deploys = result.scalars().all()
            for i, deploy in enumerate(deploys):
                # Space evenly: most recent commit = 5min ago,
                # oldest = spread_hours ago
                offset_minutes = 5 + (i * spread_hours * 60 / max(len(deploys), 1))
                deploy.timestamp = now - timedelta(minutes=offset_minutes)

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
        # Auto-register services for anomaly ↔ commit correlation
        if service_names and config.github_repo:
            repo_service = config.github_repo.split("/")[-1]
            await _auto_register_services(
                db, service_names, repo_service, config.github_repo,
            )
        await db.commit()
        return {"ingested": count, "services": sorted(service_names)}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=502, detail=f"Poll failed: {e}")
    finally:
        await poller.close()


@router.post("/prometheus/backfill")
async def backfill_prometheus_data(
    hours_back: float = Query(2.0, ge=0.5, le=24, description="Hours of history to fetch"),
    step_seconds: int = Query(30, ge=10, le=300, description="Resolution in seconds"),
    db: AsyncSession = Depends(get_db),
):
    """Backfill historical data from Prometheus using range queries.

    Fetches `hours_back` hours of data at `step_seconds` resolution so
    anomaly detection has a rich dataset from the start instead of
    starting from zero and waiting for data to accumulate.
    """
    config = await _get_config(db)
    if not config or not config.prometheus_endpoint:
        raise HTTPException(status_code=400, detail="Prometheus endpoint not configured")
    if not config.prometheus_queries:
        raise HTTPException(status_code=400, detail="No queries configured")

    poller = PrometheusPoller(
        endpoint=config.prometheus_endpoint,
        queries=config.prometheus_queries,
    )
    try:
        count, service_names = await poller.backfill_range(
            db, hours_back=hours_back, step_seconds=step_seconds,
        )
        # Auto-register services for anomaly ↔ commit correlation
        if service_names and config.github_repo:
            repo_service = config.github_repo.split("/")[-1]
            await _auto_register_services(
                db, service_names, repo_service, config.github_repo,
            )
        await db.commit()
        return {
            "backfilled": count,
            "hours_back": hours_back,
            "step_seconds": step_seconds,
            "services": sorted(service_names),
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=502, detail=f"Backfill failed: {e}")
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

    Preserves the WorkspaceConfig itself.  Uses TRUNCATE for speed on
    large datasets (DELETE on 150K+ rows can take minutes).
    """
    stop_polling()

    from sqlalchemy import text

    tables = [
        "chat_messages",
        "chat_conversations",
        "anomaly_correlations",
        "anomalies",
        "metric_data_points",
        "deployment_logs",
        "config_change_logs",
        "service_registry",
    ]
    await db.execute(text(f"TRUNCATE TABLE {', '.join(tables)} CASCADE"))
    await db.commit()
    return {"status": "cleared", "tables_truncated": tables}


@router.delete("/data/metrics")
async def drop_metrics_data(db: AsyncSession = Depends(get_db)):
    """Drop only metric data points and anomalies."""
    from sqlalchemy import text

    await db.execute(text(
        "TRUNCATE TABLE anomaly_correlations, anomalies, metric_data_points CASCADE"
    ))
    await db.commit()
    return {"status": "cleared"}
