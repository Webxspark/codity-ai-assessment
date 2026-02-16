/**
 * Service overview cards showing summary stats.
 */
import { Card, Chip } from "@heroui/react";
import { Server, Activity, AlertTriangle } from "lucide-react";
import type { MetricsSummary, Anomaly } from "../types";

interface ServiceOverviewProps {
  summaries: MetricsSummary[];
  anomalies: Anomaly[];
  selectedService: string | null;
  onSelectService: (service: string | null) => void;
}

export function ServiceOverview({
  summaries,
  anomalies,
  selectedService,
  onSelectService,
}: ServiceOverviewProps) {
  // Group summaries by service
  const services = new Map<string, MetricsSummary[]>();
  for (const s of summaries) {
    const existing = services.get(s.service_name) || [];
    existing.push(s);
    services.set(s.service_name, existing);
  }

  // Count anomalies per service
  const anomalyCounts = new Map<string, { total: number; critical: number }>();
  for (const a of anomalies) {
    const existing = anomalyCounts.get(a.service_name) || {
      total: 0,
      critical: 0,
    };
    existing.total++;
    if (a.severity === "critical") existing.critical++;
    anomalyCounts.set(a.service_name, existing);
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      {Array.from(services.entries()).map(([serviceName, metrics]) => {
        const anomalyInfo = anomalyCounts.get(serviceName);
        const isSelected = selectedService === serviceName;
        const hasCritical = anomalyInfo && anomalyInfo.critical > 0;

        return (
          <Card
            key={serviceName}
            className={`cursor-pointer transition-all ${
              isSelected
                ? "ring-2 ring-primary"
                : hasCritical
                  ? "ring-1 ring-danger/40"
                  : ""
            }`}
            variant="secondary"
            onClick={() =>
              onSelectService(isSelected ? null : serviceName)
            }
          >
            <Card.Header>
              <div className="flex items-center justify-between w-full">
                <div className="flex items-center gap-2">
                  <Server size={16} className="text-primary" />
                  <Card.Title className="text-sm">{serviceName}</Card.Title>
                </div>
                {anomalyInfo && (
                  <div className="flex items-center gap-1">
                    <AlertTriangle
                      size={14}
                      className={
                        hasCritical ? "text-danger" : "text-warning"
                      }
                    />
                    <span
                      className={`text-xs font-medium ${
                        hasCritical ? "text-danger" : "text-warning"
                      }`}
                    >
                      {anomalyInfo.total}
                    </span>
                  </div>
                )}
              </div>
            </Card.Header>
            <Card.Content>
              <div className="flex flex-wrap gap-1.5">
                {metrics.map((m) => (
                  <Chip key={m.metric_name} size="sm" variant="soft">
                    <span className="flex items-center gap-1">
                      <Activity size={10} />
                      {m.metric_name}
                    </span>
                  </Chip>
                ))}
              </div>
            </Card.Content>
          </Card>
        );
      })}
    </div>
  );
}
