"""Code context service — correlates anomalies with deployments, config changes, and services."""

from datetime import timedelta

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import (
    Anomaly,
    AnomalyCorrelation,
    DeploymentLog,
    ConfigChangeLog,
    ServiceRegistry,
)


class CodeContextService:
    """Associates anomalies with code changes and service context."""

    # Look-back windows for correlating changes to anomalies
    DEPLOYMENT_WINDOW_MINUTES = 60
    CONFIG_CHANGE_WINDOW_MINUTES = 60

    def __init__(self, db: AsyncSession):
        self.db = db

    async def correlate_anomaly(self, anomaly: Anomaly) -> list[AnomalyCorrelation]:
        """Find and store correlations between an anomaly and probable causes."""
        correlations = []

        # 1. Find deployments in the look-back window
        deploy_corrs = await self._correlate_deployments(anomaly)
        correlations.extend(deploy_corrs)

        # 2. Find config changes in the look-back window
        config_corrs = await self._correlate_config_changes(anomaly)
        correlations.extend(config_corrs)

        # 3. Find related anomalies in other metrics for the same service
        related_corrs = await self._correlate_related_anomalies(anomaly)
        correlations.extend(related_corrs)

        return correlations

    async def _correlate_deployments(self, anomaly: Anomaly) -> list[AnomalyCorrelation]:
        """Find deployments that happened before the anomaly."""
        window_start = anomaly.detected_at - timedelta(minutes=self.DEPLOYMENT_WINDOW_MINUTES)

        # Check both the anomalous service and its dependencies
        service_names = [anomaly.service_name]
        svc_registry = await self._get_service_registry(anomaly.service_name)
        if svc_registry and svc_registry.dependencies:
            service_names.extend(svc_registry.dependencies)

        stmt = select(DeploymentLog).where(
            and_(
                DeploymentLog.service_name.in_(service_names),
                DeploymentLog.timestamp >= window_start,
                DeploymentLog.timestamp <= anomaly.detected_at,
            )
        ).order_by(DeploymentLog.timestamp.desc())

        result = await self.db.execute(stmt)
        deployments = result.scalars().all()

        correlations = []
        for deploy in deployments:
            # Calculate suspicion score based on temporal proximity + same service
            time_diff = (anomaly.detected_at - deploy.timestamp).total_seconds()
            proximity_score = max(0, 1 - time_diff / (self.DEPLOYMENT_WINDOW_MINUTES * 60))
            same_service_bonus = 0.3 if deploy.service_name == anomaly.service_name else 0.0
            suspicion = min(1.0, proximity_score * 0.7 + same_service_bonus)

            explanation = (
                f"Deployment to {deploy.service_name} at "
                f"{deploy.timestamp.strftime('%H:%M:%S')} "
                f"({int(time_diff / 60)} min before anomaly). "
                f"Commit {deploy.commit_sha[:8]} by {deploy.author}: "
                f"\"{deploy.commit_message}\". "
                f"Changed files: {', '.join(deploy.changed_files or [])}."
            )

            corr = AnomalyCorrelation(
                anomaly_id=anomaly.id,
                correlation_type="deployment",
                reference_id=deploy.id,
                suspicion_score=round(suspicion, 3),
                explanation=explanation,
            )
            self.db.add(corr)
            correlations.append(corr)

        return correlations

    async def _correlate_config_changes(self, anomaly: Anomaly) -> list[AnomalyCorrelation]:
        """Find config changes that happened before the anomaly."""
        window_start = anomaly.detected_at - timedelta(minutes=self.CONFIG_CHANGE_WINDOW_MINUTES)

        stmt = select(ConfigChangeLog).where(
            and_(
                ConfigChangeLog.service_name == anomaly.service_name,
                ConfigChangeLog.timestamp >= window_start,
                ConfigChangeLog.timestamp <= anomaly.detected_at,
            )
        ).order_by(ConfigChangeLog.timestamp.desc())

        result = await self.db.execute(stmt)
        changes = result.scalars().all()

        correlations = []
        for change in changes:
            time_diff = (anomaly.detected_at - change.timestamp).total_seconds()
            proximity_score = max(0, 1 - time_diff / (self.CONFIG_CHANGE_WINDOW_MINUTES * 60))
            suspicion = min(1.0, proximity_score * 0.8 + 0.1)  # config changes are usually suspicious

            explanation = (
                f"Config change on {change.service_name} at "
                f"{change.timestamp.strftime('%H:%M:%S')} "
                f"({int(time_diff / 60)} min before anomaly). "
                f"Parameter '{change.parameter}' changed from "
                f"'{change.old_value}' to '{change.new_value}' "
                f"by {change.changed_by}."
            )

            corr = AnomalyCorrelation(
                anomaly_id=anomaly.id,
                correlation_type="config_change",
                reference_id=change.id,
                suspicion_score=round(suspicion, 3),
                explanation=explanation,
            )
            self.db.add(corr)
            correlations.append(corr)

        return correlations

    async def _correlate_related_anomalies(self, anomaly: Anomaly) -> list[AnomalyCorrelation]:
        """Find anomalies in other metrics around the same time."""
        window = timedelta(minutes=10)

        stmt = select(Anomaly).where(
            and_(
                Anomaly.service_name == anomaly.service_name,
                Anomaly.metric_name != anomaly.metric_name,
                Anomaly.detected_at >= anomaly.detected_at - window,
                Anomaly.detected_at <= anomaly.detected_at + window,
                Anomaly.id != anomaly.id,
            )
        )

        result = await self.db.execute(stmt)
        related = result.scalars().all()

        correlations = []
        for rel in related:
            time_diff = abs((anomaly.detected_at - rel.detected_at).total_seconds())
            suspicion = max(0.3, 1 - time_diff / 600)

            explanation = (
                f"Related anomaly in {rel.metric_name} at "
                f"{rel.detected_at.strftime('%H:%M:%S')} "
                f"(severity: {rel.severity}, value: {rel.metric_value:.2f}). "
                f"Multiple metrics spiking simultaneously suggests a systemic issue."
            )

            corr = AnomalyCorrelation(
                anomaly_id=anomaly.id,
                correlation_type="related_anomaly",
                reference_id=rel.id,
                suspicion_score=round(suspicion, 3),
                explanation=explanation,
            )
            self.db.add(corr)
            correlations.append(corr)

        return correlations

    async def _get_service_registry(self, service_name: str) -> ServiceRegistry | None:
        """Fetch service registry entry."""
        result = await self.db.execute(
            select(ServiceRegistry).where(ServiceRegistry.service_name == service_name)
        )
        return result.scalar_one_or_none()

    async def get_full_context_for_anomaly(self, anomaly_id) -> dict:
        """Get complete context for an anomaly — used by AI chat."""
        from sqlalchemy.orm import selectinload

        # Fetch anomaly with correlations
        stmt = (
            select(Anomaly)
            .options(selectinload(Anomaly.correlations))
            .where(Anomaly.id == anomaly_id)
        )
        result = await self.db.execute(stmt)
        anomaly = result.scalar_one_or_none()
        if not anomaly:
            return {}

        # Get service info
        svc = await self._get_service_registry(anomaly.service_name)

        # Get correlated deployments
        deploy_ids = [
            c.reference_id for c in anomaly.correlations
            if c.correlation_type == "deployment"
        ]
        deployments = []
        if deploy_ids:
            stmt = select(DeploymentLog).where(DeploymentLog.id.in_(deploy_ids))
            result = await self.db.execute(stmt)
            deployments = result.scalars().all()

        # Get correlated config changes
        config_ids = [
            c.reference_id for c in anomaly.correlations
            if c.correlation_type == "config_change"
        ]
        config_changes = []
        if config_ids:
            stmt = select(ConfigChangeLog).where(ConfigChangeLog.id.in_(config_ids))
            result = await self.db.execute(stmt)
            config_changes = result.scalars().all()

        # Get related anomalies
        related_ids = [
            c.reference_id for c in anomaly.correlations
            if c.correlation_type == "related_anomaly"
        ]
        related_anomalies = []
        if related_ids:
            stmt = select(Anomaly).where(Anomaly.id.in_(related_ids))
            result = await self.db.execute(stmt)
            related_anomalies = result.scalars().all()

        return {
            "anomaly": {
                "id": str(anomaly.id),
                "service_name": anomaly.service_name,
                "metric_name": anomaly.metric_name,
                "detected_at": anomaly.detected_at.isoformat(),
                "severity": anomaly.severity,
                "confidence_score": anomaly.confidence_score,
                "anomaly_type": anomaly.anomaly_type,
                "metric_value": anomaly.metric_value,
                "baseline_mean": anomaly.baseline_mean,
                "baseline_std": anomaly.baseline_std,
                "z_score": anomaly.z_score,
                "explanation": anomaly.explanation,
                "detection_details": anomaly.detection_details,
            },
            "service": {
                "name": svc.service_name if svc else anomaly.service_name,
                "description": svc.description if svc else None,
                "owner_team": svc.owner_team if svc else None,
                "repository_url": svc.repository_url if svc else None,
                "dependencies": svc.dependencies if svc else None,
                "modules": svc.modules if svc else None,
            },
            "deployments": [
                {
                    "timestamp": d.timestamp.isoformat(),
                    "commit_sha": d.commit_sha,
                    "commit_message": d.commit_message,
                    "author": d.author,
                    "changed_files": d.changed_files,
                    "service_name": d.service_name,
                }
                for d in deployments
            ],
            "config_changes": [
                {
                    "timestamp": c.timestamp.isoformat(),
                    "parameter": c.parameter,
                    "old_value": c.old_value,
                    "new_value": c.new_value,
                    "changed_by": c.changed_by,
                    "service_name": c.service_name,
                }
                for c in config_changes
            ],
            "related_anomalies": [
                {
                    "metric_name": a.metric_name,
                    "detected_at": a.detected_at.isoformat(),
                    "severity": a.severity,
                    "metric_value": a.metric_value,
                    "anomaly_type": a.anomaly_type,
                    "explanation": a.explanation,
                }
                for a in related_anomalies
            ],
            "correlations": [
                {
                    "type": c.correlation_type,
                    "suspicion_score": c.suspicion_score,
                    "explanation": c.explanation,
                }
                for c in anomaly.correlations
            ],
        }
