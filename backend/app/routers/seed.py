"""Mock data seeding endpoints — callable from frontend UI."""

from fastapi import APIRouter, Depends
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db_models import (
    MetricDataPoint,
    Anomaly,
    ServiceRegistry,
    DeploymentLog,
    ConfigChangeLog,
    AnomalyCorrelation,
    ChatConversation,
    ChatMessage,
)

router = APIRouter()


@router.get("/status")
async def seed_status(db: AsyncSession = Depends(get_db)):
    """Check whether mock data exists and return summary counts."""
    counts = {}
    for model, label in [
        (MetricDataPoint, "metric_data_points"),
        (Anomaly, "anomalies"),
        (ServiceRegistry, "services"),
        (DeploymentLog, "deployments"),
        (ConfigChangeLog, "config_changes"),
        (AnomalyCorrelation, "correlations"),
        (ChatConversation, "conversations"),
        (ChatMessage, "chat_messages"),
    ]:
        result = await db.execute(select(func.count(model.id)))
        counts[label] = result.scalar() or 0

    has_data = counts["metric_data_points"] > 0
    return {"has_data": has_data, "counts": counts}


@router.post("/generate")
async def generate_and_seed(db: AsyncSession = Depends(get_db)):
    """Drop all existing data and seed fresh mock data.

    This imports and runs the mock data generator script.
    """
    # Drop all rows in reverse dependency order
    for model in [
        ChatMessage,
        ChatConversation,
        AnomalyCorrelation,
        Anomaly,
        MetricDataPoint,
        ConfigChangeLog,
        DeploymentLog,
        ServiceRegistry,
    ]:
        await db.execute(text(f"DELETE FROM {model.__tablename__}"))
    await db.commit()

    # Import and run the seed function (it creates its own session)
    from scripts.generate_mock_data import seed_database
    await seed_database()

    # Return the new counts
    return await seed_status(db=db)
