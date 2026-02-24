/**
 * Main dashboard page — metrics overview, anomaly list, detail panel, and AI chat.
 *
 * Heavy components are lazily loaded via React.lazy + Suspense so the initial
 * bundle stays small and the page above-the-fold renders faster.
 */
import { useState, useMemo, lazy, Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, Button, Chip, Spinner } from "@heroui/react";
import {
  Activity,
  AlertTriangle,
  RefreshCw,
  PanelRightOpen,
  PanelRightClose,
  LayoutDashboard,
  History,
  Settings,
} from "lucide-react";

import {
  fetchMetrics,
  fetchMetricsSummary,
  fetchAnomalies,
  fetchDeployments,
  fetchConfigChanges,
  triggerDetection,
} from "../api/client";
import type { Anomaly, MetricDataPoint } from "../types";
import { Link } from "react-router-dom";

// ── Eagerly loaded (lightweight / always visible) ───────────────────
import { ServiceOverview } from "../components/ServiceOverview";
import { AnomalyList } from "../components/AnomalyList";
import { ErrorBoundary } from "../components/ErrorBoundary";

// ── Lazily loaded (heavy / conditionally visible) ───────────────────
const MetricChart = lazy(() =>
  import("../components/MetricChart").then((m) => ({ default: m.MetricChart }))
);
const AnomalyDetail = lazy(() =>
  import("../components/AnomalyDetail").then((m) => ({ default: m.AnomalyDetail }))
);
const ChatPanel = lazy(() =>
  import("../components/ChatPanel").then((m) => ({ default: m.ChatPanel }))
);
const DeploymentTimeline = lazy(() =>
  import("../components/DeploymentTimeline").then((m) => ({
    default: m.DeploymentTimeline,
  }))
);
const MockDataDialog = lazy(() =>
  import("../components/MockDataDialog").then((m) => ({
    default: m.MockDataDialog,
  }))
);

/** Shared fallback spinner for Suspense boundaries */
function LazyFallback() {
  return (
    <div className="flex items-center justify-center py-8">
      <Spinner size="sm" />
    </div>
  );
}

export function Dashboard() {
  const [selectedService, setSelectedService] = useState<string | null>(null);
  const [selectedAnomaly, setSelectedAnomaly] = useState<Anomaly | null>(null);
  const [chatAnomalyId, setChatAnomalyId] = useState<string | undefined>();
  const [showChat, setShowChat] = useState(false);
  const [isDetecting, setIsDetecting] = useState(false);

  // ── Data queries ──────────────────────────────────────────────────

  const { data: summaries = [], isLoading: loadingSummaries } = useQuery({
    queryKey: ["metrics-summary"],
    queryFn: () => fetchMetricsSummary(),
  });

  const { data: anomalies = [], isLoading: loadingAnomalies, refetch: refetchAnomalies } = useQuery({
    queryKey: ["anomalies"],
    queryFn: () => fetchAnomalies({ limit: 500 }),
  });

  const { data: deployments = [] } = useQuery({
    queryKey: ["deployments"],
    queryFn: () => fetchDeployments(),
  });

  const { data: configChanges = [] } = useQuery({
    queryKey: ["config-changes"],
    queryFn: () => fetchConfigChanges(),
  });

  // Fetch metrics for charts
  const metricsToFetch = useMemo(() => {
    if (selectedService) {
      return summaries
        .filter((s) => s.service_name === selectedService)
        .map((s) => ({ service: s.service_name, metric: s.metric_name }));
    }
    // Show first metric from each service by default
    const seen = new Set<string>();
    return summaries
      .filter((s) => {
        if (seen.has(s.service_name)) return false;
        seen.add(s.service_name);
        return true;
      })
      .map((s) => ({ service: s.service_name, metric: s.metric_name }));
  }, [summaries, selectedService]);

  const metricQueries = useQuery({
    queryKey: ["metrics-chart", metricsToFetch],
    queryFn: async () => {
      const results: Record<string, MetricDataPoint[]> = {};
      await Promise.all(
        metricsToFetch.map(async ({ service, metric }) => {
          const key = `${service}/${metric}`;
          results[key] = await fetchMetrics({
            service_name: service,
            metric_name: metric,
            limit: 1440,
          });
        })
      );
      return results;
    },
    enabled: metricsToFetch.length > 0,
  });

  // Filter anomalies for selected service
  const filteredAnomalies = useMemo(() => {
    if (selectedService) {
      return anomalies.filter((a) => a.service_name === selectedService);
    }
    return anomalies;
  }, [anomalies, selectedService]);

  // Get anomalies for a specific chart
  const getChartAnomalies = (service: string, metric: string) => {
    return anomalies.filter(
      (a) => a.service_name === service && a.metric_name === metric
    );
  };

  const handleDetect = async () => {
    setIsDetecting(true);
    try {
      await triggerDetection();
      await refetchAnomalies();
    } finally {
      setIsDetecting(false);
    }
  };

  const handleOpenChat = (anomalyId: string) => {
    setChatAnomalyId(anomalyId);
    if (!showChat) setShowChat(true);
  };

  const handleAnomalySelect = (anomaly: Anomaly) => {
    setSelectedAnomaly(anomaly);
  };

  // Summary stats
  const criticalCount = anomalies.filter((a) => a.severity === "critical").length;
  const warningCount = anomalies.filter((a) => a.severity === "warning").length;

  const isLoading = loadingSummaries || loadingAnomalies;

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Main content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar — wraps on mobile */}
        <header className="shrink-0 border-b border-border px-3 sm:px-6 py-3 flex flex-wrap items-center justify-between gap-2 bg-surface">
          <div className="flex items-center gap-2 sm:gap-3 min-w-0">
            <Activity size={22} className="text-primary shrink-0" />
            <h1 className="text-base sm:text-lg font-bold text-foreground truncate">CodityAI</h1>
            <span className="text-xs text-muted hidden sm:inline">
              Metrics Anomaly Detection & Code Insight
            </span>
          </div>
          <div className="flex items-center gap-1.5 sm:gap-3 flex-wrap">
            {criticalCount > 0 && (
              <Chip color="danger" variant="soft" size="sm">
                {criticalCount} Critical
              </Chip>
            )}
            {warningCount > 0 && (
              <Chip color="warning" variant="soft" size="sm">
                {warningCount} Warning
              </Chip>
            )}
            <Suspense fallback={null}>
              <MockDataDialog />
            </Suspense>
            <Link to="/settings" className="button button--sm button--outline">
              <Settings size={14} />
              <span className="hidden sm:inline">Settings</span>
            </Link>
            <Button
              size="sm"
              variant="outline"
              onPress={handleDetect}
              isDisabled={isDetecting}
            >
              {isDetecting ? (
                <Spinner size="sm" />
              ) : (
                <RefreshCw size={14} />
              )}
              <span className="hidden sm:inline">{isDetecting ? "Detecting..." : "Run Detection"}</span>
            </Button>
            <Button
              size="sm"
              variant={showChat ? "primary" : "outline"}
              onPress={() => setShowChat(!showChat)}
            >
              {showChat ? (
                <PanelRightClose size={14} />
              ) : (
                <PanelRightOpen size={14} />
              )}
              <span className="hidden sm:inline">AI Chat</span>
            </Button>
          </div>
        </header>

        {isLoading ? (
          <div className="flex-1 flex items-center justify-center">
            <Spinner size="lg" />
          </div>
        ) : (
          <div className="flex-1 flex overflow-hidden relative">
            {/* Left panel — content area */}
            <div className="flex-1 overflow-y-auto p-3 sm:p-6 space-y-4 sm:space-y-6">
              {/* Service overview cards */}
              <ServiceOverview
                summaries={summaries}
                anomalies={anomalies}
                selectedService={selectedService}
                onSelectService={setSelectedService}
              />

              {/* Metrics charts */}
              <div>
                <h2 className="text-sm font-semibold text-foreground mb-3 flex items-center gap-2">
                  <LayoutDashboard size={16} />
                  {selectedService
                    ? `${selectedService} Metrics`
                    : "Metrics Overview"}
                  {selectedService && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onPress={() => setSelectedService(null)}
                    >
                      Show All
                    </Button>
                  )}
                </h2>
                <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                  {metricsToFetch.map(({ service, metric }) => {
                    const key = `${service}/${metric}`;
                    const data = metricQueries.data?.[key] || [];
                    const chartAnomalies = getChartAnomalies(service, metric);
                    return (
                      <Card key={key} variant="secondary" className="p-3 sm:p-4">
                        <ErrorBoundary>
                          <Suspense fallback={<LazyFallback />}>
                            <MetricChart
                              data={data}
                              anomalies={chartAnomalies}
                              title={`${service} / ${metric}`}
                              onAnomalyClick={handleAnomalySelect}
                            />
                          </Suspense>
                        </ErrorBoundary>
                      </Card>
                    );
                  })}
                </div>
              </div>

              {/* Bottom section: Anomaly list + detail + timeline */}
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-6">
                {/* Anomaly list */}
                <div className="lg:col-span-1">
                  <h2 className="text-sm font-semibold text-foreground mb-3 flex items-center gap-2">
                    <AlertTriangle size={16} />
                    Anomalies ({filteredAnomalies.length})
                  </h2>
                  <div className="max-h-80 sm:max-h-125 overflow-y-auto pr-1 p-3">
                    <AnomalyList
                      anomalies={filteredAnomalies}
                      selectedId={selectedAnomaly?.id}
                      onSelect={handleAnomalySelect}
                    />
                  </div>
                </div>

                {/* Detail panel */}
                <div className="lg:col-span-2">
                  {selectedAnomaly ? (
                    <div>
                      <div className="flex items-center justify-between mb-3">
                        <h2 className="text-sm font-semibold text-foreground flex items-center gap-2">
                          <Activity size={16} />
                          Anomaly Details
                        </h2>
                        <Button
                          size="sm"
                          variant="ghost"
                          onPress={() => setSelectedAnomaly(null)}
                        >
                          Close
                        </Button>
                      </div>
                      <Suspense fallback={<LazyFallback />}>
                        <ErrorBoundary>
                          <AnomalyDetail
                            anomaly={selectedAnomaly}
                            deployments={deployments}
                            configChanges={configChanges}
                            onOpenChat={handleOpenChat}
                          />
                        </ErrorBoundary>
                      </Suspense>
                    </div>
                  ) : (
                    <div className="flex items-center justify-center h-48 text-sm text-muted border border-dashed border-border rounded-lg">
                      Select an anomaly to see details
                    </div>
                  )}
                </div>
              </div>

              {/* Deployment Timeline — always visible */}
              <div>
                <h2 className="text-sm font-semibold text-foreground mb-3 flex items-center gap-2">
                  <History size={16} />
                  Deployment &amp; Config Timeline
                </h2>
                <Card variant="secondary" className="p-3 sm:p-4">
                  <ErrorBoundary>
                    <Suspense fallback={<LazyFallback />}>
                      <DeploymentTimeline
                        deployments={deployments}
                        configChanges={configChanges}
                      />
                    </Suspense>
                  </ErrorBoundary>
                </Card>
              </div>
            </div>

            {/* Right panel — AI Chat (overlay on mobile, sidebar on desktop) */}
            {showChat && (
              <div className="fixed inset-0 z-40 bg-surface md:static md:inset-auto md:z-auto md:w-130 shrink-0 md:border-l border-border">
                <ErrorBoundary>
                  <Suspense fallback={<LazyFallback />}>
                    <ChatPanel
                      anomalyId={chatAnomalyId}
                      anomalies={anomalies}
                      onClose={() => setShowChat(false)}
                    />
                  </Suspense>
                </ErrorBoundary>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
