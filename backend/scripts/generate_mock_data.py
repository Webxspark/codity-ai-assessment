"""Generate realistic mock data for the anomaly detection system.

Creates:
- 3 services with metadata
- 24 hours of metric data at 1-minute granularity
- Planted anomalies (spikes, sustained deviations, drops)
- Correlated deployments and config changes

Each seed produces different data — anomaly positions, noise patterns,
and deployment times are randomized. The base date is set to the
current day so timestamps always look "recent".
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
DURATION_HOURS = 24
INTERVAL_MINUTES = 1
TOTAL_POINTS = DURATION_HOURS * 60 // INTERVAL_MINUTES  # 1440


def _base_date() -> datetime:
    """Use yesterday midnight so the 24-hour window looks recent."""
    now = datetime.utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)


def generate_timestamps(base: datetime):
    return [base + timedelta(minutes=i) for i in range(TOTAL_POINTS)]


def add_noise(values, sigma=1.0):
    return [v + random.gauss(0, sigma) for v in values]


def sinusoidal_pattern(n, amplitude=50.0, period=1440, offset=100.0, phase=0):
    """Generate a daily sinusoidal pattern."""
    return [
        offset + amplitude * math.sin(2 * math.pi * (i + phase) / period)
        for i in range(n)
    ]


def _rand_range(center: int, spread: int = 30, duration: int = 18):
    """Return a randomized (start, end) index near `center` ± spread."""
    start = center + random.randint(-spread, spread)
    start = max(60, min(TOTAL_POINTS - duration - 10, start))
    return start, start + duration + random.randint(-4, 8)


# ── Metric generators ───────────────────────────────────────────────

def gen_payment_latency(timestamps):
    """Payment service p95 latency — spike at a random afternoon index."""
    phase = random.randint(100, 300)
    values = sinusoidal_pattern(len(timestamps), amplitude=random.uniform(15, 30), offset=random.uniform(100, 150), phase=phase)
    values = add_noise(values, sigma=random.uniform(5, 12))

    # Inject spike around mid-afternoon
    peak = random.randint(800, 950)
    start, end = _rand_range(peak, spread=5, duration=14)
    for i in range(start, min(end, len(values))):
        spike_intensity = math.exp(-0.3 * abs(i - peak))
        values[i] += random.uniform(500, 900) * spike_intensity

    return values


def gen_payment_error_rate(timestamps):
    """Payment service error rate — sustained deviation."""
    phase = random.randint(100, 300)
    values = sinusoidal_pattern(len(timestamps), amplitude=random.uniform(0.01, 0.03), offset=random.uniform(0.05, 0.10), phase=phase)
    values = add_noise(values, sigma=random.uniform(0.005, 0.015))

    start = random.randint(830, 930)
    duration = random.randint(25, 50)
    for i in range(start, min(start + duration, len(values))):
        values[i] += random.uniform(1.0, 2.5) + 0.5 * random.random()

    values = [max(0, v) for v in values]
    return values


def gen_payment_request_count(timestamps):
    """Payment service request count — normal daily pattern."""
    phase = random.randint(200, 400)
    values = sinusoidal_pattern(len(timestamps), amplitude=random.uniform(150, 300), offset=random.uniform(400, 600), phase=phase)
    values = add_noise(values, sigma=random.uniform(15, 40))
    values = [max(0, v) for v in values]
    return values


def gen_user_service_latency(timestamps):
    """User service p95 latency — small bump correlated with payment spike."""
    phase = random.randint(100, 250)
    values = sinusoidal_pattern(len(timestamps), amplitude=random.uniform(8, 15), offset=random.uniform(35, 55), phase=phase)
    values = add_noise(values, sigma=random.uniform(2, 6))

    start, end = _rand_range(random.randint(850, 920), spread=10, duration=10)
    for i in range(start, min(end, len(values))):
        values[i] += random.uniform(20, 45) + random.uniform(0, 15)

    return values


def gen_user_service_cpu(timestamps):
    """User service CPU % — pattern break at a random morning index."""
    phase = random.randint(50, 200)
    values = sinusoidal_pattern(len(timestamps), amplitude=random.uniform(10, 20), offset=random.uniform(35, 50), phase=phase)
    values = add_noise(values, sigma=random.uniform(2, 5))

    break_idx = random.randint(420, 660)  # roughly 07:00–11:00
    flat_level = random.uniform(45, 65)
    for i in range(break_idx, len(values)):
        values[i] = flat_level + random.gauss(0, random.uniform(5, 12))

    values = [max(0, min(100, v)) for v in values]
    return values


def gen_api_gateway_latency(timestamps):
    """API Gateway latency — small drop at a random early-morning index."""
    phase = random.randint(20, 100)
    values = sinusoidal_pattern(len(timestamps), amplitude=random.uniform(3, 8), offset=random.uniform(25, 40), phase=phase)
    values = add_noise(values, sigma=random.uniform(1, 3))

    start, end = _rand_range(random.randint(120, 260), spread=15, duration=20)
    for i in range(start, min(end, len(values))):
        values[i] -= random.uniform(10, 25) + random.uniform(0, 8)

    values = [max(1, v) for v in values]
    return values


def gen_api_gateway_queue_depth(timestamps):
    """API Gateway queue depth — normal pattern."""
    phase = random.randint(150, 350)
    values = sinusoidal_pattern(len(timestamps), amplitude=random.uniform(5, 12), offset=random.uniform(8, 18), phase=phase)
    values = add_noise(values, sigma=random.uniform(1, 3))
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


# ── Templates (timestamps filled dynamically) ───────────────────────

_COMMIT_MESSAGES = [
    "Add user preferences JOIN to checkout query for personalized pricing",
    "Update payment processor SDK to v3.2.1",
    "Refactor session management to use Redis cluster",
    "Adjust rate limiter thresholds for overnight batch jobs",
    "Optimise connection pooling for peak traffic",
    "Migrate auth tokens to short-lived JWTs",
    "Fix circuit-breaker false-positive on slow DB reads",
]

_AUTHORS = ["alice@acme.com", "bob@acme.com", "charlie@acme.com", "devops-bot"]


def _random_sha() -> str:
    return uuid.uuid4().hex[:40]


def _build_deployments(base: datetime):
    """Generate 4 deployments with randomized times."""
    offsets = sorted(random.sample(range(60, 23 * 60), 4))  # 4 unique minute-offsets
    templates = [
        {
            "service_name": "payment-service",
            "commit_message": random.choice(_COMMIT_MESSAGES[:2]),
            "author": "alice@acme.com",
            "changed_files": ["src/checkout/db_query.py", "src/checkout/handler.py", "tests/test_checkout.py"],
            "pr_url": f"https://github.com/acme-corp/payment-service/pull/{random.randint(300, 500)}",
        },
        {
            "service_name": "payment-service",
            "commit_message": random.choice(_COMMIT_MESSAGES[1:3]),
            "author": "bob@acme.com",
            "changed_files": ["requirements.txt", "src/payment-processor/client.py"],
            "pr_url": f"https://github.com/acme-corp/payment-service/pull/{random.randint(300, 500)}",
        },
        {
            "service_name": "user-service",
            "commit_message": random.choice(_COMMIT_MESSAGES[2:5]),
            "author": "charlie@acme.com",
            "changed_files": ["src/session/store.py", "src/session/middleware.py", "config/redis.yaml"],
            "pr_url": f"https://github.com/acme-corp/user-service/pull/{random.randint(200, 300)}",
        },
        {
            "service_name": "api-gateway",
            "commit_message": random.choice(_COMMIT_MESSAGES[3:]),
            "author": "devops-bot",
            "changed_files": ["config/rate-limits.yaml", "src/rate-limiter/config.py"],
            "pr_url": None,
        },
    ]
    result = []
    for offset, tpl in zip(offsets, templates):
        result.append({
            **tpl,
            "timestamp": base + timedelta(minutes=offset),
            "commit_sha": _random_sha(),
        })
    return result


def _build_config_changes(base: datetime):
    """Generate 3 config changes with randomized times."""
    offsets = sorted(random.sample(range(60, 23 * 60), 3))
    templates = [
        {"service_name": "user-service", "parameter": "redis.pool_size",
         "old_value": str(random.choice([10, 20, 30])), "new_value": str(random.choice([5, 10, 15])),
         "changed_by": "charlie@acme.com"},
        {"service_name": "payment-service", "parameter": "db.query_timeout_ms",
         "old_value": str(random.choice([3000, 5000, 8000])), "new_value": str(random.choice([2000, 3000, 4000])),
         "changed_by": "alice@acme.com"},
        {"service_name": "api-gateway", "parameter": "rate_limit.requests_per_second",
         "old_value": str(random.choice([500, 1000, 2000])), "new_value": str(random.choice([250, 500, 750])),
         "changed_by": "devops-bot"},
    ]
    result = []
    for offset, tpl in zip(offsets, templates):
        result.append({**tpl, "timestamp": base + timedelta(minutes=offset)})
    return result


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
    """Seed the database with randomized mock data.

    No fixed seed — every invocation produces a unique dataset.
    """
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, expire_on_commit=False)

    base = _base_date()
    deployments = _build_deployments(base)
    config_changes = _build_config_changes(base)

    async with async_session() as session:
        # 1. Seed service registry
        print("Seeding service registry...")
        for svc_data in SERVICES:
            svc = ServiceRegistry(**svc_data)
            session.add(svc)
        await session.flush()

        # 2. Seed deployments
        print("Seeding deployment logs...")
        for dep_data in deployments:
            dep = DeploymentLog(**dep_data)
            session.add(dep)
        await session.flush()

        # 3. Seed config changes
        print("Seeding config change logs...")
        for cfg_data in config_changes:
            cfg = ConfigChangeLog(**cfg_data)
            session.add(cfg)
        await session.flush()

        # 4. Generate and seed metrics (no fixed seed — every run is unique)
        timestamps = generate_timestamps(base)
        print(f"Generating {len(METRIC_GENERATORS)} metric series × {TOTAL_POINTS} points...")

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
    print(f"\n✅ Seeded {len(SERVICES)} services, {len(deployments)} deployments, "
          f"{len(config_changes)} config changes, "
          f"{len(METRIC_GENERATORS) * TOTAL_POINTS} metric data points.")


if __name__ == "__main__":
    asyncio.run(seed_database())
