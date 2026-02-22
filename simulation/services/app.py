"""Simulated microservice that exposes Prometheus metrics.

Each instance acts as a different service (api-gateway, payment-service,
user-service) by setting the SERVICE_NAME env var. Exposes:
- http_requests_total (counter) — with method, endpoint, status labels
- http_request_duration_seconds (histogram) — request latency
- error_rate (gauge) — current error rate percentage
- cpu_usage_percent (gauge) — simulated CPU usage
- memory_usage_mb (gauge) — simulated memory usage
- active_connections (gauge) — simulated active connections

The /simulate endpoint lets the traffic generator trigger realistic
request processing with controllable latency and error injection.
"""

import os
import time
import random
import asyncio

from fastapi import FastAPI, Query
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    REGISTRY,
)
from starlette.responses import Response

SERVICE_NAME = os.getenv("SERVICE_NAME", "unknown-service")
PORT = int(os.getenv("PORT", "8080"))

app = FastAPI(title=f"{SERVICE_NAME} (simulated)")

# ── Prometheus metrics ───────────────────────────────────────────────

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "method", "endpoint", "status"],
)

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["service", "method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

ERROR_RATE = Gauge(
    "error_rate_percent",
    "Current error rate percentage",
    ["service"],
)

CPU_USAGE = Gauge(
    "cpu_usage_percent",
    "Simulated CPU usage percentage",
    ["service"],
)

MEMORY_USAGE = Gauge(
    "memory_usage_mb",
    "Simulated memory usage in MB",
    ["service"],
)

ACTIVE_CONNECTIONS = Gauge(
    "active_connections",
    "Simulated active connections",
    ["service"],
)

# ── Baseline state ───────────────────────────────────────────────────

state = {
    "base_latency": 0.05,       # seconds
    "latency_spike": 0.0,       # additional latency during anomaly
    "error_probability": 0.01,  # 1% baseline error rate
    "cpu_base": 30.0,
    "memory_base": 256.0,
    "connections_base": 50,
}

ENDPOINTS = ["/api/v1/users", "/api/v1/orders", "/api/v1/payments", "/api/v1/health", "/api/v1/search"]
METHODS = ["GET", "POST", "PUT", "DELETE"]


# ── Endpoints ────────────────────────────────────────────────────────

@app.get("/metrics")
async def metrics():
    """Prometheus scrape endpoint."""
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
async def health():
    return {"service": SERVICE_NAME, "status": "ok"}


@app.post("/simulate")
async def simulate_request(
    count: int = Query(default=1, le=100, description="Number of requests to simulate"),
    latency_spike_ms: float = Query(default=0, description="Extra latency to inject in ms"),
    error_rate_pct: float = Query(default=-1, description="Override error rate (0-100). -1 = use default"),
    cpu_spike: float = Query(default=0, description="Extra CPU usage to add"),
    memory_spike: float = Query(default=0, description="Extra memory in MB to add"),
):
    """Simulate request processing — called by the traffic generator."""
    results = {"processed": 0, "errors": 0, "avg_latency_ms": 0}

    # Apply temporary overrides
    extra_latency = latency_spike_ms / 1000.0
    err_prob = error_rate_pct / 100.0 if error_rate_pct >= 0 else state["error_probability"]

    total_latency = 0.0
    for _ in range(count):
        endpoint = random.choice(ENDPOINTS)
        method = random.choice(METHODS)

        # Simulate latency
        base = state["base_latency"] + state["latency_spike"]
        jitter = random.uniform(-0.3, 0.3) * base
        latency = max(0.001, base + jitter + extra_latency)

        # Simulate occasionally
        await asyncio.sleep(min(latency, 0.5))  # cap actual sleep to 500ms

        # Determine success/failure
        is_error = random.random() < err_prob
        status = random.choice(["500", "502", "503"]) if is_error else "200"

        # Record metrics
        REQUEST_COUNT.labels(
            service=SERVICE_NAME, method=method, endpoint=endpoint, status=status
        ).inc()

        REQUEST_DURATION.labels(
            service=SERVICE_NAME, method=method, endpoint=endpoint
        ).observe(latency)

        total_latency += latency
        results["processed"] += 1
        if is_error:
            results["errors"] += 1

    # Update gauges
    actual_err_rate = (results["errors"] / max(results["processed"], 1)) * 100
    ERROR_RATE.labels(service=SERVICE_NAME).set(round(actual_err_rate, 2))

    cpu = state["cpu_base"] + cpu_spike + random.uniform(-5, 5)
    CPU_USAGE.labels(service=SERVICE_NAME).set(round(max(0, min(100, cpu)), 1))

    mem = state["memory_base"] + memory_spike + random.uniform(-20, 20)
    MEMORY_USAGE.labels(service=SERVICE_NAME).set(round(max(0, mem), 1))

    conns = state["connections_base"] + random.randint(-10, 10)
    ACTIVE_CONNECTIONS.labels(service=SERVICE_NAME).set(max(0, conns))

    results["avg_latency_ms"] = round((total_latency / max(results["processed"], 1)) * 1000, 2)
    return results


@app.post("/inject-anomaly")
async def inject_anomaly(
    latency_spike_ms: float = Query(default=0, description="Persistent latency spike in ms"),
    error_probability: float = Query(default=-1, description="Override error rate (0-1). -1 = reset"),
    cpu_base: float = Query(default=-1, description="Override CPU base. -1 = no change"),
    memory_base: float = Query(default=-1, description="Override memory base. -1 = no change"),
    duration_seconds: float = Query(default=0, description="Auto-reset after N seconds. 0 = permanent"),
):
    """Inject a persistent anomaly into this service. Used by the traffic simulator."""
    if latency_spike_ms > 0:
        state["latency_spike"] = latency_spike_ms / 1000.0
    if error_probability >= 0:
        state["error_probability"] = error_probability
    if cpu_base >= 0:
        state["cpu_base"] = cpu_base
    if memory_base >= 0:
        state["memory_base"] = memory_base

    # Auto-reset after duration
    if duration_seconds > 0:
        asyncio.create_task(_reset_after(duration_seconds))

    return {
        "service": SERVICE_NAME,
        "state": state,
        "auto_reset_seconds": duration_seconds or "permanent",
    }


async def _reset_after(seconds: float):
    await asyncio.sleep(seconds)
    state["latency_spike"] = 0.0
    state["error_probability"] = 0.01
    state["cpu_base"] = 30.0
    state["memory_base"] = 256.0


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
