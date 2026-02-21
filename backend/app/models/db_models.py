"""SQLAlchemy ORM models for the database."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    String,
    Float,
    DateTime,
    Text,
    Integer,
    ForeignKey,
    JSON,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class MetricDataPoint(Base):
    """Individual time-series metric data points."""

    __tablename__ = "metric_data_points"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_name = Column(String(255), nullable=False, index=True)
    metric_name = Column(String(255), nullable=False, index=True)
    value = Column(Float, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    labels = Column(JSON, nullable=True)  # optional key-value metadata
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_metric_service_name_ts", "service_name", "metric_name", "timestamp"),
    )


class Anomaly(Base):
    """Detected anomalies."""

    __tablename__ = "anomalies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_name = Column(String(255), nullable=False, index=True)
    metric_name = Column(String(255), nullable=False)
    detected_at = Column(DateTime, nullable=False, index=True)
    severity = Column(String(50), nullable=False)  # critical, warning, info
    confidence_score = Column(Float, nullable=False)
    anomaly_type = Column(String(100), nullable=False)  # spike, drop, sustained_deviation, pattern_change
    metric_value = Column(Float, nullable=False)
    baseline_mean = Column(Float, nullable=True)
    baseline_std = Column(Float, nullable=True)
    z_score = Column(Float, nullable=True)
    explanation = Column(Text, nullable=True)
    detection_details = Column(JSON, nullable=True)  # raw detection info from each method
    window_start = Column(DateTime, nullable=True)
    window_end = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # relationships
    correlations = relationship("AnomalyCorrelation", back_populates="anomaly", cascade="all, delete-orphan")


class ServiceRegistry(Base):
    """Registry of services and their metadata."""

    __tablename__ = "service_registry"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_name = Column(String(255), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    owner_team = Column(String(255), nullable=True)
    repository_url = Column(String(500), nullable=True)
    metrics = Column(JSON, nullable=True)  # list of metric names this service emits
    dependencies = Column(JSON, nullable=True)  # list of service names this depends on
    modules = Column(JSON, nullable=True)  # list of module/component names
    created_at = Column(DateTime, default=datetime.utcnow)


class DeploymentLog(Base):
    """Deployment / commit history."""

    __tablename__ = "deployment_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_name = Column(String(255), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    commit_sha = Column(String(40), nullable=False)
    commit_message = Column(Text, nullable=True)
    author = Column(String(255), nullable=True)
    changed_files = Column(JSON, nullable=True)  # list of file paths
    commit_diff = Column(Text, nullable=True)  # actual code diff (patch)
    pr_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ConfigChangeLog(Base):
    """Configuration change history."""

    __tablename__ = "config_change_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_name = Column(String(255), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    parameter = Column(String(255), nullable=False)
    old_value = Column(String(500), nullable=True)
    new_value = Column(String(500), nullable=True)
    changed_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AnomalyCorrelation(Base):
    """Links anomalies to probable causes (deployments, config changes)."""

    __tablename__ = "anomaly_correlations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    anomaly_id = Column(UUID(as_uuid=True), ForeignKey("anomalies.id"), nullable=False)
    correlation_type = Column(String(50), nullable=False)  # deployment, config_change, related_anomaly
    reference_id = Column(UUID(as_uuid=True), nullable=False)  # ID of the related entity
    suspicion_score = Column(Float, nullable=True)  # 0-1 how likely this caused the anomaly
    explanation = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    anomaly = relationship("Anomaly", back_populates="correlations")


class ChatConversation(Base):
    """Chat conversation sessions."""

    __tablename__ = "chat_conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    anomaly_id = Column(UUID(as_uuid=True), nullable=True)  # optional: conversation tied to an anomaly
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages = relationship("ChatMessage", back_populates="conversation", cascade="all, delete-orphan",
                            order_by="ChatMessage.created_at")


class ChatMessage(Base):
    """Individual chat messages."""

    __tablename__ = "chat_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("chat_conversations.id"), nullable=False)
    role = Column(String(20), nullable=False)  # user, assistant, system
    content = Column(Text, nullable=False)
    metadata_ = Column("metadata", JSON, nullable=True)  # references to anomalies, metrics, etc.
    created_at = Column(DateTime, default=datetime.utcnow)

    conversation = relationship("ChatConversation", back_populates="messages")


class WorkspaceConfig(Base):
    """Runtime workspace configuration — GitHub repo, Prometheus endpoint, etc.

    Single-row table: only one workspace config at a time.
    Use upsert pattern to update.
    """

    __tablename__ = "workspace_config"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, default="default")
    description = Column(Text, nullable=True)

    # GitHub
    github_repo = Column(String(500), nullable=True)  # "owner/repo"
    github_token = Column(Text, nullable=True)  # PAT or OAuth token
    github_default_branch = Column(String(100), nullable=True, default="main")

    # Prometheus
    prometheus_endpoint = Column(String(500), nullable=True)  # e.g. "http://prometheus:9090"
    prometheus_poll_interval_seconds = Column(Integer, nullable=False, default=60)
    prometheus_queries = Column(JSON, nullable=True)  # list of {query, service_name, metric_name}

    # Polling state
    is_polling = Column(String(10), nullable=False, default="false")  # "true" / "false"

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
