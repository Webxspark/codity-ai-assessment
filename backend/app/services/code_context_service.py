"""Code context service — correlates anomalies with deployments, config changes, and services."""

from datetime import timedelta

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import (
    Anomaly,
    AnomalyCorrelation,
    DeploymentLog,
    ConfigChangeLog,
    MetricDataPoint,
    ServiceRegistry,
)


class CodeContextService:
    """Associates anomalies with code changes and service context."""

    # Look-back windows for correlating changes to anomalies
    DEPLOYMENT_WINDOW_MINUTES = 360  # 6 hours — commits may be hours old
    CONFIG_CHANGE_WINDOW_MINUTES = 120

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
        """Find anomalies across ALL services around the same time (cross-service correlation)."""
        window = timedelta(minutes=10)

        stmt = select(Anomaly).where(
            and_(
                # Cross-service: match any service, just exclude the exact same anomaly
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
            same_service = rel.service_name == anomaly.service_name
            # Same-service anomalies are more suspicious; cross-service suggests cascading failure
            base_suspicion = max(0.3, 1 - time_diff / 600)
            suspicion = min(1.0, base_suspicion + (0.1 if same_service else 0.0))

            if same_service:
                explanation = (
                    f"Related anomaly in {rel.metric_name} on same service at "
                    f"{rel.detected_at.strftime('%H:%M:%S')} "
                    f"(severity: {rel.severity}, value: {rel.metric_value:.2f}). "
                    f"Multiple metrics spiking simultaneously suggests a systemic issue."
                )
            else:
                explanation = (
                    f"Cross-service anomaly: {rel.service_name}/{rel.metric_name} at "
                    f"{rel.detected_at.strftime('%H:%M:%S')} "
                    f"(severity: {rel.severity}, value: {rel.metric_value:.2f}). "
                    f"Anomalies in multiple services within a short window suggest "
                    f"a cascading failure or shared root cause."
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
        """Get complete context for an anomaly — used by AI chat.

        This performs *both* a lookup of pre-stored correlations and a
        live query of nearby deployments / config changes.  The live
        fallback is critical because correlations may not have been
        computed yet (e.g., anomaly detected before a config change was
        ingested) or may have been run with a narrower window.  By
        always including live data, the LLM never misses obvious
        context like a rate-limit change 19 minutes before a drop.

        Token budget:  To remain well within even small model context
        windows, metric trend data is downsampled to at most
        MAX_TREND_POINTS points and correlation explanations are
        truncated.
        """
        from sqlalchemy.orm import selectinload

        MAX_TREND_POINTS = 60     # ≈1 per minute for 1h
        CONTEXT_WINDOW_MIN = 360  # live query window (6h)

        # ── 1. Fetch the anomaly with stored correlations ────────────
        stmt = (
            select(Anomaly)
            .options(selectinload(Anomaly.correlations))
            .where(Anomaly.id == anomaly_id)
        )
        result = await self.db.execute(stmt)
        anomaly = result.scalar_one_or_none()
        if not anomaly:
            return {}

        # ── 2. Service info ──────────────────────────────────────────
        svc = await self._get_service_registry(anomaly.service_name)

        # ── 3. Gather deployments (pre-stored + live fallback) ───────
        deploy_ids = [
            c.reference_id for c in anomaly.correlations
            if c.correlation_type == "deployment"
        ]
        deployments = []
        if deploy_ids:
            stmt = select(DeploymentLog).where(DeploymentLog.id.in_(deploy_ids))
            result = await self.db.execute(stmt)
            deployments = list(result.scalars().all())

        # Live fallback: always query nearby deployments so we never
        # miss a deploy that wasn't pre-correlated.
        live_window_start = anomaly.detected_at - timedelta(minutes=CONTEXT_WINDOW_MIN)
        live_window_end = anomaly.detected_at + timedelta(minutes=30)

        service_names = [anomaly.service_name]
        if svc and svc.dependencies:
            service_names.extend(svc.dependencies)

        live_deploy_stmt = select(DeploymentLog).where(
            and_(
                DeploymentLog.service_name.in_(service_names),
                DeploymentLog.timestamp >= live_window_start,
                DeploymentLog.timestamp <= live_window_end,
            )
        ).order_by(DeploymentLog.timestamp.desc()).limit(20)
        result = await self.db.execute(live_deploy_stmt)
        live_deploys = result.scalars().all()

        # Merge, dedup by ID
        existing_ids = {d.id for d in deployments}
        for ld in live_deploys:
            if ld.id not in existing_ids:
                deployments.append(ld)
                existing_ids.add(ld.id)

        # ── 4. Gather config changes (pre-stored + live fallback) ────
        config_ids = [
            c.reference_id for c in anomaly.correlations
            if c.correlation_type == "config_change"
        ]
        config_changes = []
        if config_ids:
            stmt = select(ConfigChangeLog).where(ConfigChangeLog.id.in_(config_ids))
            result = await self.db.execute(stmt)
            config_changes = list(result.scalars().all())

        # Live fallback for config changes
        live_cfg_stmt = select(ConfigChangeLog).where(
            and_(
                ConfigChangeLog.service_name == anomaly.service_name,
                ConfigChangeLog.timestamp >= live_window_start,
                ConfigChangeLog.timestamp <= live_window_end,
            )
        ).order_by(ConfigChangeLog.timestamp.desc()).limit(20)
        result = await self.db.execute(live_cfg_stmt)
        live_cfgs = result.scalars().all()

        existing_cfg_ids = {c.id for c in config_changes}
        for lc in live_cfgs:
            if lc.id not in existing_cfg_ids:
                config_changes.append(lc)
                existing_cfg_ids.add(lc.id)

        # ── 5. Related anomalies ─────────────────────────────────────
        related_ids = [
            c.reference_id for c in anomaly.correlations
            if c.correlation_type == "related_anomaly"
        ]
        related_anomalies = []
        if related_ids:
            stmt = select(Anomaly).where(Anomaly.id.in_(related_ids)).limit(15)
            result = await self.db.execute(stmt)
            related_anomalies = list(result.scalars().all())

        # Live fallback: cross-service anomalies within ±15 min
        live_rel_stmt = select(Anomaly).where(
            and_(
                Anomaly.detected_at >= anomaly.detected_at - timedelta(minutes=15),
                Anomaly.detected_at <= anomaly.detected_at + timedelta(minutes=15),
                Anomaly.id != anomaly.id,
            )
        ).order_by(Anomaly.detected_at.asc()).limit(15)
        result = await self.db.execute(live_rel_stmt)
        live_rels = result.scalars().all()

        existing_rel_ids = {r.id for r in related_anomalies}
        for lr in live_rels:
            if lr.id not in existing_rel_ids:
                related_anomalies.append(lr)
                existing_rel_ids.add(lr.id)

        # ── 6. Metric trend (downsampled for token budget) ───────────
        trend_window_start = anomaly.detected_at - timedelta(minutes=60)
        trend_window_end = anomaly.detected_at + timedelta(minutes=15)
        trend_stmt = (
            select(MetricDataPoint)
            .where(
                and_(
                    MetricDataPoint.service_name == anomaly.service_name,
                    MetricDataPoint.metric_name == anomaly.metric_name,
                    MetricDataPoint.timestamp >= trend_window_start,
                    MetricDataPoint.timestamp <= trend_window_end,
                )
            )
            .order_by(MetricDataPoint.timestamp.asc())
            .limit(MAX_TREND_POINTS * 3)  # fetch more, then downsample
        )
        result = await self.db.execute(trend_stmt)
        trend_points = result.scalars().all()

        # Downsample if too many points
        if len(trend_points) > MAX_TREND_POINTS:
            step = max(1, len(trend_points) // MAX_TREND_POINTS)
            trend_points = trend_points[::step][:MAX_TREND_POINTS]

        metric_trend = [
            {"t": p.timestamp.strftime("%H:%M"), "v": round(p.value, 2)}
            for p in trend_points
        ]

        # ── 7. Build context dict ────────────────────────────────────
        def _deploy_dict(d: DeploymentLog) -> dict:
            time_diff = (anomaly.detected_at - d.timestamp).total_seconds()
            direction = "before" if time_diff >= 0 else "after"
            mins = abs(int(time_diff / 60))
            return {
                "timestamp": d.timestamp.isoformat(),
                "relative": f"{mins} min {direction} anomaly",
                "commit_sha": d.commit_sha,
                "commit_message": d.commit_message,
                "author": d.author,
                "changed_files": d.changed_files,
                "service_name": d.service_name,
            }

        def _config_dict(c: ConfigChangeLog) -> dict:
            time_diff = (anomaly.detected_at - c.timestamp).total_seconds()
            direction = "before" if time_diff >= 0 else "after"
            mins = abs(int(time_diff / 60))
            return {
                "timestamp": c.timestamp.isoformat(),
                "relative": f"{mins} min {direction} anomaly",
                "parameter": c.parameter,
                "old_value": c.old_value,
                "new_value": c.new_value,
                "changed_by": c.changed_by,
                "service_name": c.service_name,
            }

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
            },
            "service": {
                "name": svc.service_name if svc else anomaly.service_name,
                "description": svc.description if svc else None,
                "owner_team": svc.owner_team if svc else None,
                "repository_url": svc.repository_url if svc else None,
                "dependencies": svc.dependencies if svc else None,
            },
            "nearby_deployments": sorted(
                [_deploy_dict(d) for d in deployments],
                key=lambda x: x["timestamp"],
                reverse=True,
            ),
            "nearby_config_changes": sorted(
                [_config_dict(c) for c in config_changes],
                key=lambda x: x["timestamp"],
                reverse=True,
            ),
            "related_anomalies": [
                {
                    "service_name": a.service_name,
                    "metric_name": a.metric_name,
                    "detected_at": a.detected_at.isoformat(),
                    "severity": a.severity,
                    "metric_value": round(a.metric_value, 2),
                    "anomaly_type": a.anomaly_type,
                }
                for a in related_anomalies[:10]
            ],
            "metric_trend_around_anomaly": metric_trend,
            "correlations": [
                {
                    "type": c.correlation_type,
                    "suspicion_score": c.suspicion_score,
                    "explanation": c.explanation[:300] if c.explanation else None,
                }
                for c in anomaly.correlations[:10]
            ],
        }
