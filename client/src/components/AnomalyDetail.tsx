/**
 * Anomaly detail panel — shows explanation, code context, correlations, and metrics.
 */
import { useState } from "react";
import { Card, Chip, Button } from "@heroui/react";
import { format } from "date-fns";
import {
  GitCommit,
  Settings,
  AlertTriangle,
  MessageSquare,
  ExternalLink,
  FileCode,
  ArrowLeftRight,
} from "lucide-react";
import type { Anomaly, DeploymentLog, ConfigChangeLog } from "../types";
import { SeverityBadge, AnomalyTypeBadge } from "./SeverityBadge";
import { DeploymentComparisonChart } from "./DeploymentComparisonChart";

interface AnomalyDetailProps {
  anomaly: Anomaly;
  deployments: DeploymentLog[];
  configChanges: ConfigChangeLog[];
  onOpenChat: (anomalyId: string) => void;
}

export function AnomalyDetail({
  anomaly,
  deployments,
  configChanges,
  onOpenChat,
}: AnomalyDetailProps) {
  const [expandedDeployId, setExpandedDeployId] = useState<string | null>(null);

  // Filter deployments/config changes relevant to this anomaly's time window
  const anomalyTime = new Date(anomaly.detected_at).getTime();
  const windowMs = 60 * 60 * 1000; // 1 hour

  const relevantDeploys = deployments.filter((d) => {
    const t = new Date(d.timestamp).getTime();
    return (
      t >= anomalyTime - windowMs &&
      t <= anomalyTime &&
      (d.service_name === anomaly.service_name || true)
    );
  });

  const relevantConfigs = configChanges.filter((c) => {
    const t = new Date(c.timestamp).getTime();
    return t >= anomalyTime - windowMs && t <= anomalyTime;
  });

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-bold text-foreground">
            {anomaly.service_name} / {anomaly.metric_name}
          </h2>
          <p className="text-sm text-muted mt-0.5">
            Detected at {format(new Date(anomaly.detected_at), "yyyy-MM-dd HH:mm:ss")}
          </p>
        </div>
        <Button
          variant="primary"
          size="sm"
          onPress={() => onOpenChat(anomaly.id)}
        >
          <MessageSquare size={14} />
          Ask AI
        </Button>
      </div>

      <div className="flex gap-2 flex-wrap">
        <SeverityBadge severity={anomaly.severity} />
        <AnomalyTypeBadge type={anomaly.anomaly_type} />
        <Chip size="sm" variant="soft" color="accent">
          {(anomaly.confidence_score * 100).toFixed(0)}% confidence
        </Chip>
        {anomaly.z_score && (
          <Chip size="sm" variant="soft">
            Z-Score: {anomaly.z_score.toFixed(1)}σ
          </Chip>
        )}
      </div>

      {/* Explanation */}
      <Card variant="secondary">
        <Card.Header>
          <Card.Title className="text-sm">Why is this anomalous?</Card.Title>
        </Card.Header>
        <Card.Content>
          <p className="text-sm text-foreground leading-relaxed">
            {anomaly.explanation}
          </p>
        </Card.Content>
      </Card>

      {/* Metric values */}
      <Card variant="secondary">
        <Card.Header>
          <Card.Title className="text-sm">Metric Values</Card.Title>
        </Card.Header>
        <Card.Content>
          <div className="grid grid-cols-3 gap-4">
            <div>
              <p className="text-xs text-muted">Observed</p>
              <p className="text-lg font-bold text-danger">
                {anomaly.metric_value.toFixed(2)}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted">Baseline Mean</p>
              <p className="text-lg font-bold text-foreground">
                {anomaly.baseline_mean?.toFixed(2) ?? "N/A"}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted">Baseline Std</p>
              <p className="text-lg font-bold text-foreground">
                ±{anomaly.baseline_std?.toFixed(2) ?? "N/A"}
              </p>
            </div>
          </div>
        </Card.Content>
      </Card>

      {/* Correlated Deployments */}
      {relevantDeploys.length > 0 && (
        <Card variant="secondary">
          <Card.Header>
            <Card.Title className="text-sm flex items-center gap-2">
              <GitCommit size={14} /> Nearby Deployments
            </Card.Title>
          </Card.Header>
          <Card.Content>
            <div className="flex flex-col gap-3">
              {relevantDeploys.map((deploy) => {
                const timeDiffMin = Math.round(
                  (anomalyTime - new Date(deploy.timestamp).getTime()) / 60000
                );
                return (
                  <div
                    key={deploy.id}
                    className="border border-border rounded-lg p-3"
                  >
                    <div className="flex items-center gap-2 flex-wrap">
                      <Chip size="sm" variant="soft" color="warning">
                        {timeDiffMin} min before
                      </Chip>
                      <span className="text-xs text-muted font-mono">
                        {deploy.commit_sha.slice(0, 8)}
                      </span>
                      <span className="text-xs text-muted">
                        by {deploy.author}
                      </span>
                    </div>
                    <p className="text-sm text-foreground mt-1">
                      {deploy.commit_message}
                    </p>
                    {deploy.changed_files && deploy.changed_files.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-2">
                        {deploy.changed_files.map((file) => (
                          <Chip
                            key={file}
                            size="sm"
                            variant="secondary"
                          >
                            <FileCode size={10} />
                            {file}
                          </Chip>
                        ))}
                      </div>
                    )}
                    {deploy.pr_url && (
                      <a
                        href={deploy.pr_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs text-primary flex items-center gap-1 mt-1.5"
                      >
                        View PR <ExternalLink size={10} />
                      </a>
                    )}

                    {/* Before vs After comparison toggle */}
                    <Button
                      size="sm"
                      variant={expandedDeployId === deploy.id ? "primary" : "outline"}
                      className="mt-2"
                      onPress={() =>
                        setExpandedDeployId(
                          expandedDeployId === deploy.id ? null : deploy.id
                        )
                      }
                    >
                      <ArrowLeftRight size={12} />
                      {expandedDeployId === deploy.id
                        ? "Hide Comparison"
                        : "Compare Before / After"}
                    </Button>

                    {expandedDeployId === deploy.id && (
                      <div className="mt-3">
                        <DeploymentComparisonChart deployment={deploy} />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </Card.Content>
        </Card>
      )}

      {/* Config Changes */}
      {relevantConfigs.length > 0 && (
        <Card variant="secondary">
          <Card.Header>
            <Card.Title className="text-sm flex items-center gap-2">
              <Settings size={14} /> Nearby Config Changes
            </Card.Title>
          </Card.Header>
          <Card.Content>
            <div className="flex flex-col gap-3">
              {relevantConfigs.map((change) => {
                const timeDiffMin = Math.round(
                  (anomalyTime - new Date(change.timestamp).getTime()) / 60000
                );
                return (
                  <div
                    key={change.id}
                    className="border border-border rounded-lg p-3"
                  >
                    <div className="flex items-center gap-2 flex-wrap">
                      <Chip size="sm" variant="soft" color="warning">
                        {timeDiffMin} min before
                      </Chip>
                      <span className="text-sm font-medium">
                        {change.parameter}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 mt-1 text-sm">
                      <span className="text-danger line-through">
                        {change.old_value}
                      </span>
                      <span>→</span>
                      <span className="text-success font-medium">
                        {change.new_value}
                      </span>
                    </div>
                    <p className="text-xs text-muted mt-1">
                      Changed by {change.changed_by}
                    </p>
                  </div>
                );
              })}
            </div>
          </Card.Content>
        </Card>
      )}

      {/* Anomaly Correlations from detection */}
      {anomaly.correlations.length > 0 && (
        <Card variant="secondary">
          <Card.Header>
            <Card.Title className="text-sm flex items-center gap-2">
              <AlertTriangle size={14} /> Correlation Analysis
            </Card.Title>
          </Card.Header>
          <Card.Content>
            <div className="flex flex-col gap-2">
              {anomaly.correlations
                .sort((a, b) => (b.suspicion_score || 0) - (a.suspicion_score || 0))
                .map((corr) => (
                  <div
                    key={corr.id}
                    className="border border-border rounded-lg p-3"
                  >
                    <div className="flex items-center gap-2">
                      <Chip
                        size="sm"
                        variant="soft"
                        color={
                          corr.correlation_type === "deployment"
                            ? "accent"
                            : corr.correlation_type === "config_change"
                              ? "warning"
                              : "default"
                        }
                      >
                        {corr.correlation_type.replace("_", " ")}
                      </Chip>
                      {corr.suspicion_score && (
                        <span className="text-xs text-muted">
                          Suspicion: {(corr.suspicion_score * 100).toFixed(0)}%
                        </span>
                      )}
                    </div>
                    {corr.explanation && (
                      <p className="text-sm text-foreground mt-1">
                        {corr.explanation}
                      </p>
                    )}
                  </div>
                ))}
            </div>
          </Card.Content>
        </Card>
      )}
    </div>
  );
}
