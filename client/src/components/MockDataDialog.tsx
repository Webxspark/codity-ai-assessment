/**
 * Mock Data Seeding Dialog — explains the data generation process
 * and provides a one-click button to seed or re-seed the database.
 */
import { useState, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Modal, Button, Chip, Spinner } from "@heroui/react";
import {
  Database,
  ServerCog,
  AlertTriangle,
  GitCommit,
  Settings,
  BarChart3,
  Sparkles,
  CheckCircle,
  TriangleAlert,
} from "lucide-react";
import { fetchSeedStatus, generateSeedData } from "../api/client";

export function MockDataDialog() {
  const [isSeeding, setIsSeeding] = useState(false);
  const [seedResult, setSeedResult] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: status, isLoading: loadingStatus } = useQuery({
    queryKey: ["seed-status"],
    queryFn: fetchSeedStatus,
  });

  const hasData = status?.has_data ?? false;

  const handleSeed = useCallback(async () => {
    setIsSeeding(true);
    setSeedResult(null);
    try {
      const result = await generateSeedData();
      setSeedResult(
        `Successfully seeded ${result.counts.metric_data_points.toLocaleString()} metric points, ` +
          `${result.counts.services} services, ${result.counts.deployments} deployments, ` +
          `${result.counts.config_changes} config changes.`
      );
      // Invalidate all dashboard queries so data refreshes
      queryClient.invalidateQueries({ queryKey: ["metrics-summary"] });
      queryClient.invalidateQueries({ queryKey: ["anomalies"] });
      queryClient.invalidateQueries({ queryKey: ["deployments"] });
      queryClient.invalidateQueries({ queryKey: ["config-changes"] });
      queryClient.invalidateQueries({ queryKey: ["metrics-chart"] });
      queryClient.invalidateQueries({ queryKey: ["seed-status"] });
    } catch (err) {
      setSeedResult(
        `Error: ${err instanceof Error ? err.message : "Failed to seed data"}`
      );
    } finally {
      setIsSeeding(false);
    }
  }, [queryClient]);

  return (
    <Modal>
      <Button size="sm" variant="outline">
        <Database size={14} />
        Mock Data
      </Button>
      <Modal.Backdrop>
        <Modal.Container>
          <Modal.Dialog className="sm:max-w-[520px]">
            <Modal.CloseTrigger />
            <Modal.Header>
              <Modal.Icon className="bg-primary/10 text-primary">
                <Database className="size-5" />
              </Modal.Icon>
              <Modal.Heading>Mock Data Generation</Modal.Heading>
            </Modal.Header>
            <Modal.Body>
              <div className="space-y-4">
                {/* Status section */}
                <div className="flex items-center gap-2 rounded-lg border border-border p-3">
                  {loadingStatus ? (
                    <Spinner size="sm" />
                  ) : hasData ? (
                    <>
                      <CheckCircle size={16} className="text-success shrink-0" />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-foreground">
                          Data exists in database
                        </p>
                        <div className="flex flex-wrap gap-1.5 mt-1">
                          <Chip size="sm" variant="soft">
                            {status!.counts.metric_data_points.toLocaleString()} metrics
                          </Chip>
                          <Chip size="sm" variant="soft">
                            {status!.counts.anomalies} anomalies
                          </Chip>
                          <Chip size="sm" variant="soft">
                            {status!.counts.services} services
                          </Chip>
                          <Chip size="sm" variant="soft">
                            {status!.counts.deployments} deploys
                          </Chip>
                        </div>
                      </div>
                    </>
                  ) : (
                    <>
                      <TriangleAlert size={16} className="text-warning shrink-0" />
                      <p className="text-sm text-foreground">
                        No data found — seed mock data to populate the dashboard.
                      </p>
                    </>
                  )}
                </div>

                {/* What gets generated */}
                <div>
                  <h4 className="text-sm font-semibold text-foreground mb-2">
                    What gets generated?
                  </h4>
                  <div className="space-y-2.5 text-sm text-foreground">
                    <div className="flex items-start gap-2.5">
                      <ServerCog size={15} className="text-primary mt-0.5 shrink-0" />
                      <div>
                        <span className="font-medium">3 Microservices</span>
                        <p className="text-xs text-muted">
                          payment-service, user-service, api-gateway — with
                          registry metadata, modules, and dependencies
                        </p>
                      </div>
                    </div>
                    <div className="flex items-start gap-2.5">
                      <BarChart3 size={15} className="text-primary mt-0.5 shrink-0" />
                      <div>
                        <span className="font-medium">
                          10,080 Metric Data Points
                        </span>
                        <p className="text-xs text-muted">
                          7 metric series (latency, error rate, CPU, queue
                          depth, etc.) across 24 hours at 1-minute granularity
                          with realistic sinusoidal daily patterns + noise
                        </p>
                      </div>
                    </div>
                    <div className="flex items-start gap-2.5">
                      <AlertTriangle size={15} className="text-warning mt-0.5 shrink-0" />
                      <div>
                        <span className="font-medium">
                          4 Planted Anomaly Scenarios
                        </span>
                        <p className="text-xs text-muted">
                          Latency spike (payment-service 14:28–14:45), sustained error
                          rate elevation (14:33–15:10), cross-service latency bump
                          (user-service), and CPU pattern break (user-service 09:00+)
                        </p>
                      </div>
                    </div>
                    <div className="flex items-start gap-2.5">
                      <GitCommit size={15} className="text-accent mt-0.5 shrink-0" />
                      <div>
                        <span className="font-medium">4 Deployments</span>
                        <p className="text-xs text-muted">
                          With commit SHAs, messages, changed files, PR links —
                          timed to correlate with planted anomalies
                        </p>
                      </div>
                    </div>
                    <div className="flex items-start gap-2.5">
                      <Settings size={15} className="text-accent mt-0.5 shrink-0" />
                      <div>
                        <span className="font-medium">3 Config Changes</span>
                        <p className="text-xs text-muted">
                          Redis pool size reduction, DB query timeout change,
                          rate limit adjustment — correlated with service issues
                        </p>
                      </div>
                    </div>
                  </div>
                </div>

                {/* How detection works note */}
                <div className="rounded-lg bg-surface-secondary p-3">
                  <div className="flex items-start gap-2">
                    <Sparkles size={14} className="text-primary mt-0.5 shrink-0" />
                    <p className="text-xs text-muted leading-relaxed">
                      <span className="font-medium text-foreground">After seeding:</span>{" "}
                      Click "Run Detection" in the dashboard header to trigger the
                      hybrid anomaly detector (Z-Score + EWMA + IQR ensemble).
                      Detected anomalies will be automatically correlated with
                      nearby deployments and config changes. Then ask the AI chat
                      to analyze root causes.
                    </p>
                  </div>
                </div>

                {/* Result message */}
                {seedResult && (
                  <div
                    className={`rounded-lg p-3 text-sm ${
                      seedResult.startsWith("Error")
                        ? "bg-danger/10 text-danger"
                        : "bg-success/10 text-success"
                    }`}
                  >
                    {seedResult}
                  </div>
                )}
              </div>
            </Modal.Body>
            <Modal.Footer>
              <Button slot="close" variant="outline" isDisabled={isSeeding}>
                Close
              </Button>
              <Button
                variant="primary"
                onPress={handleSeed}
                isDisabled={isSeeding}
              >
                {isSeeding ? (
                  <>
                    <Spinner size="sm" />
                    Seeding…
                  </>
                ) : hasData ? (
                  <>
                    <Database size={14} />
                    Drop All &amp; Seed New Mock Data
                  </>
                ) : (
                  <>
                    <Database size={14} />
                    Generate &amp; Seed Mock Data
                  </>
                )}
              </Button>
            </Modal.Footer>
          </Modal.Dialog>
        </Modal.Container>
      </Modal.Backdrop>
    </Modal>
  );
}
