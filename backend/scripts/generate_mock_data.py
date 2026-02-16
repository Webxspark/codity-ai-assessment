"""Generate realistic mock data for the anomaly detection system.

Creates:
- 3 services with metadata
- 24 hours of metric data at 1-minute granularity
- Planted anomalies (spikes, sustained deviations, drops)
- Correlated deployments and config changes
"""

import asyncio
import uuid
import math
import random
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.config import get_settings
from app.models.db_models import (
    MetricDataPoint,
    ServiceRegistry,
    DeploymentLog,
    ConfigChangeLog,
)
from app.database import Base

# ── Time range ───────────────────────────────────────────────────────
# Use a fixed reference date so demo is reproducible
BASE_DATE = datetime(2026, 2, 15, 0, 0, 0)  # midnight
DURATION_HOURS = 24
INTERVAL_MINUTES = 1
TOTAL_POINTS = DURATION_HOURS * 60 // INTERVAL_MINUTES  # 1440


def generate_timestamps():
    return [BASE_DATE + timedelta(minutes=i) for i in range(TOTAL_POINTS)]


def add_noise(values, sigma=1.0):
    return [v + random.gauss(0, sigma) for v in values]


def sinusoidal_pattern(n, amplitude=50, period=1440, offset=100, phase=0):
    """Generate a daily sinusoidal pattern."""
    return [
        offset + amplitude * math.sin(2 * math.pi * (i + phase) / period)
        for i in range(n)
    ]


# ── Metric generators ───────────────────────────────────────────────

def gen_payment_latency(timestamps):
    """Payment service p95 latency. Spike at 14:32 (index 872)."""
    values = sinusoidal_pattern(len(timestamps), amplitude=20, offset=120, phase=200)
    values = add_noise(values, sigma=8)

    # Inject spike at 14:28-14:45 (indices 868-885)
    for i in range(868, 886):
        if i < len(values):
            spike_intensity = math.exp(-0.3 * abs(i - 872))  # peak at 872
            values[i] += 700 * spike_intensity

    return values


def gen_payment_error_rate(timestamps):
    """Payment service error rate. Sustained deviation starting 14:33."""
    values = sinusoidal_pattern(len(timestamps), amplitude=0.02, offset=0.08, phase=200)
    values = add_noise(values, sigma=0.01)

    # Sustained elevation from 14:33 to 15:10 (indices 873-910)
    for i in range(873, 911):
        if i < len(values):
            values[i] += 1.5 + 0.5 * random.random()  # jump to ~2%

    # Clamp to non-negative
    values = [max(0, v) for v in values]
    return values


def gen_payment_request_count(timestamps):
    """Payment service request count — normal daily pattern."""
    values = sinusoidal_pattern(len(timestamps), amplitude=200, offset=500, phase=300)
    values = add_noise(values, sigma=25)
    values = [max(0, v) for v in values]
    return values


def gen_user_service_latency(timestamps):
    """User service p95 latency. Small bump correlating with payment spike."""
    values = sinusoidal_pattern(len(timestamps), amplitude=10, offset=45, phase=150)
    values = add_noise(values, sigma=4)

    # Small bump at 14:30-14:40
    for i in range(870, 880):
        if i < len(values):
            values[i] += 30 + 10 * random.random()

    return values


def gen_user_service_cpu(timestamps):
    """User service CPU %. Pattern break — loses cycle after 09:00."""
    values = sinusoidal_pattern(len(timestamps), amplitude=15, offset=40, phase=100)
    values = add_noise(values, sigma=3)

    # After index 540 (09:00), flatten + add random noise
    for i in range(540, len(values)):
        values[i] = 55 + random.gauss(0, 8)

    values = [max(0, min(100, v)) for v in values]
    return values


def gen_api_gateway_latency(timestamps):
    """API Gateway latency — generally stable, small drop at 03:00."""
    values = sinusoidal_pattern(len(timestamps), amplitude=5, offset=30, phase=50)
    values = add_noise(values, sigma=2)

    # Drop at 03:00-03:20 (indices 180-200)
    for i in range(180, 200):
        if i < len(values):
            values[i] -= 15 + 5 * random.random()

    values = [max(1, v) for v in values]
    return values


def gen_api_gateway_queue_depth(timestamps):
    """API Gateway queue depth — normal pattern."""
    values = sinusoidal_pattern(len(timestamps), amplitude=8, offset=12, phase=250)
    values = add_noise(values, sigma=2)
    values = [max(0, v) for v in values]
    return values


# ── Service registry data ────────────────────────────────────────────

SERVICES = [
    {
        "service_name": "payment-service",
        "description": "Handles checkout, payment processing, and refunds. Core revenue-critical service.",
        "owner_team": "payments-team",
        "repository_url": "https://github.com/acme-corp/payment-service",
        "metrics": ["latency_p95", "error_rate", "request_count"],
        "dependencies": ["user-service", "api-gateway"],
        "modules": ["checkout", "refund", "webhook", "payment-processor"],
    },
    {
        "service_name": "user-service",
        "description": "User authentication, profiles, and preferences management.",
        "owner_team": "platform-team",
        "repository_url": "https://github.com/acme-corp/user-service",
        "metrics": ["latency_p95", "cpu_percent"],
        "dependencies": ["api-gateway"],
        "modules": ["auth", "profile", "preferences", "session"],
    },
    {
        "service_name": "api-gateway",
        "description": "Edge gateway handling routing, rate limiting, and request aggregation.",
        "owner_team": "infra-team",
        "repository_url": "https://github.com/acme-corp/api-gateway",
        "metrics": ["latency_p95", "queue_depth"],
        "dependencies": [],
        "modules": ["router", "rate-limiter", "circuit-breaker", "load-balancer"],
    },
]


# ── Deployments ──────────────────────────────────────────────────────

DEPLOYMENTS = [
    {
        "service_name": "payment-service",
        "timestamp": BASE_DATE + timedelta(hours=14, minutes=28),
        "commit_sha": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
        "commit_message": "Add user preferences JOIN to checkout query for personalized pricing",
        "author": "alice@acme.com",
        "changed_files": [
            "src/checkout/db_query.py",
            "src/checkout/handler.py",
            "tests/test_checkout.py",
        ],
        "pr_url": "https://github.com/acme-corp/payment-service/pull/342",
    },
    {
        "service_name": "payment-service",
        "timestamp": BASE_DATE + timedelta(hours=10, minutes=15),
        "commit_sha": "b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1",
        "commit_message": "Update payment processor SDK to v3.2.1",
        "author": "bob@acme.com",
        "changed_files": [
            "requirements.txt",
            "src/payment-processor/client.py",
        ],
        "pr_url": "https://github.com/acme-corp/payment-service/pull/340",
    },
    {
        "service_name": "user-service",
        "timestamp": BASE_DATE + timedelta(hours=8, minutes=45),
        "commit_sha": "c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2",
        "commit_message": "Refactor session management to use Redis cluster",
        "author": "charlie@acme.com",
        "changed_files": [
            "src/session/store.py",
            "src/session/middleware.py",
            "config/redis.yaml",
        ],
        "pr_url": "https://github.com/acme-corp/user-service/pull/218",
    },
    {
        "service_name": "api-gateway",
        "timestamp": BASE_DATE + timedelta(hours=2, minutes=30),
        "commit_sha": "d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3",
        "commit_message": "Adjust rate limiter thresholds for overnight batch jobs",
        "author": "devops-bot",
        "changed_files": [
            "config/rate-limits.yaml",
            "src/rate-limiter/config.py",
        ],
        "pr_url": None,
    },
]


# ── Config changes ───────────────────────────────────────────────────

CONFIG_CHANGES = [
    {
        "service_name": "user-service",
        "timestamp": BASE_DATE + timedelta(hours=8, minutes=55),
        "parameter": "redis.pool_size",
        "old_value": "20",
        "new_value": "10",
        "changed_by": "charlie@acme.com",
    },
    {
        "service_name": "payment-service",
        "timestamp": BASE_DATE + timedelta(hours=14, minutes=25),
        "parameter": "db.query_timeout_ms",
        "old_value": "5000",
        "new_value": "3000",
        "changed_by": "alice@acme.com",
    },
    {
        "service_name": "api-gateway",
        "timestamp": BASE_DATE + timedelta(hours=2, minutes=28),
        "parameter": "rate_limit.requests_per_second",
        "old_value": "1000",
        "new_value": "500",
        "changed_by": "devops-bot",
    },
]


# ── Metric definitions ──────────────────────────────────────────────

METRIC_GENERATORS = [
    ("payment-service", "latency_p95", gen_payment_latency),
    ("payment-service", "error_rate", gen_payment_error_rate),
    ("payment-service", "request_count", gen_payment_request_count),
    ("user-service", "latency_p95", gen_user_service_latency),
    ("user-service", "cpu_percent", gen_user_service_cpu),
    ("api-gateway", "latency_p95", gen_api_gateway_latency),
    ("api-gateway", "queue_depth", gen_api_gateway_queue_depth),
]


async def seed_database():
    """Seed the database with all mock data."""
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        # 1. Seed service registry
        print("Seeding service registry...")
        for svc_data in SERVICES:
            svc = ServiceRegistry(**svc_data)
            session.add(svc)
        await session.flush()

        # 2. Seed deployments
        print("Seeding deployment logs...")
        for dep_data in DEPLOYMENTS:
            dep = DeploymentLog(**dep_data)
            session.add(dep)
        await session.flush()

        # 3. Seed config changes
        print("Seeding config change logs...")
        for cfg_data in CONFIG_CHANGES:
            cfg = ConfigChangeLog(**cfg_data)
            session.add(cfg)
        await session.flush()

        # 4. Generate and seed metrics
        timestamps = generate_timestamps()
        print(f"Generating {len(METRIC_GENERATORS)} metric series × {TOTAL_POINTS} points...")

        random.seed(42)  # reproducible

        for service_name, metric_name, generator in METRIC_GENERATORS:
            values = generator(timestamps)
            batch = []
            for ts, val in zip(timestamps, values):
                batch.append(MetricDataPoint(
                    service_name=service_name,
                    metric_name=metric_name,
                    value=round(val, 4),
                    timestamp=ts,
                ))
            session.add_all(batch)
            print(f"  ✓ {service_name}/{metric_name}: {len(batch)} points")
            await session.flush()

        await session.commit()

    await engine.dispose()
    print(f"\n✅ Seeded {len(SERVICES)} services, {len(DEPLOYMENTS)} deployments, "
          f"{len(CONFIG_CHANGES)} config changes, "
          f"{len(METRIC_GENERATORS) * TOTAL_POINTS} metric data points.")


if __name__ == "__main__":
    asyncio.run(seed_database())
