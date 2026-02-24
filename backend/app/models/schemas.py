"""Pydantic schemas for API request/response models."""

from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field


# ── Metrics ──────────────────────────────────────────────────────────

class MetricDataPointIn(BaseModel):
    service_name: str
    metric_name: str
    value: float
    timestamp: datetime
    labels: dict | None = None


class MetricsBulkIngestRequest(BaseModel):
    data_points: list[MetricDataPointIn] = Field(
        ..., max_length=10_000, description="Max 10 000 data points per request"
    )


class MetricDataPointOut(BaseModel):
    id: UUID
    service_name: str
    metric_name: str
    value: float
    timestamp: datetime
    labels: dict | None = None

    model_config = {"from_attributes": True}


class MetricsQueryParams(BaseModel):
    service_name: str | None = None
    metric_name: str | None = None
    from_ts: datetime | None = None
    to_ts: datetime | None = None
    limit: int = Field(default=1000, le=10000)


class MetricsSummary(BaseModel):
    service_name: str
    metric_name: str
    count: int
    min_value: float
    max_value: float
    avg_value: float
    latest_timestamp: datetime


# ── Anomalies ────────────────────────────────────────────────────────

class AnomalyOut(BaseModel):
    id: UUID
    service_name: str
    metric_name: str
    detected_at: datetime
    severity: str
    confidence_score: float
    anomaly_type: str
    metric_value: float
    baseline_mean: float | None = None
    baseline_std: float | None = None
    z_score: float | None = None
    explanation: str | None = None
    detection_details: dict | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    correlations: list["AnomalyCorrelationOut"] = []

    model_config = {"from_attributes": True}


class AnomalyCorrelationOut(BaseModel):
    id: UUID
    correlation_type: str
    reference_id: UUID
    suspicion_score: float | None = None
    explanation: str | None = None

    model_config = {"from_attributes": True}


class DetectAnomaliesRequest(BaseModel):
    service_name: str | None = None
    metric_name: str | None = None
    from_ts: datetime | None = None
    to_ts: datetime | None = None


class DetectAnomaliesResponse(BaseModel):
    anomalies_detected: int
    anomalies: list[AnomalyOut]


# ── Code Context ─────────────────────────────────────────────────────

class ServiceRegistryOut(BaseModel):
    id: UUID
    service_name: str
    description: str | None = None
    owner_team: str | None = None
    repository_url: str | None = None
    metrics: list[str] | None = None
    dependencies: list[str] | None = None
    modules: list[str] | None = None

    model_config = {"from_attributes": True}


class DeploymentLogOut(BaseModel):
    id: UUID
    service_name: str
    timestamp: datetime
    commit_sha: str
    commit_message: str | None = None
    author: str | None = None
    changed_files: list[str] | None = None
    pr_url: str | None = None

    model_config = {"from_attributes": True}


class ConfigChangeLogOut(BaseModel):
    id: UUID
    service_name: str
    timestamp: datetime
    parameter: str
    old_value: str | None = None
    new_value: str | None = None
    changed_by: str | None = None

    model_config = {"from_attributes": True}


# ── Chat ─────────────────────────────────────────────────────────────

class ChatMessageIn(BaseModel):
    message: str
    anomaly_id: UUID | None = None
    conversation_id: UUID | None = None


class ChatMessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    metadata_: dict | None = Field(None, alias="metadata_")
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatConversationOut(BaseModel):
    id: UUID
    anomaly_id: UUID | None = None
    messages: list[ChatMessageOut] = []
    created_at: datetime

    model_config = {"from_attributes": True}


# ── General ──────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


class ServiceListOut(BaseModel):
    services: list[str]
    metrics: dict[str, list[str]]  # service_name -> list of metric names


# ── Deployment Comparison ────────────────────────────────────────────

class MetricWindow(BaseModel):
    start: str
    end: str
    data_points: list[dict]  # [{timestamp, value}, ...]
    stats: dict  # {mean, min, max, std}


class DeploymentComparisonMetric(BaseModel):
    metric_name: str
    before: MetricWindow
    after: MetricWindow
    pct_change: float | None = None  # % change in mean


class DeploymentComparisonOut(BaseModel):
    deployment: DeploymentLogOut
    window_minutes: int
    metrics: list[DeploymentComparisonMetric]


# ── Workspace Config ─────────────────────────────────────────────────

class PrometheusQueryConfig(BaseModel):
    query: str = Field(..., description="PromQL expression")
    service_name: str = Field("unknown", description="Service name to tag ingested points with")
    metric_name: str = Field("", description="Friendly metric name (defaults to __name__ label)")


class WorkspaceConfigIn(BaseModel):
    name: str = "default"
    description: str | None = None
    github_repo: str | None = Field(None, description="owner/repo")
    github_token: str | None = Field(None, description="GitHub PAT")
    github_default_branch: str | None = "main"
    prometheus_endpoint: str | None = Field(None, description="e.g. http://prometheus:9090")
    prometheus_poll_interval_seconds: int = 60
    prometheus_queries: list[PrometheusQueryConfig] | None = None


class WorkspaceConfigOut(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    github_repo: str | None = None
    github_default_branch: str | None = None
    prometheus_endpoint: str | None = None
    prometheus_poll_interval_seconds: int = 60
    prometheus_queries: list[dict] | None = None
    is_polling: str = "false"
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class GitHubSyncResult(BaseModel):
    synced: int
    commits: list[dict]


class ConnectionTestResult(BaseModel):
    status: str
    details: dict = {}
