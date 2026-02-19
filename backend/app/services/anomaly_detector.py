"""Anomaly detection engine using hybrid statistical methods.

Detection layers:
  1. Z-Score — catches sudden spikes/drops
  2. EWMA (Exponentially Weighted Moving Average) with Bollinger Bands — catches sustained deviations
  3. IQR (Interquartile Range) — robust non-parametric detection

Each detector outputs a score in [0, 1]. The final confidence score is a weighted average.
"""

from datetime import datetime, timedelta
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import MetricDataPoint, Anomaly


@dataclass
class DetectionResult:
    """Result from a single detection method."""
    is_anomaly: bool
    score: float  # 0-1 normalized score
    method: str
    details: dict


@dataclass
class AnomalyCandidate:
    """A candidate anomaly from the hybrid detector."""
    timestamp: datetime
    value: float
    severity: str
    confidence_score: float
    anomaly_type: str
    z_score: float | None
    baseline_mean: float
    baseline_std: float
    explanation: str
    detection_details: dict
    window_start: datetime | None
    window_end: datetime | None


class AnomalyDetectorService:
    """Hybrid anomaly detector combining Z-Score, EWMA, and IQR."""

    # Weights for combining detector scores
    WEIGHTS = {"z_score": 0.4, "ewma": 0.35, "iqr": 0.25}

    # Thresholds
    Z_SCORE_THRESHOLD = 3.5
    EWMA_SPAN = 30  # data points for EWMA window
    EWMA_K = 3.0  # multiplier for Bollinger Band width
    EWMA_SUSTAINED_COUNT = 4  # consecutive points outside band
    IQR_MULTIPLIER = 2.0
    MIN_CONFIDENCE = 0.45  # minimum score to report as anomaly

    def __init__(self, db: AsyncSession):
        self.db = db

    async def detect(
        self,
        service_name: str | None = None,
        metric_name: str | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> list[Anomaly]:
        """Run anomaly detection on matching metrics and persist results."""

        # Get distinct service/metric combinations to detect on
        combos = await self._get_metric_combinations(service_name, metric_name)

        all_anomalies = []
        for svc, metric in combos:
            data = await self._fetch_metric_data(svc, metric, from_ts, to_ts)
            if len(data) < 10:
                continue

            timestamps = [d.timestamp for d in data]
            values = np.array([d.value for d in data], dtype=np.float64)

            candidates = self._detect_anomalies(timestamps, values, svc, metric)

            # Deduplicate: skip anomalies within 5 minutes of an existing one
            existing = await self._get_existing_anomalies(svc, metric, from_ts, to_ts)
            existing_times = {a.detected_at for a in existing}

            for candidate in candidates:
                if any(abs((candidate.timestamp - et).total_seconds()) < 300 for et in existing_times):
                    continue

                anomaly = Anomaly(
                    service_name=svc,
                    metric_name=metric,
                    detected_at=candidate.timestamp,
                    severity=candidate.severity,
                    confidence_score=candidate.confidence_score,
                    anomaly_type=candidate.anomaly_type,
                    metric_value=candidate.value,
                    baseline_mean=candidate.baseline_mean,
                    baseline_std=candidate.baseline_std,
                    z_score=candidate.z_score,
                    explanation=candidate.explanation,
                    detection_details=candidate.detection_details,
                    window_start=candidate.window_start,
                    window_end=candidate.window_end,
                )
                self.db.add(anomaly)
                all_anomalies.append(anomaly)

        await self.db.flush()  # get IDs assigned
        return all_anomalies

    def _detect_anomalies(
        self,
        timestamps: list[datetime],
        values: np.ndarray,
        service_name: str,
        metric_name: str,
    ) -> list[AnomalyCandidate]:
        """Run all detection methods and combine results."""
        n = len(values)
        if n < 10:
            return []

        # Precompute rolling statistics
        rolling_window = min(30, n // 3)
        global_mean = float(np.mean(values))
        global_std = float(np.std(values)) if float(np.std(values)) > 0 else 1e-9

        # Z-score detection
        z_results = self._detect_zscore(values, rolling_window)

        # EWMA detection
        ewma_results = self._detect_ewma(values)

        # IQR detection
        iqr_results = self._detect_iqr(values, rolling_window)

        # Combine results per data point
        candidates = []
        for i in range(n):
            z_r = z_results[i]
            ewma_r = ewma_results[i]
            iqr_r = iqr_results[i]

            # Weighted confidence score
            score = (
                self.WEIGHTS["z_score"] * z_r.score
                + self.WEIGHTS["ewma"] * ewma_r.score
                + self.WEIGHTS["iqr"] * iqr_r.score
            )

            if score < self.MIN_CONFIDENCE:
                continue

            # Determine anomaly type
            anomaly_type = self._classify_anomaly_type(values, i, z_r, ewma_r)

            # Determine severity using composite criteria:
            # - Critical: confidence ≥ 75% AND (Z-Score > 6σ OR sustained ≥ 8 points)
            # - Warning:  confidence ≥ 55% AND at least 2 methods triggered
            # - Info:     anything else above MIN_CONFIDENCE
            abs_z = abs(z_r.details.get("z_score", 0))
            consecutive = ewma_r.details.get("consecutive_outside", 0)
            methods_triggered = sum([z_r.is_anomaly, ewma_r.is_anomaly, iqr_r.is_anomaly])

            if score >= 0.75 and (abs_z > 6 or consecutive >= 8):
                severity = "critical"
            elif score >= 0.55 and methods_triggered >= 2:
                severity = "warning"
            else:
                severity = "info"

            # Build explanation
            local_start = max(0, i - rolling_window)
            local_mean = float(np.mean(values[local_start:i])) if i > 0 else global_mean
            local_std = float(np.std(values[local_start:i])) if i > 1 else global_std

            explanation = self._build_explanation(
                service_name=service_name,
                metric_name=metric_name,
                timestamp=timestamps[i],
                value=float(values[i]),
                baseline_mean=local_mean,
                baseline_std=local_std,
                z_score=z_r.details.get("z_score"),
                anomaly_type=anomaly_type,
                severity=severity,
                score=score,
                z_r=z_r,
                ewma_r=ewma_r,
                iqr_r=iqr_r,
            )

            # Window context
            window_start = timestamps[max(0, i - rolling_window)]
            window_end = timestamps[min(n - 1, i + 5)]

            candidates.append(AnomalyCandidate(
                timestamp=timestamps[i],
                value=float(values[i]),
                severity=severity,
                confidence_score=round(score, 4),
                anomaly_type=anomaly_type,
                z_score=z_r.details.get("z_score"),
                baseline_mean=round(local_mean, 4),
                baseline_std=round(local_std, 4),
                explanation=explanation,
                detection_details={
                    "z_score": z_r.details,
                    "ewma": ewma_r.details,
                    "iqr": iqr_r.details,
                    "weights": self.WEIGHTS,
                },
                window_start=window_start,
                window_end=window_end,
            ))

        return candidates

    def _detect_zscore(self, values: np.ndarray, window: int) -> list[DetectionResult]:
        """Z-Score based detection using rolling window."""
        n = len(values)
        results = []
        for i in range(n):
            start = max(0, i - window)
            window_vals = values[start:i] if i > 0 else values[:1]
            if len(window_vals) < 2:
                results.append(DetectionResult(False, 0.0, "z_score", {"z_score": 0.0}))
                continue

            mean = float(np.mean(window_vals))
            std = float(np.std(window_vals))
            if std < 1e-9:
                std = 1e-9

            z = (values[i] - mean) / std
            abs_z = abs(z)

            # Normalize to [0, 1] score: 0 at threshold, 1 at 2x threshold
            if abs_z >= self.Z_SCORE_THRESHOLD:
                score = min(1.0, (abs_z - self.Z_SCORE_THRESHOLD) / self.Z_SCORE_THRESHOLD + 0.5)
                is_anomaly = True
            else:
                score = max(0.0, abs_z / self.Z_SCORE_THRESHOLD * 0.4)
                is_anomaly = False

            results.append(DetectionResult(
                is_anomaly=is_anomaly,
                score=round(score, 4),
                method="z_score",
                details={"z_score": round(float(z), 4), "mean": round(mean, 4), "std": round(std, 4)},
            ))
        return results

    def _detect_ewma(self, values: np.ndarray) -> list[DetectionResult]:
        """EWMA with Bollinger Bands for sustained deviation detection."""
        n = len(values)
        alpha = 2.0 / (self.EWMA_SPAN + 1)
        results = []

        ewma = np.zeros(n)
        ewma_var = np.zeros(n)
        ewma[0] = values[0]
        ewma_var[0] = 0.0

        for i in range(1, n):
            ewma[i] = alpha * values[i] + (1 - alpha) * ewma[i - 1]
            diff = values[i] - ewma[i - 1]
            ewma_var[i] = alpha * (diff ** 2) + (1 - alpha) * ewma_var[i - 1]

        ewma_std = np.sqrt(ewma_var)
        consecutive_outside = 0

        for i in range(n):
            if ewma_std[i] < 1e-9:
                results.append(DetectionResult(False, 0.0, "ewma", {
                    "ewma": round(float(ewma[i]), 4),
                    "upper_band": 0.0, "lower_band": 0.0,
                    "consecutive_outside": 0,
                }))
                continue

            upper = ewma[i] + self.EWMA_K * ewma_std[i]
            lower = ewma[i] - self.EWMA_K * ewma_std[i]

            outside = values[i] > upper or values[i] < lower
            if outside:
                consecutive_outside += 1
            else:
                consecutive_outside = 0

            deviation = abs(values[i] - ewma[i]) / ewma_std[i] if ewma_std[i] > 1e-9 else 0
            sustained_factor = min(1.0, consecutive_outside / self.EWMA_SUSTAINED_COUNT)

            if outside and consecutive_outside >= self.EWMA_SUSTAINED_COUNT:
                score = min(1.0, 0.5 + sustained_factor * 0.3 + deviation / 10)
                is_anomaly = True
            elif outside:
                score = min(0.5, deviation / 8 + sustained_factor * 0.2)
                is_anomaly = deviation > 3
            else:
                score = max(0.0, deviation / 12)
                is_anomaly = False

            results.append(DetectionResult(
                is_anomaly=is_anomaly,
                score=round(score, 4),
                method="ewma",
                details={
                    "ewma": round(float(ewma[i]), 4),
                    "upper_band": round(float(upper), 4),
                    "lower_band": round(float(lower), 4),
                    "consecutive_outside": consecutive_outside,
                },
            ))
        return results

    def _detect_iqr(self, values: np.ndarray, window: int) -> list[DetectionResult]:
        """IQR-based non-parametric detection."""
        n = len(values)
        results = []
        for i in range(n):
            start = max(0, i - window)
            window_vals = values[start:i] if i > 0 else values[:1]
            if len(window_vals) < 4:
                results.append(DetectionResult(False, 0.0, "iqr", {
                    "q1": 0.0, "q3": 0.0, "iqr": 0.0,
                }))
                continue

            q1 = float(np.percentile(window_vals, 25))
            q3 = float(np.percentile(window_vals, 75))
            iqr = q3 - q1
            if iqr < 1e-9:
                iqr = 1e-9

            lower_fence = q1 - self.IQR_MULTIPLIER * iqr
            upper_fence = q3 + self.IQR_MULTIPLIER * iqr

            val = float(values[i])
            outside = val < lower_fence or val > upper_fence

            if outside:
                dist = max(val - upper_fence, lower_fence - val)
                score = min(1.0, 0.4 + dist / (iqr * 3))
                is_anomaly = True
            else:
                score = 0.0
                is_anomaly = False

            results.append(DetectionResult(
                is_anomaly=is_anomaly,
                score=round(score, 4),
                method="iqr",
                details={
                    "q1": round(q1, 4),
                    "q3": round(q3, 4),
                    "iqr": round(iqr, 4),
                    "lower_fence": round(lower_fence, 4),
                    "upper_fence": round(upper_fence, 4),
                },
            ))
        return results

    def _classify_anomaly_type(
        self,
        values: np.ndarray,
        idx: int,
        z_r: DetectionResult,
        ewma_r: DetectionResult,
    ) -> str:
        """Classify the type of anomaly based on detection signals."""
        z = z_r.details.get("z_score", 0)
        consecutive = ewma_r.details.get("consecutive_outside", 0)

        if abs(z) >= 5:
            return "spike" if z > 0 else "drop"
        if consecutive >= self.EWMA_SUSTAINED_COUNT:
            return "sustained_deviation"
        if abs(z) >= self.Z_SCORE_THRESHOLD:
            return "spike" if z > 0 else "drop"
        return "pattern_change"

    def _build_explanation(
        self,
        service_name: str,
        metric_name: str,
        timestamp: datetime,
        value: float,
        baseline_mean: float,
        baseline_std: float,
        z_score: float | None,
        anomaly_type: str,
        severity: str,
        score: float,
        z_r: DetectionResult,
        ewma_r: DetectionResult,
        iqr_r: DetectionResult,
    ) -> str:
        """Build a human-readable explanation of why this data point is anomalous."""
        ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        parts = [
            f"[{severity.upper()}] Anomaly detected in {service_name}/{metric_name} at {ts_str}.",
            f"Observed value: {value:.2f} (baseline mean: {baseline_mean:.2f} ± {baseline_std:.2f}).",
        ]

        if anomaly_type == "spike":
            parts.append(f"This is a sudden spike — the value is significantly above the normal range.")
        elif anomaly_type == "drop":
            parts.append(f"This is a sudden drop — the value fell significantly below the normal range.")
        elif anomaly_type == "sustained_deviation":
            parts.append(
                f"This is a sustained deviation — the metric has been consistently outside "
                f"normal bounds for {ewma_r.details.get('consecutive_outside', '?')} consecutive data points."
            )
        else:
            parts.append(f"The metric shows a pattern change from its expected behavior.")

        # Detection method breakdown
        methods = []
        if z_r.is_anomaly:
            methods.append(f"Z-Score: {z_score:.1f}σ from rolling mean")
        if ewma_r.is_anomaly:
            methods.append(f"EWMA: outside Bollinger Band for {ewma_r.details.get('consecutive_outside', '?')} points")
        if iqr_r.is_anomaly:
            methods.append(f"IQR: value outside interquartile fences")

        if methods:
            parts.append(f"Detection signals: {'; '.join(methods)}.")

        parts.append(f"Confidence score: {score:.1%}.")

        return " ".join(parts)

    async def _get_metric_combinations(
        self, service_name: str | None, metric_name: str | None
    ) -> list[tuple[str, str]]:
        """Get unique (service, metric) combos to run detection on."""
        from sqlalchemy import distinct
        stmt = select(
            distinct(MetricDataPoint.service_name),
            MetricDataPoint.metric_name,
        )
        conditions = []
        if service_name:
            conditions.append(MetricDataPoint.service_name == service_name)
        if metric_name:
            conditions.append(MetricDataPoint.metric_name == metric_name)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.group_by(MetricDataPoint.service_name, MetricDataPoint.metric_name)
        result = await self.db.execute(stmt)
        return [(r[0], r[1]) for r in result.all()]

    # Maximum number of data points loaded into memory per metric for detection.
    # With millions of records per metric, loading all is infeasible.
    # 50 000 points at 1-min intervals ≈ ~35 days — plenty for anomaly detection.
    MAX_FETCH_POINTS = 50_000

    async def _fetch_metric_data(
        self,
        service_name: str,
        metric_name: str,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> list[MetricDataPoint]:
        """Fetch sorted metric data for a service/metric combo.

        Caps at MAX_FETCH_POINTS most-recent rows to prevent OOM on large datasets.
        """
        stmt = select(MetricDataPoint).where(
            and_(
                MetricDataPoint.service_name == service_name,
                MetricDataPoint.metric_name == metric_name,
            )
        )
        if from_ts:
            stmt = stmt.where(MetricDataPoint.timestamp >= from_ts)
        if to_ts:
            stmt = stmt.where(MetricDataPoint.timestamp <= to_ts)
        stmt = stmt.order_by(MetricDataPoint.timestamp.asc()).limit(self.MAX_FETCH_POINTS)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _get_existing_anomalies(
        self,
        service_name: str,
        metric_name: str,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> list[Anomaly]:
        """Get existing anomalies for deduplication."""
        stmt = select(Anomaly).where(
            and_(
                Anomaly.service_name == service_name,
                Anomaly.metric_name == metric_name,
            )
        )
        if from_ts:
            stmt = stmt.where(Anomaly.detected_at >= from_ts)
        if to_ts:
            stmt = stmt.where(Anomaly.detected_at <= to_ts)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
