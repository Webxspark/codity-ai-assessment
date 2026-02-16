/**
 * Anomaly list panel showing detected anomalies with filters.
 */
import { Card, Chip } from "@heroui/react";
import { format } from "date-fns";
import { AlertTriangle, TrendingDown, TrendingUp, Activity } from "lucide-react";
import type { Anomaly } from "../types";
import { SeverityBadge, AnomalyTypeBadge } from "./SeverityBadge";

interface AnomalyListProps {
  anomalies: Anomaly[];
  selectedId?: string;
  onSelect: (anomaly: Anomaly) => void;
}

const TYPE_ICONS = {
  spike: TrendingUp,
  drop: TrendingDown,
  sustained_deviation: Activity,
  pattern_change: AlertTriangle,
};

export function AnomalyList({ anomalies, selectedId, onSelect }: AnomalyListProps) {
  if (anomalies.length === 0) {
    return (
      <div className="flex items-center justify-center h-40 text-muted text-sm">
        No anomalies detected
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {anomalies.map((anomaly) => {
        const Icon = TYPE_ICONS[anomaly.anomaly_type] || AlertTriangle;
        const isSelected = anomaly.id === selectedId;

        return (
          <Card
            key={anomaly.id}
            className={`cursor-pointer transition-all p-3 ${
              isSelected
                ? "ring-2 ring-primary bg-primary/5"
                : "hover:bg-surface-secondary"
            }`}
            variant="secondary"
            onClick={() => onSelect(anomaly)}
          >
            <div className="flex items-start gap-3 w-full">
              <div
                className={`mt-0.5 p-1.5 rounded-lg ${
                  anomaly.severity === "critical"
                    ? "bg-danger/10 text-danger"
                    : anomaly.severity === "warning"
                      ? "bg-warning/10 text-warning"
                      : "bg-default/10 text-default-500"
                }`}
              >
                <Icon size={16} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium text-foreground truncate">
                    {anomaly.service_name}
                  </span>
                  <Chip size="sm" variant="soft" color="accent">
                    {anomaly.metric_name}
                  </Chip>
                </div>
                <div className="flex items-center gap-2 mt-1">
                  <SeverityBadge severity={anomaly.severity} />
                  <AnomalyTypeBadge type={anomaly.anomaly_type} />
                </div>
                <div className="flex items-center justify-between mt-1.5">
                  <span className="text-xs text-muted">
                    {format(new Date(anomaly.detected_at), "MMM d, HH:mm")}
                  </span>
                  <span className="text-xs text-muted">
                    {(anomaly.confidence_score * 100).toFixed(0)}% confidence
                  </span>
                </div>
              </div>
            </div>
          </Card>
        );
      })}
    </div>
  );
}
