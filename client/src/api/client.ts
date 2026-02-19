/**
 * API client for CodityAI backend.
 */
import axios from "axios";
import type {
  MetricDataPoint,
  MetricsSummary,
  ServiceListResponse,
  Anomaly,
  DetectAnomaliesResponse,
  ServiceRegistry,
  DeploymentLog,
  ConfigChangeLog,
  ChatConversation,
  ChatStreamChunk,
  DeploymentComparison,
  SeedStatus,
} from "../types";

const api = axios.create({
  baseURL: "/api",
  headers: { "Content-Type": "application/json" },
});

// ── Metrics ─────────────────────────────────────────────────────────

export async function fetchMetrics(params: {
  service_name?: string;
  metric_name?: string;
  from_ts?: string;
  to_ts?: string;
  limit?: number;
}): Promise<MetricDataPoint[]> {
  const { data } = await api.get<MetricDataPoint[]>("/metrics", { params });
  return data;
}

export async function fetchServicesList(): Promise<ServiceListResponse> {
  const { data } = await api.get<ServiceListResponse>("/metrics/services");
  return data;
}

export async function fetchMetricsSummary(params?: {
  service_name?: string;
  from_ts?: string;
  to_ts?: string;
}): Promise<MetricsSummary[]> {
  const { data } = await api.get<MetricsSummary[]>("/metrics/summary", { params });
  return data;
}

// ── Anomalies ───────────────────────────────────────────────────────

export async function fetchAnomalies(params?: {
  service_name?: string;
  severity?: string;
  from_ts?: string;
  to_ts?: string;
  limit?: number;
}): Promise<Anomaly[]> {
  const { data } = await api.get<Anomaly[]>("/anomalies", { params });
  return data;
}

export async function fetchAnomaly(anomalyId: string): Promise<Anomaly> {
  const { data } = await api.get<Anomaly>(`/anomalies/${anomalyId}`);
  return data;
}

export async function triggerDetection(params?: {
  service_name?: string;
  metric_name?: string;
  from_ts?: string;
  to_ts?: string;
}): Promise<DetectAnomaliesResponse> {
  const { data } = await api.post<DetectAnomaliesResponse>("/anomalies/detect", params || {});
  return data;
}

// ── Code Context ────────────────────────────────────────────────────

export async function fetchRegisteredServices(): Promise<ServiceRegistry[]> {
  const { data } = await api.get<ServiceRegistry[]>("/code-context/services");
  return data;
}

export async function fetchDeployments(params?: {
  service_name?: string;
  from_ts?: string;
  to_ts?: string;
}): Promise<DeploymentLog[]> {
  const { data } = await api.get<DeploymentLog[]>("/code-context/deployments", { params });
  return data;
}

export async function fetchConfigChanges(params?: {
  service_name?: string;
  from_ts?: string;
  to_ts?: string;
}): Promise<ConfigChangeLog[]> {
  const { data } = await api.get<ConfigChangeLog[]>("/code-context/config-changes", { params });
  return data;
}

export async function fetchDeploymentComparison(
  deploymentId: string,
  windowMinutes: number = 60,
): Promise<DeploymentComparison> {
  const { data } = await api.get<DeploymentComparison>(
    `/code-context/deployments/${deploymentId}/comparison`,
    { params: { window_minutes: windowMinutes } },
  );
  return data;
}

// ── Chat ────────────────────────────────────────────────────────────

export async function fetchConversations(): Promise<ChatConversation[]> {
  const { data } = await api.get<ChatConversation[]>("/chat");
  return data;
}

export async function fetchConversation(conversationId: string): Promise<ChatConversation> {
  const { data } = await api.get<ChatConversation>(`/chat/${conversationId}`);
  return data;
}

/**
 * Send a chat message and receive SSE stream.
 * Returns an async generator of ChatStreamChunks.
 * Pass an AbortSignal to cancel the HTTP request and stop streaming.
 */
export async function* sendChatMessage(params: {
  message: string;
  anomaly_id?: string;
  conversation_id?: string;
  signal?: AbortSignal;
}): AsyncGenerator<ChatStreamChunk> {
  const { signal, ...body } = params;
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    throw new Error(`Chat request failed: ${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          const chunk: ChatStreamChunk = JSON.parse(line.slice(6));
          yield chunk;
        } catch {
          // skip malformed chunks
        }
      }
    }
  }
}

// ── Seed / Mock Data ────────────────────────────────────────────────

export async function fetchSeedStatus(): Promise<SeedStatus> {
  const { data } = await api.get<SeedStatus>("/seed/status");
  return data;
}

export async function generateSeedData(): Promise<SeedStatus> {
  const { data } = await api.post<SeedStatus>("/seed/generate");
  return data;
}
