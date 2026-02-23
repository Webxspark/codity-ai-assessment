"""Prometheus metrics polling service.

Fetches metrics from a Prometheus endpoint using the instant query API
and ingests them as MetricDataPoint records. Designed to run as an
asyncio background task that polls at a configurable interval.

Supports historical backfill via Prometheus range query API so the
system can be populated with hours of data on first connect instead
of starting from zero.

After each poll cycle the background loop also:
  1. Auto-registers discovered services in ServiceRegistry, linking them
     to the configured GitHub repo so anomaly ↔ commit correlation works.
  2. Periodically syncs recent GitHub commits as DeploymentLog entries.
  3. Periodically runs anomaly detection on recent data so alerts appear
     automatically without requiring manual triggering.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import MetricDataPoint, ServiceRegistry, WorkspaceConfig

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

    async def poll_once(self, db: AsyncSession) -> tuple[int, set[str]]:
        """Execute all configured queries and ingest results.

        Returns (data_points_ingested, set_of_service_names_seen).
        """
        total = 0
        service_names: set[str] = set()
        for qcfg in self.queries:
            try:
                points = await self._execute_query(qcfg)
                for point in points:
                    db.add(point)
                    service_names.add(point.service_name)
                total += len(points)
            except Exception as e:
                logger.error(f"Prometheus query failed: {qcfg.get('query', '?')}: {e}")

        if total > 0:
            await db.flush()

        return total, service_names

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

    async def backfill_range(
        self,
        db: AsyncSession,
        hours_back: float = 2.0,
        step_seconds: int = 60,
    ) -> tuple[int, set[str]]:
        """Backfill historical data from Prometheus using range queries.

        Uses /api/v1/query_range to fetch `hours_back` hours of data at
        `step_seconds` resolution. This lets the system start with a rich
        dataset for anomaly detection instead of waiting for live data to
        accumulate.

        Returns (data_points_ingested, set_of_service_names_seen).
        """
        end = datetime.utcnow()
        start = end - timedelta(hours=hours_back)
        total = 0
        service_names: set[str] = set()

        for qcfg in self.queries:
            try:
                points = await self._execute_range_query(
                    qcfg, start, end, step_seconds
                )
                for point in points:
                    db.add(point)
                    service_names.add(point.service_name)
                total += len(points)
            except Exception as e:
                logger.error(
                    f"Prometheus range query failed: {qcfg.get('query', '?')}: {e}"
                )

        if total > 0:
            await db.flush()
            logger.info(
                f"Backfilled {total} data points from {hours_back}h of history "
                f"(step={step_seconds}s, services={sorted(service_names)})"
            )

        return total, service_names

    async def _execute_range_query(
        self,
        qcfg: dict,
        start: datetime,
        end: datetime,
        step_seconds: int,
    ) -> list[MetricDataPoint]:
        """Execute a Prometheus range query and return data points."""
        query = qcfg["query"]
        service_name = qcfg.get("service_name", "unknown")
        metric_name = qcfg.get("metric_name", query[:100])

        resp = await self.client.get(
            f"{self.endpoint}/api/v1/query_range",
            params={
                "query": query,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": f"{step_seconds}s",
            },
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "success":
            raise ValueError(f"Prometheus error: {data.get('error', 'unknown')}")

        points = []
        for result in data.get("data", {}).get("result", []):
            labels = result.get("metric", {})
            effective_service = labels.get(
                "service", labels.get("job", service_name)
            )
            effective_metric = labels.get("__name__", metric_name)

            clean_labels = {
                k: v
                for k, v in labels.items()
                if not k.startswith("__") and k not in ("job", "instance")
            }

            # result["values"] is [[timestamp, value_string], ...]
            for ts_unix, val_str in result.get("values", []):
                try:
                    value = float(val_str)
                except (ValueError, TypeError):
                    continue

                points.append(
                    MetricDataPoint(
                        service_name=effective_service,
                        metric_name=effective_metric,
                        value=round(value, 6),
                        timestamp=datetime.utcfromtimestamp(ts_unix),
                        labels=clean_labels if clean_labels else None,
                    )
                )

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

# How many poll cycles between automatic GitHub syncs
_GITHUB_SYNC_EVERY_N_CYCLES = 10
# How many poll cycles between automatic anomaly detection runs
_AUTO_DETECT_EVERY_N_CYCLES = 5


async def _auto_register_services(
    db: AsyncSession,
    service_names: set[str],
    repo_service: str,
    github_repo: str,
) -> None:
    """Ensure each discovered Prometheus service has a ServiceRegistry entry
    with the configured GitHub repo listed as a dependency.  This is what
    makes anomaly ↔ commit correlation work across different service names.
    """
    for svc_name in service_names:
        if svc_name == repo_service:
            continue  # no need to register the repo itself as its own dep
        result = await db.execute(
            select(ServiceRegistry).where(ServiceRegistry.service_name == svc_name)
        )
        existing = result.scalar_one_or_none()

        if not existing:
            db.add(ServiceRegistry(
                service_name=svc_name,
                description=f"Auto-discovered from Prometheus metrics",
                repository_url=f"https://github.com/{github_repo}",
                dependencies=[repo_service],
            ))
            logger.info(f"Auto-registered service '{svc_name}' → depends on '{repo_service}'")
        elif repo_service not in (existing.dependencies or []):
            deps = list(existing.dependencies or [])
            deps.append(repo_service)
            existing.dependencies = deps
            logger.info(f"Added '{repo_service}' as dependency of '{svc_name}'")


async def _update_service_modules(
    db: AsyncSession,
    deployments: list[dict],
) -> None:
    """Extract module/directory names from deployment changed files and update
    the ServiceRegistry.modules field.  This creates the metric → code linkage
    required by the assignment: each monitored service is associated with the
    code modules (directories) that were changed in nearby deployments.
    """
    # Collect all unique top-level modules from changed files
    modules: set[str] = set()
    for deploy in deployments:
        for filepath in deploy.get("changed_files", []):
            parts = filepath.split("/")
            if len(parts) > 1:
                modules.add(parts[0])  # top-level directory
            else:
                modules.add(filepath)   # root-level file

    if not modules:
        return

    # Update all registered services with these modules
    result = await db.execute(select(ServiceRegistry))
    services = result.scalars().all()
    for svc in services:
        existing = set(svc.modules or [])
        combined = existing | modules
        if combined != existing:
            svc.modules = sorted(combined)
            logger.info(f"Updated modules for '{svc.service_name}': {svc.modules}")


async def _auto_sync_github(db: AsyncSession, config: "WorkspaceConfig") -> int:
    """Sync recent GitHub commits as DeploymentLog entries.
    Returns the number of new commits synced."""
    from app.services.github_service import GitHubService, GitHubRateLimitError

    svc = GitHubService(repo=config.github_repo, token=config.github_token)
    try:
        since = datetime.utcnow() - timedelta(hours=4)
        branch = config.github_default_branch or "main"
        service_name = config.github_repo.split("/")[-1]

        new = await svc.sync_commits_to_deployments(
            db_session=db,
            service_name=service_name,
            branch=branch,
            since=since,
            limit=10,
        )
        if new:
            logger.info(f"Auto-synced {len(new)} GitHub commits")
            # Extract modules (top-level directories) from changed files
            # and add them to all registered services for code linkage
            await _update_service_modules(db, new)
        return len(new)
    except GitHubRateLimitError:
        logger.warning("GitHub rate limit hit during auto-sync — will retry next cycle")
        return 0
    except Exception as e:
        logger.error(f"GitHub auto-sync failed: {e}")
        return 0
    finally:
        await svc.close()


async def _auto_detect_anomalies(db: AsyncSession) -> int:
    """Run anomaly detection on recent data and correlate any findings.

    Only scans the last 2 hours of data to keep it lightweight.
    Returns the number of new anomalies detected.
    """
    from app.services.anomaly_detector import AnomalyDetectorService
    from app.services.code_context_service import CodeContextService

    try:
        from_ts = datetime.utcnow() - timedelta(hours=2)
        detector = AnomalyDetectorService(db)
        anomalies = await detector.detect(from_ts=from_ts)

        if anomalies:
            ctx_service = CodeContextService(db)
            for anomaly in anomalies:
                await ctx_service.correlate_anomaly(anomaly)

            logger.info(
                f"Auto-detection: found {len(anomalies)} new anomalies in last 2h"
            )

        return len(anomalies)
    except Exception as e:
        logger.error(f"Auto-detection failed: {e}")
        return 0


async def _polling_loop(db_factory, config_id):
    """Background loop: poll Prometheus → register services → sync GitHub."""

    cycle_count = 0
    backfilled = False

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
                    # On first cycle, backfill historical data from Prometheus
                    # so the system has enough data for anomaly detection
                    if not backfilled:
                        bf_count, service_names = await poller.backfill_range(
                            db, hours_back=2.0, step_seconds=30,
                        )
                        await db.commit()
                        backfilled = True
                        if bf_count > 0:
                            logger.info(
                                f"Initial backfill: {bf_count} data points "
                                f"from 2h of Prometheus history"
                            )
                    else:
                        count, service_names = await poller.poll_once(db)
                        await db.commit()
                        if count > 0:
                            logger.info(
                                f"Prometheus poll: ingested {count} data points"
                            )
                finally:
                    await poller.close()

                # ── Auto-register discovered services ────────────────
                if service_names and config.github_repo:
                    repo_service = config.github_repo.split("/")[-1]
                    await _auto_register_services(
                        db, service_names, repo_service, config.github_repo,
                    )
                    await db.commit()

                # ── Periodically sync GitHub commits ─────────────────
                cycle_count += 1
                if cycle_count % _GITHUB_SYNC_EVERY_N_CYCLES == 0 and config.github_repo:
                    await _auto_sync_github(db, config)
                    await db.commit()
                # ── Periodically auto-detect anomalies ─────────────────
                if cycle_count % _AUTO_DETECT_EVERY_N_CYCLES == 0:
                    await _auto_detect_anomalies(db)
                    await db.commit()
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
