/**
 * Workspace Settings page — Configure GitHub repo, Prometheus endpoint,
 * sync commits, start/stop polling, and manage data.
 */
import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Card,
  Button,
  Chip,
  Spinner,
  Input,
  TextArea,
  Alert,
} from "@heroui/react";
import {
  Settings as SettingsIcon,
  Github,
  Activity,
  RefreshCw,
  Trash2,
  CheckCircle,
  XCircle,
  ArrowLeft,
  Play,
  Square,
  Zap,
  Database,
} from "lucide-react";
import { Link } from "react-router-dom";

import {
  fetchWorkspaceConfig,
  saveWorkspaceConfig,
  testGitHubConnection,
  syncGitHubCommits,
  testPrometheusConnection,
  startPrometheusPolling,
  stopPrometheusPolling,
  dropAllData,
  dropMetricsData,
} from "../api/client";
import type { WorkspaceConfigInput, PrometheusQueryConfig } from "../types";

export function SettingsPage() {
  const queryClient = useQueryClient();

  // ── Load existing config ──────────────────────────────────────────
  const { data: config, isLoading } = useQuery({
    queryKey: ["workspace-config"],
    queryFn: fetchWorkspaceConfig,
  });

  // ── Form state ────────────────────────────────────────────────────
  const [name, setName] = useState("default");
  const [description, setDescription] = useState("");
  const [githubRepo, setGithubRepo] = useState("");
  const [githubToken, setGithubToken] = useState("");
  const [githubBranch, setGithubBranch] = useState("main");
  const [prometheusEndpoint, setPrometheusEndpoint] = useState("");
  const [pollInterval, setPollInterval] = useState(60);
  const [queries, setQueries] = useState<PrometheusQueryConfig[]>([
    { query: "", service_name: "", metric_name: "" },
  ]);

  // ── Status messages ───────────────────────────────────────────────
  const [ghTestResult, setGhTestResult] = useState<{
    status: string;
    message: string;
  } | null>(null);
  const [promTestResult, setPromTestResult] = useState<{
    status: string;
    message: string;
  } | null>(null);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  // Populate form from loaded config
  useEffect(() => {
    if (config) {
      setName(config.name || "default");
      setDescription(config.description || "");
      setGithubRepo(config.github_repo || "");
      setGithubBranch(config.github_default_branch || "main");
      setPrometheusEndpoint(config.prometheus_endpoint || "");
      setPollInterval(config.prometheus_poll_interval_seconds || 60);
      if (config.prometheus_queries && config.prometheus_queries.length > 0) {
        setQueries(
          config.prometheus_queries.map((q) => ({
            query: q.query || "",
            service_name: q.service_name || "",
            metric_name: q.metric_name || "",
          }))
        );
      }
      // Don't populate token (it's sensitive - backend doesn't expose it)
    }
  }, [config]);

  // ── Mutations ─────────────────────────────────────────────────────
  const saveMutation = useMutation({
    mutationFn: (data: WorkspaceConfigInput) => saveWorkspaceConfig(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace-config"] });
      setSaveMessage("Configuration saved successfully");
      setTimeout(() => setSaveMessage(null), 3000);
    },
    onError: (err: Error) => {
      setSaveMessage(`Error: ${err.message}`);
    },
  });

  const ghTestMutation = useMutation({
    mutationFn: testGitHubConnection,
    onSuccess: (result) => {
      setGhTestResult({
        status: result.status,
        message:
          result.status === "connected"
            ? `Connected to ${(result.details as Record<string, string>).full_name || githubRepo}`
            : `Error: ${(result.details as Record<string, string>).error || "Unknown error"}`,
      });
    },
    onError: (err: Error) => {
      setGhTestResult({ status: "error", message: err.message });
    },
  });

  const syncMutation = useMutation({
    mutationFn: () => syncGitHubCommits({ hours_back: 168, limit: 50 }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["deployments"] });
    },
  });

  const promTestMutation = useMutation({
    mutationFn: testPrometheusConnection,
    onSuccess: (result) => {
      setPromTestResult({
        status: result.status,
        message:
          result.status === "connected"
            ? `Connected — Prometheus v${(result.details as Record<string, string>).version || "?"}`
            : `Error: ${(result.details as Record<string, string>).error || "Unknown error"}`,
      });
    },
    onError: (err: Error) => {
      setPromTestResult({ status: "error", message: err.message });
    },
  });

  const startPollingMutation = useMutation({
    mutationFn: startPrometheusPolling,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace-config"] });
    },
  });

  const stopPollingMutation = useMutation({
    mutationFn: stopPrometheusPolling,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace-config"] });
    },
  });

  const dropAllMutation = useMutation({
    mutationFn: dropAllData,
    onSuccess: () => {
      queryClient.invalidateQueries();
    },
  });

  const dropMetricsMutation = useMutation({
    mutationFn: dropMetricsData,
    onSuccess: () => {
      queryClient.invalidateQueries();
    },
  });

  // ── Handlers ──────────────────────────────────────────────────────
  const handleSave = () => {
    const filteredQueries = queries.filter((q) => q.query.trim());
    const data: WorkspaceConfigInput = {
      name,
      description: description || undefined,
      github_repo: githubRepo || undefined,
      github_token: githubToken || undefined,
      github_default_branch: githubBranch || undefined,
      prometheus_endpoint: prometheusEndpoint || undefined,
      prometheus_poll_interval_seconds: pollInterval,
      prometheus_queries: filteredQueries.length > 0 ? filteredQueries : undefined,
    };
    saveMutation.mutate(data);
  };

  const addQuery = () => {
    setQueries([...queries, { query: "", service_name: "", metric_name: "" }]);
  };

  const removeQuery = (index: number) => {
    setQueries(queries.filter((_, i) => i !== index));
  };

  const updateQuery = (
    index: number,
    field: keyof PrometheusQueryConfig,
    value: string
  ) => {
    const updated = [...queries];
    updated[index] = { ...updated[index], [field]: value };
    setQueries(updated);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <Spinner size="lg" />
      </div>
    );
  }

  const isPolling = config?.is_polling === "true";

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="shrink-0 border-b border-border px-3 sm:px-6 py-3 flex items-center justify-between bg-surface sticky top-0 z-10">
        <div className="flex items-center gap-3">
          <Link className="button button--sm button--ghost" to="/">
            <ArrowLeft size={14} />
            <span className="hidden md:block">Dashboard</span>
          </Link>
          <SettingsIcon size={20} className="text-primary hidden md:block" />
          <h1 className="md:text-lg text-base font-bold text-foreground">
            Workspace Settings
          </h1>
        </div>
        <Button
          size="sm"
          variant="primary"
          onPress={handleSave}
          isDisabled={saveMutation.isPending}
        >
          {saveMutation.isPending ? <Spinner size="sm" /> : null}
          Save Configuration
        </Button>
      </header>

      <div className="max-w-4xl mx-auto p-4 sm:p-6 space-y-6">
        {/* Save feedback */}
        {saveMessage && (
          <Alert
            color={saveMessage.startsWith("Error") ? "danger" : "success"}
          >
            {saveMessage}
          </Alert>
        )}

        {/* General */}
        <Card variant="secondary" className="p-5">
          <h2 className="text-sm font-semibold text-foreground mb-4 flex items-center gap-2">
            <Database size={16} />
            General
          </h2>
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium text-foreground mb-1 block">Workspace Name</label>
              <Input
                placeholder="default"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div>
              <label className="text-sm font-medium text-foreground mb-1 block">Description</label>
              <TextArea
                placeholder="Brief description of this workspace..."
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={2}
                className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"
              />
            </div>
          </div>
        </Card>

        {/* GitHub Configuration */}
        <Card variant="secondary" className="p-5">
          <h2 className="text-sm font-semibold text-foreground mb-4 flex items-center gap-2">
            <Github size={16} />
            GitHub Repository
          </h2>
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium text-foreground mb-1 block">Repository</label>
              <Input
                placeholder="owner/repo"
                value={githubRepo}
                onChange={(e) => setGithubRepo(e.target.value)}
              />
              <p className="text-xs text-muted mt-1">GitHub repository in owner/repo format</p>
            </div>
            <div>
              <label className="text-sm font-medium text-foreground mb-1 block">Personal Access Token</label>
              <Input
                placeholder="ghp_..."
                type="password"
                value={githubToken}
                onChange={(e) => setGithubToken(e.target.value)}
              />
              <p className="text-xs text-muted mt-1">Required for private repos. Leave blank for public repos.</p>
            </div>
            <div>
              <label className="text-sm font-medium text-foreground mb-1 block">Default Branch</label>
              <Input
                placeholder="main"
                value={githubBranch}
                onChange={(e) => setGithubBranch(e.target.value)}
              />
            </div>

            <hr className="border-border" />

            {/* Test + Sync actions */}
            <div className="flex flex-wrap gap-3">
              <Button
                size="sm"
                variant="outline"
                onPress={() => {
                  setGhTestResult(null);
                  ghTestMutation.mutate();
                }}
                isDisabled={!githubRepo || ghTestMutation.isPending}
              >
                {ghTestMutation.isPending ? (
                  <Spinner size="sm" />
                ) : (
                  <Zap size={14} />
                )}
                Test Connection
              </Button>
              <Button
                size="sm"
                variant="outline"
                onPress={() => syncMutation.mutate()}
                isDisabled={!config?.github_repo || syncMutation.isPending}
              >
                {syncMutation.isPending ? (
                  <Spinner size="sm" />
                ) : (
                  <RefreshCw size={14} />
                )}
                Sync Commits
              </Button>
            </div>

            {/* Test result */}
            {ghTestResult && (
              <div
                className={`flex items-center gap-2 text-sm ${ghTestResult.status === "connected"
                    ? "text-success"
                    : "text-danger"
                  }`}
              >
                {ghTestResult.status === "connected" ? (
                  <CheckCircle size={14} />
                ) : (
                  <XCircle size={14} />
                )}
                {ghTestResult.message}
              </div>
            )}

            {/* Sync result */}
            {syncMutation.isSuccess && (
              <div className="text-sm text-success flex items-center gap-2">
                <CheckCircle size={14} />
                Synced {syncMutation.data.synced} new commits
              </div>
            )}
            {syncMutation.isError && (
              <div className="text-sm text-danger flex items-center gap-2">
                <XCircle size={14} />
                {(syncMutation.error as Error).message}
              </div>
            )}
          </div>
        </Card>

        {/* Prometheus Configuration */}
        <Card variant="secondary" className="p-5">
          <h2 className="text-sm font-semibold text-foreground mb-4 flex items-center gap-2">
            <Activity size={16} />
            Prometheus Endpoint
          </h2>
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium text-foreground mb-1 block">Endpoint URL</label>
              <Input
                placeholder="http://prometheus:9090"
                value={prometheusEndpoint}
                onChange={(e) => setPrometheusEndpoint(e.target.value)}
              />
              <p className="text-xs text-muted mt-1">Prometheus HTTP API base URL</p>
            </div>
            <div>
              <label className="text-sm font-medium text-foreground mb-1 block">Poll Interval (seconds)</label>
              <Input
                type="number"
                placeholder="60"
                value={String(pollInterval)}
                onChange={(e) => setPollInterval(Number(e.target.value) || 60)}
              />
            </div>

            <hr className="border-border" />

            {/* PromQL Queries */}
            <div>
              <div className="flex items-center justify-between mb-3">
                <label className="text-sm font-medium text-foreground">
                  PromQL Queries
                </label>
                <Button size="sm" variant="ghost" onPress={addQuery}>
                  + Add Query
                </Button>
              </div>
              <div className="space-y-3">
                {queries.map((q, i) => (
                  <div
                    key={i}
                    className="grid grid-cols-1 sm:grid-cols-[1fr_auto_auto_auto] gap-2 items-end"
                  >
                    <div>
                      <label className="text-xs text-muted mb-0.5 block">Query {i + 1}</label>
                      <Input
                        placeholder='rate(http_requests_total[5m])'
                        value={q.query}
                        onChange={(e) => updateQuery(i, "query", e.target.value)}
                      />
                    </div>
                    <div>
                      <label className="text-xs text-muted mb-0.5 block">Service</label>
                      <Input
                        placeholder="api"
                        value={q.service_name}
                        onChange={(e) => updateQuery(i, "service_name", e.target.value)}
                      />
                    </div>
                    <div>
                      <label className="text-xs text-muted mb-0.5 block">Metric</label>
                      <Input
                        placeholder="request_rate"
                        value={q.metric_name}
                        onChange={(e) => updateQuery(i, "metric_name", e.target.value)}
                      />
                    </div>
                    {queries.length > 1 && (
                      <Button
                        size="sm"
                        variant="ghost"
                        isIconOnly
                        onPress={() => removeQuery(i)}
                        className="text-danger"
                      >
                        <Trash2 size={14} />
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            </div>

            <hr className="border-border" />

            {/* Test + Polling actions */}
            <div className="flex flex-wrap gap-3">
              <Button
                size="sm"
                variant="outline"
                onPress={() => {
                  setPromTestResult(null);
                  promTestMutation.mutate();
                }}
                isDisabled={!prometheusEndpoint || promTestMutation.isPending}
              >
                {promTestMutation.isPending ? (
                  <Spinner size="sm" />
                ) : (
                  <Zap size={14} />
                )}
                Test Connection
              </Button>
              {isPolling ? (
                <Button
                  size="sm"
                  variant="outline"
                  onPress={() => stopPollingMutation.mutate()}
                  isDisabled={stopPollingMutation.isPending}
                  className="text-danger border-danger"
                >
                  {stopPollingMutation.isPending ? (
                    <Spinner size="sm" />
                  ) : (
                    <Square size={14} />
                  )}
                  Stop Polling
                </Button>
              ) : (
                <Button
                  size="sm"
                  variant="outline"
                  onPress={() => startPollingMutation.mutate()}
                  isDisabled={
                    !config?.prometheus_endpoint || startPollingMutation.isPending
                  }
                >
                  {startPollingMutation.isPending ? (
                    <Spinner size="sm" />
                  ) : (
                    <Play size={14} />
                  )}
                  Start Polling
                </Button>
              )}
              {isPolling && (
                <Chip color="success" variant="soft" size="sm">
                  Polling active — every {config?.prometheus_poll_interval_seconds}s
                </Chip>
              )}
            </div>

            {/* Test result */}
            {promTestResult && (
              <div
                className={`flex items-center gap-2 text-sm ${promTestResult.status === "connected"
                    ? "text-success"
                    : "text-danger"
                  }`}
              >
                {promTestResult.status === "connected" ? (
                  <CheckCircle size={14} />
                ) : (
                  <XCircle size={14} />
                )}
                {promTestResult.message}
              </div>
            )}
          </div>
        </Card>

        {/* Data Management */}
        <Card variant="secondary" className="p-5">
          <h2 className="text-sm font-semibold text-foreground mb-4 flex items-center gap-2">
            <Trash2 size={16} />
            Data Management
          </h2>
          <p className="text-sm text-muted mb-4">
            Drop data to reconfigure this workspace for a different
            repository or metrics endpoint. The workspace configuration itself is preserved.
          </p>
          <div className="flex flex-wrap gap-3">
            <Button
              size="sm"
              variant="outline"
              onPress={() => {
                if (
                  confirm(
                    "Drop all metric data and anomalies? This cannot be undone."
                  )
                ) {
                  dropMetricsMutation.mutate();
                }
              }}
              isDisabled={dropMetricsMutation.isPending}
              className="text-warning border-warning"
            >
              {dropMetricsMutation.isPending ? (
                <Spinner size="sm" />
              ) : (
                <Trash2 size={14} />
              )}
              Drop Metrics & Anomalies
            </Button>
            <Button
              size="sm"
              variant="outline"
              onPress={() => {
                if (
                  confirm(
                    "Drop ALL data (metrics, anomalies, deployments, chats)? This cannot be undone."
                  )
                ) {
                  dropAllMutation.mutate();
                }
              }}
              isDisabled={dropAllMutation.isPending}
              className="text-danger border-danger"
            >
              {dropAllMutation.isPending ? (
                <Spinner size="sm" />
              ) : (
                <Trash2 size={14} />
              )}
              Drop All Data
            </Button>
          </div>
          {(dropAllMutation.isSuccess || dropMetricsMutation.isSuccess) && (
            <div className="mt-3 text-sm text-success flex items-center gap-2">
              <CheckCircle size={14} />
              Data cleared successfully
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
