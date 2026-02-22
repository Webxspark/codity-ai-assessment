"""Code context endpoints - services, deployments, config changes."""

import json
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db_models import ServiceRegistry, DeploymentLog, ConfigChangeLog, MetricDataPoint
from app.models.schemas import (
    ServiceRegistryOut,
    DeploymentLogOut,
    ConfigChangeLogOut,
    DeploymentComparisonOut,
    DeploymentComparisonMetric,
    MetricWindow,
)

router = APIRouter()


@router.get("/services", response_model=list[ServiceRegistryOut])
async def list_registered_services(db: AsyncSession = Depends(get_db)):
    """List all registered services with metadata."""
    result = await db.execute(select(ServiceRegistry).order_by(ServiceRegistry.service_name))
    return result.scalars().all()


@router.get("/services/{service_name}", response_model=ServiceRegistryOut)
async def get_service(service_name: str, db: AsyncSession = Depends(get_db)):
    """Get service registry details."""
    result = await db.execute(
        select(ServiceRegistry).where(ServiceRegistry.service_name == service_name)
    )
    svc = result.scalar_one_or_none()
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    return svc


@router.get("/deployments", response_model=list[DeploymentLogOut])
async def list_deployments(
    service_name: str | None = Query(None),
    from_ts: datetime | None = Query(None),
    to_ts: datetime | None = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List deployments with optional filters."""
    stmt = select(DeploymentLog)
    conditions = []
    if service_name:
        conditions.append(DeploymentLog.service_name == service_name)
    if from_ts:
        conditions.append(DeploymentLog.timestamp >= from_ts)
    if to_ts:
        conditions.append(DeploymentLog.timestamp <= to_ts)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.order_by(DeploymentLog.timestamp.desc()).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/config-changes", response_model=list[ConfigChangeLogOut])
async def list_config_changes(
    service_name: str | None = Query(None),
    from_ts: datetime | None = Query(None),
    to_ts: datetime | None = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List config changes with optional filters."""
    stmt = select(ConfigChangeLog)
    conditions = []
    if service_name:
        conditions.append(ConfigChangeLog.service_name == service_name)
    if from_ts:
        conditions.append(ConfigChangeLog.timestamp >= from_ts)
    if to_ts:
        conditions.append(ConfigChangeLog.timestamp <= to_ts)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.order_by(ConfigChangeLog.timestamp.desc()).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/deployments/{deployment_id}/comparison", response_model=DeploymentComparisonOut)
async def deployment_comparison(
    deployment_id: UUID,
    window_minutes: int = Query(60, ge=5, le=720, description="Minutes to compare before/after"),
    db: AsyncSession = Depends(get_db),
):
    """Compare metric behaviour before vs after a deployment.

    Returns all metrics for the deployment's service in the window
    before and after the deployment timestamp.  Data points are capped
    at MAX_COMPARISON_POINTS per window per metric to handle large datasets.
    """
    MAX_COMPARISON_POINTS = 500  # per window per metric
    # Look up the deployment
    result = await db.execute(
        select(DeploymentLog).where(DeploymentLog.id == deployment_id)
    )
    deploy = result.scalar_one_or_none()
    if not deploy:
        raise HTTPException(status_code=404, detail="Deployment not found")

    deploy_ts = deploy.timestamp
    window = timedelta(minutes=window_minutes)
    before_start = deploy_ts - window
    after_end = deploy_ts + window

    # Resolve which monitored services correspond to this deployment.
    # Deployment service_name is typically the repo name (e.g. "codity-ai-assessment")
    # while metric data uses Prometheus service names (e.g. "api-gateway").
    # The ServiceRegistry maps metric services → repo dependencies via JSON array.
    from sqlalchemy import text as sa_text

    svc_result = await db.execute(
        sa_text(
            "SELECT service_name FROM service_registry "
            "WHERE CAST(dependencies AS jsonb) @> CAST(:dep_json AS jsonb)"
        ),
        {"dep_json": json.dumps([deploy.service_name])},
    )
    monitored_services = [row[0] for row in svc_result.all()]
    # Fallback: if no registry mapping, try the deploy service_name directly
    if not monitored_services:
        monitored_services = [deploy.service_name]

    # Get all distinct metric names across the monitored services
    metric_combos_result = await db.execute(
        select(
            MetricDataPoint.service_name,
            MetricDataPoint.metric_name,
        )
        .where(MetricDataPoint.service_name.in_(monitored_services))
        .distinct()
    )
    metric_combos = metric_combos_result.all()  # list of (service, metric)

    metrics: list[DeploymentComparisonMetric] = []

    for svc_name, metric_name in metric_combos:
        # Before window (capped)
        before_result = await db.execute(
            select(MetricDataPoint)
            .where(
                and_(
                    MetricDataPoint.service_name == svc_name,
                    MetricDataPoint.metric_name == metric_name,
                    MetricDataPoint.timestamp >= before_start,
                    MetricDataPoint.timestamp < deploy_ts,
                )
            )
            .order_by(MetricDataPoint.timestamp.asc())
            .limit(MAX_COMPARISON_POINTS)
        )
        before_points = before_result.scalars().all()

        # After window (capped)
        after_result = await db.execute(
            select(MetricDataPoint)
            .where(
                and_(
                    MetricDataPoint.service_name == svc_name,
                    MetricDataPoint.metric_name == metric_name,
                    MetricDataPoint.timestamp >= deploy_ts,
                    MetricDataPoint.timestamp <= after_end,
                )
            )
            .order_by(MetricDataPoint.timestamp.asc())
            .limit(MAX_COMPARISON_POINTS)
        )
        after_points = after_result.scalars().all()

        # Compute summary statistics
        before_values = [p.value for p in before_points]
        after_values = [p.value for p in after_points]

        def _stats(vals: list[float]) -> dict:
            if not vals:
                return {"mean": None, "min": None, "max": None, "std": None}
            mean = sum(vals) / len(vals)
            variance = sum((v - mean) ** 2 for v in vals) / len(vals) if len(vals) > 1 else 0
            return {
                "mean": round(mean, 4),
                "min": round(min(vals), 4),
                "max": round(max(vals), 4),
                "std": round(variance ** 0.5, 4),
            }

        before_stats = _stats(before_values)
        after_stats = _stats(after_values)

        # Calculate percentage change
        pct_change = None
        if before_stats["mean"] is not None and after_stats["mean"] is not None and before_stats["mean"] != 0:
            pct_change = round(
                ((after_stats["mean"] - before_stats["mean"]) / abs(before_stats["mean"])) * 100, 2
            )

        metrics.append(
            DeploymentComparisonMetric(
                metric_name=f"{svc_name}/{metric_name}",
                before=MetricWindow(
                    start=before_start.isoformat(),
                    end=deploy_ts.isoformat(),
                    data_points=[
                        {"timestamp": p.timestamp.isoformat(), "value": p.value}
                        for p in before_points
                    ],
                    stats=before_stats,
                ),
                after=MetricWindow(
                    start=deploy_ts.isoformat(),
                    end=after_end.isoformat(),
                    data_points=[
                        {"timestamp": p.timestamp.isoformat(), "value": p.value}
                        for p in after_points
                    ],
                    stats=after_stats,
                ),
                pct_change=pct_change,
            )
        )

    return DeploymentComparisonOut(
        deployment=deploy,
        window_minutes=window_minutes,
        metrics=metrics,
    )
