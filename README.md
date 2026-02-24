# AI-Assisted Metrics Anomaly Detection & Code Insight System

> An intelligent observability platform that detects anomalies in application metrics using a hybrid statistical engine, correlates them with code changes and deployments, and provides root cause analysis through an AI assistant equipped with autonomous data retrieval capabilities.

**Live Demo:** [codityai-assessment.alanchris.me](https://codityai-assessment.alanchris.me/)

**Demo Recording:** [YouTube](https://youtube.com/soulof8d)
<details>
<summary>Recordings (prev versions)</summary>

*  **Initial Demo** — Mock data with injected anomalies. [[YouTube](https://youtu.be/0j8n9sKzLW9M)]

</details>

---

## Table of Contents

- [My Thought Process](#my-thought-process)
  - [Choosing the Anomaly Detection Approach](#choosing-the-anomaly-detection-approach)
  - [What I Learned and Explored](#what-i-learned-and-explored)
  - [How I Guided the AI Agent](#how-i-guided-the-ai-agent)
- [System Architecture](#system-architecture)
  - [High-Level Architecture](#high-level-architecture)
  - [Data Ingestion Pipeline](#data-ingestion-pipeline)
  - [Request Flow: From Question to Answer](#request-flow-from-question-to-answer)
- [Anomaly Detection Engine](#anomaly-detection-engine)
  - [Hybrid Ensemble](#hybrid-ensemble)
  - [Why Not ML?](#why-not-ml)
- [Context Engineering & AI Capabilities](#context-engineering--ai-capabilities)
  - [Metric-to-Code Linking](#metric-to-code-linking)
  - [Autonomous Tool-Calling Agent](#autonomous-tool-calling-agent)
  - [Tool-Calling Flow](#tool-calling-flow)
  - [Context Assembly](#context-assembly)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
- [API Reference](#api-reference)
- [Project Structure](#project-structure)

---

## My Thought Process

### Choosing the Anomaly Detection Approach

The core challenge was selecting an anomaly detection method for time-series metrics. I evaluated several approaches before arriving at the current design.

**Approaches I considered:**

| Approach | Why I explored it | Why I moved on |
|----------|-------------------|----------------|
| **ML-based** (Isolation Forest, Autoencoders, LSTMs) | High accuracy on complex patterns | Requires training data, cold-start problem, hard to explain detections to users, overkill for this use case |
| **Single statistical method** (just Z-Score) | Simple, fast, well-understood | Too many false positives on non-Gaussian distributions; completely misses gradual drifts |
| **Prophet / Seasonal decomposition** | Handles seasonality and trends | Heavy dependency, slow inference, assumes long historical baselines that may not exist |
| **Hybrid statistical ensemble** | Complementary methods cover each other's blind spots | This is what I chose |

**Why the hybrid ensemble won:** Production metrics exhibit three distinct anomaly patterns — sudden spikes, sustained deviations, and distribution shifts. No single statistical method handles all three well. By combining Z-Score (spikes), EWMA + Bollinger Bands (sustained deviations), and IQR (distribution shifts) with weighted scoring, I get broad coverage with minimal false positives, zero training requirements, and fully explainable detections.

### What I Learned and Explored

- **Statistical anomaly detection** — Studied Z-Score sensitivity to window size, EWMA's exponential decay tradeoffs (span vs. responsiveness), and why IQR outperforms standard deviation on skewed distributions.
- **Context engineering for LLMs** — Learned that simply injecting data into a system prompt isn't enough. The LLM needs structured, temporally-annotated context with relative timestamps ("19 min before anomaly") to reason about causation vs. correlation.
- **LLM tool calling** — Explored how function/tool calling transforms the AI from a passive responder into an autonomous agent that can query the database, search for anomalies, and fetch deployment history on its own — producing grounded answers even without pre-attached context.
- **Streaming architecture** — Implementing SSE with tool-calling required careful handling: streaming content tokens to the user in real-time while accumulating tool-call deltas in the background.

### How I Guided the AI Agent

I used GitHub Copilot (Claude Opus 4.6) as my implementation partner throughout this project. My role was **architectural decision-making, algorithm selection, and quality control**:

- I defined _what_ to build and _why_ — the AI handled the _how_
- I identified root causes when things went wrong (e.g., the context miss was because the system only looked at pre-stored correlations, missing events ingested after detection)
- I pushed for the tool-calling approach when I noticed the AI chat couldn't answer questions about anomalies not in its context
- I validated every implementation against the assessment requirements and real-world production patterns

---

## System Architecture
<img src=".docs/system-architecture.png?v=0.1" alt="System Architecture Diagram" />

### Data Ingestion Pipeline
<img src=".docs/data-ingestion-pipeline.png?v=0.1" alt="Data Ingestion Pipeline Diagram" />

### Request Flow: From Question to Answer
<img src=".docs/request-flow.png?v=0.1" alt="Request Flow Diagram" />

---

## Anomaly Detection Engine

### Hybrid Ensemble

Three complementary statistical methods, each targeting a different anomaly pattern:

<img src=".docs/hybrid-ensemble.png?v=0.1" alt="Hybrid Ensemble Diagram"/>

| Method | What it catches | How it works |
|--------|----------------|--------------|
| **Z-Score** (rolling window) | Sudden spikes and drops | Compares each point to the rolling mean/std over the last 30 points. Flags if \|z\| > 3.5σ |
| **EWMA + Bollinger Bands** | Sustained deviations | Exponentially weighted moving average with bands at ±3.0× the EWMA std. Flags when ≥4 consecutive points breach the band |
| **IQR (Interquartile Range)** | Distribution shifts | Computes Q1/Q3 over a rolling window, flags values outside Q1 − 2.0×IQR or Q3 + 2.0×IQR |

### Why Not ML?

Machine learning models (Isolation Forests, Autoencoders, LSTMs) are powerful, but introduce significant complexity for this use case:

- **Training data dependency** — They need labeled historical data or long warm-up periods. A newly deployed service has neither.
- **Explainability gap** — When an ML model flags an anomaly, it's difficult to explain _why_ in terms an engineer understands. The statistical approach directly outputs Z-scores, baseline means, and which methods agreed — all human-readable.
- **Operational overhead** — ML models require retraining as metric patterns evolve, GPU resources, and monitoring for model drift.
- **Diminishing returns** — For the three common anomaly types in metrics data (spikes, drifts, distribution shifts), the statistical ensemble achieves comparable detection rates without any of the above costs.

---

## Context Engineering & AI Capabilities

### Metric-to-Code Linking

When an anomaly is detected, the system doesn't just flag it — it **automatically correlates it with probable causes**:

1. **Deployment correlation** — Finds deployments within ±2 hours, scored by temporal proximity (exponential decay) and service relationship (direct service + dependencies)
2. **Config change correlation** — Identifies parameter changes (e.g., `rate_limit: 2000 → 500`) near the anomaly window
3. **Cross-service anomaly correlation** — Detects related anomalies in dependent services within ±15 minutes, suggesting cascading failures
4. **Live fallback queries** — If pre-stored correlations are incomplete (e.g., data ingested after detection), the system runs live queries to ensure nothing is missed

### Autonomous Tool-Calling Agent

The AI assistant isn't just a prompt-and-response system. It has **10 tools** it can call autonomously to query the system's database and the connected GitHub repository:

| Tool | Purpose |
|------|---------|
| `search_anomalies` | Find anomalies by service, metric, severity, or time range |
| `get_anomaly_context` | Full context: correlations, deployments, config changes, metric trends |
| `get_recent_deployments` | Deployments with commit SHA, message, author, changed files |
| `get_recent_config_changes` | Configuration parameter changes with old/new values |
| `get_metrics_summary` | System health overview across all services |
| `query_metric_data` | Raw time-series data points for any metric |
| `get_code_diff` | Fetch the actual code diff/patch for a deployment commit |
| `get_file_content` | Read any file from the connected GitHub repository |
| `search_code` | Search for code patterns, functions, or classes across the repo |
| `browse_repository` | Navigate the repo directory tree to discover file paths |

This means users can ask open-ended questions like _"Why did p95 latency spike at 14:32?"_ without manually attaching an anomaly — the AI will autonomously search, fetch context, and produce a grounded analysis.

### Tool-Calling Flow
<img src=".docs/tool-calling-flow.png?v=0.1" alt="Tool Calling Flow Diagram" />

The LLM autonomously decides which tools to call and in what order, using up to **5 tool rounds**. A progress indicator (`🔧 Searching anomalies...`) is streamed to the user between rounds so they always know what's happening.

### Context Assembly

Every context package sent to the LLM includes:

- **Anomaly details** — severity, confidence score, detection method breakdown, Z-score, baseline statistics
- **Nearby deployments** — commit SHA, message, author, changed files, with relative timestamps ("deployed 13 min before anomaly")
- **Nearby config changes** — parameter, old → new value, who changed it
- **Related anomalies** — cross-service anomalies suggesting cascading failures
- **Metric trend** — downsampled time-series (60 points before + 15 after) showing the shape of the deviation

---

## Features

- **Hybrid anomaly detection** — Z-Score + EWMA + IQR ensemble with weighted scoring and multi-tier severity classification
- **Automated root cause correlation** — Links anomalies to deployments, config changes, and cross-service cascading failures
- **AI analysis with tool calling** — LLM autonomously queries the database and GitHub repo (10 tools: anomaly search, code diffs, file browsing, repo navigation) to answer questions grounded in real data
- **Real-time Prometheus integration** — Connect to any Prometheus endpoint, auto-discover services, backfill historical data, auto-detect anomalies on a schedule
- **GitHub integration** — Auto-sync commits as deployments, code diff analysis, repository browsing for root cause investigation
- **Auto-anomaly detection** — Polling loop automatically detects anomalies every 5 cycles and correlates them with code changes
- **Real-time streaming** — SSE token-by-token response streaming with progress indicators during tool execution
- **Deployment comparison** — Before vs. after metric comparison with synchronized mirror tooltips
- **Interactive dashboard** — Service cards, time-series charts with anomaly markers, filterable anomaly list, deployment timeline
- **Conversation persistence** — Chat history saved and retrievable across sessions
- **Production-hardened** — Bounded queries, connection pooling, token budgeting, error boundaries, request cancellation
- **Code-split frontend** — `React.lazy` + `Suspense` + `ErrorBoundary` for fast initial load
- **Single-command deployment** — Multi-stage Docker build serving frontend + backend from one container

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | React 19, TypeScript 5.9, Vite 8, Tailwind CSS v4, HeroUI v3 |
| Charts | Recharts |
| Markdown | Streamdown with Shiki syntax highlighting |
| State | TanStack React Query v5 |
| Backend | FastAPI, Python 3.12, Pydantic v2 |
| ORM | SQLAlchemy 2 (fully async with asyncpg) |
| Database | PostgreSQL 16 |
| AI | LiteLLM proxy → GPT-5.1-codex-mini / ... |
| Infrastructure | Docker Compose, multi-stage build |

---

## Getting Started

### Prerequisites

- Docker & Docker Compose
- An LLM provider (LiteLLM proxy, OpenAI API key, or any OpenAI-compatible endpoint)

### Run

```bash
git clone https://github.com/Webxspark/codity-ai-assessment
cd codity-ai-assessment

# Configure LLM endpoint
cp backend/.env.example backend/.env
# Edit backend/.env with your API key and endpoint

# Start
docker compose up --build -d

# Open
open http://localhost:8000
```

Click **"Mock Data"** in the top bar to generate realistic data with planted anomaly patterns, then click **"Run Detection"** to trigger the detection engine.

### Real Metrics + GitHub

Alternatively, go to **Settings** and configure:
- **GitHub repository** — e.g. `owner/repo` with an optional personal access token
- **Prometheus endpoint** — e.g. `http://prometheus:9999` with PromQL queries

Once configured, click **"Start Polling"** — the system will automatically backfill 2 hours of historical data, register services, sync commits, and periodically detect anomalies.

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/metrics/ingest` | Bulk ingest metric data points |
| `GET` | `/api/metrics` | Query metrics with filters |
| `GET` | `/api/metrics/services` | List services and their metrics |
| `GET` | `/api/metrics/summary` | Aggregated statistics per metric |
| `POST` | `/api/anomalies/detect` | Trigger anomaly detection |
| `GET` | `/api/anomalies` | List anomalies with filters |
| `GET` | `/api/anomalies/{id}` | Anomaly detail with correlations |
| `GET` | `/api/code-context/deployments` | Deployment logs |
| `GET` | `/api/code-context/config-changes` | Config change logs |
| `GET` | `/api/code-context/deployments/{id}/comparison` | Before vs. after deployment metrics |
| `POST` | `/api/chat` | AI chat with SSE streaming + tool calling |
| `GET` | `/api/chat/{id}` | Conversation history |
| `PUT` | `/api/workspace/config` | Configure GitHub repo + Prometheus endpoint |
| `GET` | `/api/workspace/config` | Get current workspace configuration |
| `POST` | `/api/workspace/github/sync` | Sync GitHub commits as deployments |
| `POST` | `/api/workspace/prometheus/start-polling` | Start background Prometheus polling |
| `POST` | `/api/workspace/prometheus/stop-polling` | Stop background Prometheus polling |
| `POST` | `/api/workspace/prometheus/backfill` | Backfill historical metric data |
| `DELETE` | `/api/workspace/data/all` | Clear all ingested data |
| `DELETE` | `/api/workspace/data/metrics` | Clear metric data and anomalies |

---

## Project Structure

```
CodityAI/
├── backend/
│   └── app/
│       ├── main.py                          # FastAPI app, CORS, static file serving
│       ├── config.py                        # Environment-based settings
│       ├── database.py                      # Async SQLAlchemy engine + session
│       ├── models/
│       │   ├── db_models.py                 # 9 ORM models (8 tables + helper)
│       │   └── schemas.py                   # Pydantic request/response schemas
│       ├── routers/
│       │   ├── metrics.py                   # Ingest, query, summary
│       │   ├── anomalies.py                 # Detection, listing, detail
│       │   ├── code_context.py              # Services, deployments, config, comparison
│       │   ├── chat.py                      # SSE streaming chat
│       │   ├── workspace.py                 # Workspace config, GitHub/Prometheus integration
│       │   └── seed.py                      # Mock data generation
│       └── services/
│           ├── anomaly_detector.py          # Hybrid Z-Score + EWMA + IQR engine
│           ├── code_context_service.py      # Correlation engine + live fallback
│           ├── ai_chat_service.py           # LLM agent with 10 autonomous tools
│           ├── github_service.py            # GitHub API client (commits, diffs, files, search)
│           └── prometheus_poller.py         # Prometheus polling, backfill, auto-detection
├── client/
│   └── src/
│       ├── api/client.ts                    # Typed API client (Axios + SSE)
│       ├── types/index.ts                   # TypeScript interfaces
│       ├── pages/
│       │   ├── Dashboard.tsx                # Main dashboard (code-split)
│       │   └── Settings.tsx                 # Workspace config (GitHub, Prometheus)
│       └── components/
│           ├── MetricChart.tsx               # Time-series with anomaly markers
│           ├── AnomalyList.tsx               # Filterable anomaly list
│           ├── AnomalyDetail.tsx             # Detail view with correlations
│           ├── ChatPanel.tsx                 # AI chat with streaming + history
│           ├── DeploymentTimeline.tsx        # Chronological event timeline
│           ├── DeploymentComparisonChart.tsx # Mirror tooltip comparison
│           ├── ServiceOverview.tsx           # Service health cards
│           ├── MockDataDialog.tsx            # Data seeding UI
│           └── ErrorBoundary.tsx             # Graceful chunk-load failure handling
├── Dockerfile                               # Multi-stage: Node build → Python runtime
├── docker-compose.yml                       # PostgreSQL + backend
└── README.md
```
