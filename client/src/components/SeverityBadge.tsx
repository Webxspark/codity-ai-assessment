/**
 * Severity badge component using HeroUI Chip.
 */
import { Chip } from "@heroui/react";

const SEVERITY_COLORS = {
  critical: "danger" as const,
  warning: "warning" as const,
  info: "default" as const,
};

const SEVERITY_LABELS = {
  critical: "Critical",
  warning: "Warning",
  info: "Info",
};

export function SeverityBadge({ severity }: { severity: string }) {
  const color = SEVERITY_COLORS[severity as keyof typeof SEVERITY_COLORS] || "default";
  const label = SEVERITY_LABELS[severity as keyof typeof SEVERITY_LABELS] || severity;
  return (
    <Chip color={color} size="sm" variant="soft">
      {label}
    </Chip>
  );
}

const ANOMALY_TYPE_LABELS: Record<string, string> = {
  spike: "Spike",
  drop: "Drop",
  sustained_deviation: "Sustained Deviation",
  pattern_change: "Pattern Change",
};

export function AnomalyTypeBadge({ type }: { type: string }) {
  return (
    <Chip size="sm" variant="secondary">
      {ANOMALY_TYPE_LABELS[type] || type}
    </Chip>
  );
}
