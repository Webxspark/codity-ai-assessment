# Simulation Environment

Self-contained Docker Compose setup that spins up **4 simulated microservices**, a **Prometheus** instance, and a **traffic generator** that creates realistic traffic patterns including periodic anomalies.

## Architecture

```
┌─────────────────────┐     ┌──────────────────┐
│  Traffic Generator   │────▶│  api-gateway     │:8081
│  (Python script)     │────▶│  payment-service │:8082
│  sends traffic +     │────▶│  user-service    │:8083
│  injects anomalies   │────▶│  order-service   │:8084
└─────────────────────┘     └────────┬─────────┘
                                     │ /metrics
                            ┌────────▼─────────┐
                            │    Prometheus     │:9999
                            │  scrapes every    │
                            │    10 seconds     │
                            └──────────────────┘
```

## Quick Start

```bash
cd simulation
docker compose up -d
```

## Connect to CodityAI

1. Open the CodityAI Settings page (`/settings`)
2. Set **Prometheus Endpoint** to:
   - If CodityAI runs in Docker: `http://host.docker.internal:9999`
   - If CodityAI runs on host: `http://localhost:9999`
3. Add PromQL queries:

| Query | Service | Metric Name |
|-------|---------|-------------|
| `rate(http_requests_total[5m])` | (auto from labels) | `request_rate` |
| `histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))` | (auto) | `latency_p95` |
| `error_rate_percent` | (auto) | `error_rate` |
| `cpu_usage_percent` | (auto) | `cpu_usage` |
| `memory_usage_mb` | (auto) | `memory_usage` |

4. Click **Test Connection** → **Start Polling**

## Exposed Metrics

Each simulated service exposes:

| Metric | Type | Description |
|--------|------|-------------|
| `http_requests_total` | Counter | Total requests (labels: service, method, endpoint, status) |
| `http_request_duration_seconds` | Histogram | Request latency with standard buckets |
| `error_rate_percent` | Gauge | Current error rate % |
| `cpu_usage_percent` | Gauge | Simulated CPU usage |
| `memory_usage_mb` | Gauge | Simulated memory usage |
| `active_connections` | Gauge | Simulated active connections |

## Anomaly Scenarios

The traffic generator randomly injects these anomalies every ~2 minutes:

1. **Latency Spike** — p95 jumps to 800ms+ (simulates DB lock contention)
2. **Error Burst** — 35% error rate (simulates bad deploy)
3. **CPU Surge** — CPU hits 88%+ (simulates runaway process)
4. **Memory Leak** — Memory climbs to 920MB+ (simulates leak)
5. **Cascading Failure** — High latency + 50% errors (simulates cascade)
6. **Gradual Degradation** — Moderate latency + slight error uptick

Each anomaly lasts ~60 seconds, then auto-resets to baseline.

## Manual Anomaly Injection

You can inject anomalies manually via the service API:

```bash
# Inject latency spike on api-gateway for 30 seconds
curl -X POST "http://localhost:8081/inject-anomaly?latency_spike_ms=1000&duration_seconds=30"

# Inject error burst on payment-service
curl -X POST "http://localhost:8082/inject-anomaly?error_probability=0.5&duration_seconds=45"
```

## Ports

| Service | Host Port |
|---------|-----------|
| api-gateway | 8081 |
| payment-service | 8082 |
| user-service | 8083 |
| order-service | 8084 |
| Prometheus UI | 9999 |

## Teardown

```bash
docker compose down -v
```
