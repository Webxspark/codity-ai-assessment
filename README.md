# CodityAI — Metrics Anomaly Detection & Code Insight System

An AI-assisted platform that detects anomalies in application metrics, correlates them with code changes (deployments, config updates), and provides natural-language explanations through an interactive chat interface.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Frontend (React 19 + HeroUI v3 + Recharts)                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────────┐ │
│  │ Service  │ │ Metric   │ │ Anomaly  │ │ AI Chat Panel      │ │
│  │ Overview │ │ Charts   │ │ Detail   │ │ (SSE Streaming)    │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────────────────┘ │
└────────────────────────────┬────────────────────────────────────┘
                             │ REST + SSE
┌────────────────────────────▼────────────────────────────────────┐
│  Backend (FastAPI + SQLAlchemy Async)                           │
│  ┌────────────────┐ ┌────────────────┐ ┌─────────────────────┐ │
│  │ Anomaly        │ │ Code Context   │ │ AI Chat Service     │ │
│  │ Detector       │ │ Correlator     │ │ (LiteLLM / OpenAI)  │ │
│  │ Z+EWMA+IQR    │ │ Deploy+Config  │ │                     │ │
│  └────────────────┘ └────────────────┘ └─────────────────────┘ │
└────────────────────────────┬────────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │  PostgreSQL 16  │
                    └─────────────────┘
```

## Anomaly Detection Algorithm

### Hybrid Ensemble Approach

The system uses a **weighted ensemble** of three complementary statistical methods:

| Method | Weight | Strength |
|--------|--------|----------|
| **Z-Score** (rolling window) | 40% | Catches sudden spikes/drops against recent baseline |
| **EWMA + Bollinger Bands** | 35% | Detects sustained deviations and trend shifts |
| **IQR (Interquartile Range)** | 25% | Robust to outliers, catches distribution shifts |

**Why this combination?**

- **Z-Score alone** is too sensitive to non-Gaussian distributions and misses gradual drifts.
- **EWMA alone** has high latency for sudden spikes (the exponential smoothing absorbs them).
- **IQR alone** is too coarse for nuanced deviation patterns.

Together, they cover the three most common anomaly types in production systems: **sudden spikes**, **sustained deviations**, and **distribution shifts**.

### Severity Classification

| Severity | Criteria |
|----------|----------|
| **Critical** | Confidence ≥ 75% AND (Z-Score > 6σ OR sustained deviation ≥ 8 consecutive points) |
| **Warning** | Confidence ≥ 55% AND at least 2 detection methods triggered |
| **Info** | Confidence ≥ 45%, single method triggered |

### Tuned Thresholds

```
Z_SCORE_THRESHOLD   = 3.5σ   (rolling window = 30 points)
EWMA_K              = 3.0    (Bollinger band width multiplier)
EWMA_SUSTAINED      = 4      (consecutive points outside band)
IQR_MULTIPLIER      = 2.0    (fence distance from Q1/Q3)
MIN_CONFIDENCE      = 0.45   (minimum composite score to flag)
```

These were tuned empirically to produce ~35 anomalies from 10,080 data points (7 metrics × 1,440 minutes), with a 5 critical / 30 non-critical split — matching the planted anomaly patterns in mock data.

---

## Metric-to-Code Linking

The **Code Context Correlator** connects anomalies to probable root causes:

### Deployment Correlation

For each anomaly, the system searches for deployments within a configurable time window (default: ±2 hours) on the same service. It computes a **relevance score** based on:

- **Temporal proximity** — deployments closer in time to the anomaly score higher (exponential decay)
- **Service match** — same-service deployments get a 2× multiplier
- **Change size** — larger changes (by commit count or files changed) get a slight boost

### Before vs After Deployment Comparison

The system provides a dedicated **deployment impact comparison** feature:

- For any deployment, you can view all service metrics in a configurable window (±30m / ±60m / ±120m) around the deploy timestamp
- Side-by-side statistics: mean, std, min, max before and after
- Percentage change highlighting (e.g., +1850% error rate increase)
- Dual-colour overlay chart with a vertical deployment marker
- Available from both the Anomaly Detail panel and the Deployment Timeline

### Config Change Correlation

Similar temporal search for configuration changes. The system highlights when config changes (e.g., connection pool size, rate limits) align with metric anomalies.

### Anomaly Cross-Correlation

The system identifies related anomalies across services that occur within a short time window, suggesting cascading failures or shared root causes.

---

## AI Reasoning (Chat)

The chat interface uses an LLM (via LiteLLM proxy, OpenAI-compatible API) to provide:

1. **Root Cause Analysis** — "Why did payment-service latency spike at 14:28?"
2. **Impact Assessment** — "What's the downstream impact of this error rate increase?"
3. **Remediation Suggestions** — "How should we address this anomaly?"

### Context Assembly

When the user asks about an anomaly, the system automatically assembles a rich context package:

```
{anomaly_details} + {metric_window} + {correlated_deployments} + {config_changes} + {related_anomalies}
```

This context is injected into the system prompt so the LLM can reason about the specific situation, not just provide generic advice.

### Streaming

Responses are streamed via **Server-Sent Events (SSE)** for real-time display, with token-by-token rendering in the chat UI.

---

## Sample Scenario

### The Story in the Mock Data

| Time | Event | What Happened |
|------|-------|---------------|
| 03:00 | 🔧 Config change | API gateway rate limit changed from 1000 → 500 req/s |
| 03:00 | 📉 Anomaly | API gateway request count drops (queue_depth anomaly) |
| 09:00 | 🚀 Deployment | user-service v2.3.1 deployed (DB pool optimization) |
| 09:00 | 📈 Anomaly | user-service CPU pattern breaks (new memory profile) |
| 14:15 | 🚀 Deployment | payment-service v1.8.0 (new payment provider integration) |
| 14:28–14:45 | 🔥 Anomaly (Critical) | payment-service latency spikes to 3–5× normal |
| 14:33 | 🔥 Anomaly (Critical) | payment-service error rate sustained deviation |

**What an analyst would discover using CodityAI:**

1. Open the dashboard → see critical anomalies highlighted in red
2. Click on the payment-service latency spike → see it correlated with the v1.8.0 deployment 13 minutes earlier
3. Notice the error rate anomaly started 5 minutes after the latency spike → cascading failure
4. Ask the AI: "What caused the payment latency spike?" → LLM reasons: deployment of new payment provider likely introduced a slow API call path
5. Check the API gateway anomaly at 03:00 → correlated with the rate limit config change

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | React 19, TypeScript 5.9, Vite 8 beta, Tailwind CSS v4, HeroUI v3 beta |
| Charts | Recharts (time-series with anomaly markers) |
| State | TanStack React Query v5 |
| Backend | FastAPI, Python 3.12, Pydantic v2 |
| ORM | SQLAlchemy 2.x (async with asyncpg) |
| Database | PostgreSQL 16 |
| LLM | LiteLLM proxy (OpenAI-compatible API) |
| Infrastructure | Docker Compose |

---

## Quick Start

```bash
# 1. Start all services
docker compose up --build -d

# 2. Seed mock data (run once)
docker compose exec backend python -m scripts.generate_mock_data

# 3. Run anomaly detection
curl -X POST http://localhost:8000/api/anomalies/detect \
  -H 'Content-Type: application/json' \
  -d '{}'

# 4. Open the dashboard
open http://localhost:8000
```

For detailed setup instructions, see [.notes/SETUP.md](.notes/SETUP.md).

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/metrics/ingest` | Ingest metric data points |
| GET | `/api/metrics` | Query metrics with filters |
| GET | `/api/metrics/services` | List services and their metrics |
| GET | `/api/metrics/summary` | Statistical summary per metric |
| POST | `/api/anomalies/detect` | Trigger anomaly detection |
| GET | `/api/anomalies` | List anomalies with filters |
| GET | `/api/anomalies/{id}` | Get anomaly detail + correlations |
| GET | `/api/code-context/services` | List registered services |
| GET | `/api/code-context/deployments` | List deployment logs |
| GET | `/api/code-context/deployments/{id}/comparison` | Before vs after deployment metrics comparison |
| GET | `/api/code-context/config-changes` | List config changes |
| POST | `/api/chat` | AI chat (SSE streaming response) |
| GET | `/api/chat/{conversation_id}` | Get conversation history |
| GET | `/api/health` | Health check |

---

## Project Structure

```
CodityAI/
├── backend/
│   ├── app/
│   │   ├── config.py              # Settings (env vars)
│   │   ├── database.py            # Async SQLAlchemy setup
│   │   ├── main.py                # FastAPI app + CORS + routes
│   │   ├── models/
│   │   │   ├── db_models.py       # 8 SQLAlchemy models
│   │   │   └── schemas.py         # Pydantic request/response schemas
│   │   ├── routers/
│   │   │   ├── metrics.py         # /api/metrics endpoints
│   │   │   ├── anomalies.py       # /api/anomalies endpoints
│   │   │   ├── code_context.py    # /api/code-context endpoints
│   │   │   └── chat.py            # /api/chat SSE streaming
│   │   ├── services/
│   │   │   ├── anomaly_detector.py    # Hybrid Z+EWMA+IQR engine
│   │   │   ├── code_context_service.py # Correlation engine
│   │   │   └── ai_chat_service.py     # LLM chat with context
│   │   └── scripts/
│   │       └── generate_mock_data.py  # Realistic mock data generator
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env
├── client/
│   ├── src/
│   │   ├── api/client.ts          # Axios API client
│   │   ├── types/index.ts         # TypeScript interfaces
│   │   ├── components/
│   │   │   ├── SeverityBadge.tsx   # Severity/type badges
│   │   │   ├── MetricChart.tsx    # Time-series chart
│   │   │   ├── AnomalyList.tsx    # Anomaly list panel
│   │   │   ├── AnomalyDetail.tsx  # Anomaly detail view
│   │   │   ├── ChatPanel.tsx      # AI chat interface
│   │   │   ├── ServiceOverview.tsx # Service summary cards
│   │   │   └── DeploymentTimeline.tsx # Deploy/config timeline
│   │   ├── pages/
│   │   │   └── Dashboard.tsx      # Main dashboard page
│   │   ├── App.tsx
│   │   └── index.css
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── vite.config.ts
│   └── package.json
├── docker-compose.yml
├── .notes/                        # Detailed documentation
└── README.md
```
