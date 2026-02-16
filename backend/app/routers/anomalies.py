"""Anomaly detection and query endpoints."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.db_models import Anomaly
from app.models.schemas import AnomalyOut, DetectAnomaliesRequest, DetectAnomaliesResponse
from app.services.anomaly_detector import AnomalyDetectorService
from app.services.code_context_service import CodeContextService

router = APIRouter()


@router.get("", response_model=list[AnomalyOut])
async def list_anomalies(
    service_name: str | None = Query(None),
    severity: str | None = Query(None),
    from_ts: datetime | None = Query(None),
    to_ts: datetime | None = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List detected anomalies with optional filters."""
    stmt = select(Anomaly).options(selectinload(Anomaly.correlations))
    conditions = []
    if service_name:
        conditions.append(Anomaly.service_name == service_name)
    if severity:
        conditions.append(Anomaly.severity == severity)
    if from_ts:
        conditions.append(Anomaly.detected_at >= from_ts)
    if to_ts:
        conditions.append(Anomaly.detected_at <= to_ts)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.order_by(Anomaly.detected_at.desc()).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{anomaly_id}", response_model=AnomalyOut)
async def get_anomaly(anomaly_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get full anomaly detail including correlations."""
    stmt = (
        select(Anomaly)
        .options(selectinload(Anomaly.correlations))
        .where(Anomaly.id == anomaly_id)
    )
    result = await db.execute(stmt)
    anomaly = result.scalar_one_or_none()
    if not anomaly:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Anomaly not found")
    return anomaly


@router.post("/detect", response_model=DetectAnomaliesResponse)
async def detect_anomalies(
    request: DetectAnomaliesRequest,
    db: AsyncSession = Depends(get_db),
):
    """Trigger anomaly detection on metrics data."""
    detector = AnomalyDetectorService(db)
    anomalies = await detector.detect(
        service_name=request.service_name,
        metric_name=request.metric_name,
        from_ts=request.from_ts,
        to_ts=request.to_ts,
    )

    # Run correlation for each detected anomaly
    ctx_service = CodeContextService(db)
    for anomaly in anomalies:
        await ctx_service.correlate_anomaly(anomaly)

    await db.commit()

    # Re-fetch with correlations loaded
    if anomalies:
        ids = [a.id for a in anomalies]
        stmt = (
            select(Anomaly)
            .options(selectinload(Anomaly.correlations))
            .where(Anomaly.id.in_(ids))
            .order_by(Anomaly.detected_at.desc())
        )
        result = await db.execute(stmt)
        anomalies = list(result.scalars().all())

    return DetectAnomaliesResponse(
        anomalies_detected=len(anomalies),
        anomalies=anomalies,
    )
