"""Prometheus metrics polling service.

Fetches metrics from a Prometheus endpoint using the instant query API
and ingests them as MetricDataPoint records. Designed to run as an
asyncio background task that polls at a configurable interval.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import MetricDataPoint, WorkspaceConfig

logger = logging.getLogger(__name__)


class PrometheusPoller:
    """Polls a Prometheus endpoint and ingests metrics."""

    def __init__(self, endpoint: str, queries: list[dict] | None = None):
        """
        Args:
            endpoint: Prometheus base URL (e.g. "http://prometheus:9090")
            queries: List of query configs:
                [{"query": "rate(http_requests_total[5m])", "service_name": "api", "metric_name": "request_rate"}, ...]
        """
        self.endpoint = endpoint.rstrip("/")
        self.queries = queries or []
        self.client = httpx.AsyncClient(timeout=15.0)

    async def close(self):
        await self.client.aclose()

    async def poll_once(self, db: AsyncSession) -> int:
        """Execute all configured queries and ingest results.

        Returns the number of data points ingested.
        """
        total = 0
        for qcfg in self.queries:
            try:
                points = await self._execute_query(qcfg)
                for point in points:
                    db.add(point)
                total += len(points)
            except Exception as e:
                logger.error(f"Prometheus query failed: {qcfg.get('query', '?')}: {e}")

        if total > 0:
            await db.flush()

        return total

    async def _execute_query(self, qcfg: dict) -> list[MetricDataPoint]:
        """Execute a single Prometheus instant query and return data points."""
        query = qcfg["query"]
        service_name = qcfg.get("service_name", "unknown")
        metric_name = qcfg.get("metric_name", query[:100])

        resp = await self.client.get(
            f"{self.endpoint}/api/v1/query",
            params={"query": query},
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "success":
            raise ValueError(f"Prometheus error: {data.get('error', 'unknown')}")

        points = []
        for result in data.get("data", {}).get("result", []):
            # result["value"] is [timestamp, value_string]
            ts_unix, val_str = result["value"]
            try:
                value = float(val_str)
            except (ValueError, TypeError):
                continue

            # Extract label-based service/metric name overrides
            labels = result.get("metric", {})
            effective_service = labels.get("service", labels.get("job", service_name))
            effective_metric = labels.get("__name__", metric_name)

            # Build label dict (exclude internal labels)
            clean_labels = {
                k: v for k, v in labels.items()
                if not k.startswith("__") and k not in ("job", "instance")
            }

            points.append(MetricDataPoint(
                service_name=effective_service,
                metric_name=effective_metric,
                value=round(value, 6),
                timestamp=datetime.utcfromtimestamp(ts_unix),
                labels=clean_labels if clean_labels else None,
            ))

        return points

    async def test_connection(self) -> dict:
        """Test connectivity to the Prometheus endpoint.

        Returns status and available metric names.
        """
        try:
            # Check healthy
            resp = await self.client.get(f"{self.endpoint}/api/v1/status/buildinfo")
            resp.raise_for_status()
            build = resp.json()

            # Get label values for job (available services)
            jobs_resp = await self.client.get(f"{self.endpoint}/api/v1/label/job/values")
            jobs = jobs_resp.json().get("data", []) if jobs_resp.status_code == 200 else []

            return {
                "status": "connected",
                "version": build.get("data", {}).get("version", "unknown"),
                "available_jobs": jobs[:50],
            }
        except httpx.ConnectError:
            return {"status": "error", "error": "Cannot connect to Prometheus endpoint"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def discover_metrics(self) -> list[dict]:
        """Discover available metrics from the Prometheus endpoint.

        Returns a list of {metric_name, type, help} for common metrics.
        """
        try:
            resp = await self.client.get(f"{self.endpoint}/api/v1/label/__name__/values")
            resp.raise_for_status()
            names = resp.json().get("data", [])

            # Return first 100 metric names
            return [{"metric_name": name} for name in sorted(names)[:100]]
        except Exception as e:
            return [{"error": str(e)}]


# ── Background polling task ─────────────────────────────────────────

_polling_task: asyncio.Task | None = None


async def _polling_loop(db_factory, config_id):
    """Background loop that polls Prometheus at the configured interval."""
    from app.models.db_models import WorkspaceConfig

    while True:
        try:
            async with db_factory() as db:
                # Re-read config each iteration (user may have changed it)
                result = await db.execute(
                    select(WorkspaceConfig).where(WorkspaceConfig.id == config_id)
                )
                config = result.scalar_one_or_none()

                if not config or config.is_polling != "true" or not config.prometheus_endpoint:
                    logger.info("Polling stopped — config removed or polling disabled")
                    return

                poller = PrometheusPoller(
                    endpoint=config.prometheus_endpoint,
                    queries=config.prometheus_queries or [],
                )
                try:
                    count = await poller.poll_once(db)
                    await db.commit()
                    if count > 0:
                        logger.info(f"Prometheus poll: ingested {count} data points")
                finally:
                    await poller.close()

                interval = config.prometheus_poll_interval_seconds or 60

        except Exception as e:
            logger.error(f"Prometheus polling error: {e}")
            interval = 60  # fallback interval on error

        await asyncio.sleep(interval)


def start_polling(db_factory, config_id) -> asyncio.Task:
    """Start the background polling task. Returns the task handle."""
    global _polling_task
    if _polling_task and not _polling_task.done():
        _polling_task.cancel()

    _polling_task = asyncio.create_task(_polling_loop(db_factory, config_id))
    return _polling_task


def stop_polling():
    """Stop the background polling task."""
    global _polling_task
    if _polling_task and not _polling_task.done():
        _polling_task.cancel()
        _polling_task = None
