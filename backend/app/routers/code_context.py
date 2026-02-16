"""Code context endpoints - services, deployments, config changes."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db_models import ServiceRegistry, DeploymentLog, ConfigChangeLog
from app.models.schemas import ServiceRegistryOut, DeploymentLogOut, ConfigChangeLogOut

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
