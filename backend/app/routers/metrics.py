"""Metrics ingestion and query endpoints."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db_models import MetricDataPoint
from app.models.schemas import (
    MetricsBulkIngestRequest,
    MetricDataPointOut,
    MetricsSummary,
    ServiceListOut,
)

router = APIRouter()


@router.post("/ingest", status_code=201)
async def ingest_metrics(
    payload: MetricsBulkIngestRequest,
    db: AsyncSession = Depends(get_db),
):
    """Bulk ingest time-series metric data points."""
    objects = []
    for dp in payload.data_points:
        objects.append(
            MetricDataPoint(
                service_name=dp.service_name,
                metric_name=dp.metric_name,
                value=dp.value,
                timestamp=dp.timestamp,
                labels=dp.labels,
            )
        )
    db.add_all(objects)
    await db.commit()
    return {"ingested": len(objects)}


@router.get("", response_model=list[MetricDataPointOut])
async def query_metrics(
    service_name: str | None = Query(None),
    metric_name: str | None = Query(None),
    from_ts: datetime | None = Query(None),
    to_ts: datetime | None = Query(None),
    limit: int = Query(1000, le=10000),
    db: AsyncSession = Depends(get_db),
):
    """Query metric data points with optional filters."""
    stmt = select(MetricDataPoint)
    conditions = []
    if service_name:
        conditions.append(MetricDataPoint.service_name == service_name)
    if metric_name:
        conditions.append(MetricDataPoint.metric_name == metric_name)
    if from_ts:
        conditions.append(MetricDataPoint.timestamp >= from_ts)
    if to_ts:
        conditions.append(MetricDataPoint.timestamp <= to_ts)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.order_by(MetricDataPoint.timestamp.asc()).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/services", response_model=ServiceListOut)
async def list_services(db: AsyncSession = Depends(get_db)):
    """List all services and their metrics."""
    stmt = (
        select(
            MetricDataPoint.service_name,
            MetricDataPoint.metric_name,
        )
        .distinct()
        .order_by(MetricDataPoint.service_name, MetricDataPoint.metric_name)
    )
    result = await db.execute(stmt)
    rows = result.all()

    services = sorted(set(r[0] for r in rows))
    metrics_map: dict[str, list[str]] = {}
    for svc, metric in rows:
        metrics_map.setdefault(svc, []).append(metric)
    return ServiceListOut(services=services, metrics=metrics_map)


@router.get("/summary", response_model=list[MetricsSummary])
async def metrics_summary(
    service_name: str | None = Query(None),
    from_ts: datetime | None = Query(None),
    to_ts: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated summary of metrics."""
    stmt = select(
        MetricDataPoint.service_name,
        MetricDataPoint.metric_name,
        func.count(MetricDataPoint.id).label("count"),
        func.min(MetricDataPoint.value).label("min_value"),
        func.max(MetricDataPoint.value).label("max_value"),
        func.avg(MetricDataPoint.value).label("avg_value"),
        func.max(MetricDataPoint.timestamp).label("latest_timestamp"),
    ).group_by(MetricDataPoint.service_name, MetricDataPoint.metric_name)

    conditions = []
    if service_name:
        conditions.append(MetricDataPoint.service_name == service_name)
    if from_ts:
        conditions.append(MetricDataPoint.timestamp >= from_ts)
    if to_ts:
        conditions.append(MetricDataPoint.timestamp <= to_ts)
    if conditions:
        stmt = stmt.where(and_(*conditions))

    result = await db.execute(stmt)
    rows = result.all()
    return [
        MetricsSummary(
            service_name=r.service_name,
            metric_name=r.metric_name,
            count=r.count,
            min_value=r.min_value,
            max_value=r.max_value,
            avg_value=r.avg_value,
            latest_timestamp=r.latest_timestamp,
        )
        for r in rows
    ]
