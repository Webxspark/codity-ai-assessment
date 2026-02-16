/**
 * TypeScript interfaces mirroring backend Pydantic schemas.
 */

export interface MetricDataPoint {
  id: string;
  service_name: string;
  metric_name: string;
  value: number;
  timestamp: string;
  labels?: Record<string, string>;
}

export interface MetricsSummary {
  service_name: string;
  metric_name: string;
  count: number;
  min_value: number;
  max_value: number;
  avg_value: number;
  latest_timestamp: string;
}

export interface ServiceListResponse {
  services: string[];
  metrics: Record<string, string[]>;
}

export interface AnomalyCorrelation {
  id: string;
  correlation_type: "deployment" | "config_change" | "related_anomaly";
  reference_id: string;
  suspicion_score: number | null;
  explanation: string | null;
}

export interface Anomaly {
  id: string;
  service_name: string;
  metric_name: string;
  detected_at: string;
  severity: "critical" | "warning" | "info";
  confidence_score: number;
  anomaly_type: "spike" | "drop" | "sustained_deviation" | "pattern_change";
  metric_value: number;
  baseline_mean: number | null;
  baseline_std: number | null;
  z_score: number | null;
  explanation: string | null;
  detection_details: Record<string, unknown> | null;
  window_start: string | null;
  window_end: string | null;
  correlations: AnomalyCorrelation[];
}

export interface DetectAnomaliesResponse {
  anomalies_detected: number;
  anomalies: Anomaly[];
}

export interface ServiceRegistry {
  id: string;
  service_name: string;
  description: string | null;
  owner_team: string | null;
  repository_url: string | null;
  metrics: string[] | null;
  dependencies: string[] | null;
  modules: string[] | null;
}

export interface DeploymentLog {
  id: string;
  service_name: string;
  timestamp: string;
  commit_sha: string;
  commit_message: string | null;
  author: string | null;
  changed_files: string[] | null;
  pr_url: string | null;
}

export interface ConfigChangeLog {
  id: string;
  service_name: string;
  timestamp: string;
  parameter: string;
  old_value: string | null;
  new_value: string | null;
  changed_by: string | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  metadata_?: Record<string, unknown>;
  created_at: string;
}

export interface ChatConversation {
  id: string;
  anomaly_id: string | null;
  messages: ChatMessage[];
  created_at: string;
}

export interface ChatStreamChunk {
  type: "chunk" | "done" | "error";
  content?: string;
  conversation_id?: string;
}
