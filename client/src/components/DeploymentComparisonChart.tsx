/**
 * Before vs After deployment comparison chart.
 *
 * Overlays metric data from a configurable window before and after
 * a deployment so the user can visually inspect the impact.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, Button, Chip, Spinner } from "@heroui/react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
  Legend,
} from "recharts";
import { format } from "date-fns";
import { ArrowLeftRight, TrendingUp, TrendingDown, Minus } from "lucide-react";
import { fetchDeploymentComparison } from "../api/client";
import type { DeploymentLog, DeploymentComparisonMetric } from "../types";

interface DeploymentComparisonChartProps {
  deployment: DeploymentLog;
}

export function DeploymentComparisonChart({
  deployment,
}: DeploymentComparisonChartProps) {
  const [windowMinutes, setWindowMinutes] = useState(60);

  const { data: comparison, isLoading } = useQuery({
    queryKey: ["deployment-comparison", deployment.id, windowMinutes],
    queryFn: () => fetchDeploymentComparison(deployment.id, windowMinutes),
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-32">
        <Spinner size="sm" />
        <span className="ml-2 text-sm text-muted">Loading comparison…</span>
      </div>
    );
  }

  if (!comparison || comparison.metrics.length === 0) {
    return (
      <div className="text-sm text-muted text-center py-4">
        No metric data available for comparison.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Window selector */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-muted">Comparison window:</span>
        {[30, 60, 120].map((mins) => (
          <Button
            key={mins}
            size="sm"
            variant={windowMinutes === mins ? "primary" : "outline"}
            onPress={() => setWindowMinutes(mins)}
          >
            ±{mins}m
          </Button>
        ))}
      </div>

      {/* Per-metric comparison */}
      {comparison.metrics.map((metric) => (
        <MetricComparisonCard
          key={metric.metric_name}
          metric={metric}
          deployTimestamp={deployment.timestamp}
        />
      ))}
    </div>
  );
}

function MetricComparisonCard({
  metric,
  deployTimestamp,
}: {
  metric: DeploymentComparisonMetric;
  deployTimestamp: string;
}) {
  const deployTs = new Date(deployTimestamp).getTime();

  // Merge before + after data for a single overlay chart
  const chartData = [
    ...metric.before.data_points.map((dp) => ({
      timestamp: new Date(dp.timestamp).getTime(),
      value: dp.value,
      phase: "before" as const,
    })),
    ...metric.after.data_points.map((dp) => ({
      timestamp: new Date(dp.timestamp).getTime(),
      value: dp.value,
      phase: "after" as const,
    })),
  ].sort((a, b) => a.timestamp - b.timestamp);

  // Split into two series for dual-colour overlay
  // Merge all points by timestamp so Recharts renders continuous lines
  const merged: Record<
    number,
    { timestamp: number; before?: number; after?: number }
  > = {};
  for (const d of chartData) {
    if (!merged[d.timestamp]) merged[d.timestamp] = { timestamp: d.timestamp };
    if (d.phase === "before") merged[d.timestamp].before = d.value;
    else merged[d.timestamp].after = d.value;
  }
  const mergedData = Object.values(merged).sort(
    (a, b) => a.timestamp - b.timestamp
  );

  const { before, after, pct_change } = metric;

  const changeIcon =
    pct_change === null ? (
      <Minus size={12} />
    ) : pct_change > 0 ? (
      <TrendingUp size={12} />
    ) : (
      <TrendingDown size={12} />
    );

  const changeColor =
    pct_change === null
      ? "default"
      : Math.abs(pct_change) < 5
        ? "default"
        : pct_change > 0
          ? "danger"
          : "success";

  return (
    <Card variant="secondary" className="p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <ArrowLeftRight size={14} className="text-primary" />
          <span className="text-sm font-semibold text-foreground">
            {metric.metric_name}
          </span>
        </div>
        {pct_change !== null && (
          <Chip
            size="sm"
            variant="soft"
            color={changeColor as "default" | "danger" | "success"}
          >
            {changeIcon}
            {pct_change > 0 ? "+" : ""}
            {pct_change}%
          </Chip>
        )}
      </div>

      {/* Stats summary */}
      <div className="grid grid-cols-2 gap-4 mb-3 text-xs">
        <div className="border border-border rounded-lg p-2">
          <p className="font-medium text-muted mb-1">Before deployment</p>
          <div className="grid grid-cols-2 gap-1">
            <span className="text-muted">Mean:</span>
            <span className="font-mono">
              {before.stats.mean?.toFixed(2) ?? "—"}
            </span>
            <span className="text-muted">Std:</span>
            <span className="font-mono">
              ±{before.stats.std?.toFixed(2) ?? "—"}
            </span>
            <span className="text-muted">Range:</span>
            <span className="font-mono">
              {before.stats.min?.toFixed(1)}–{before.stats.max?.toFixed(1)}
            </span>
          </div>
        </div>
        <div className="border border-border rounded-lg p-2">
          <p className="font-medium text-muted mb-1">After deployment</p>
          <div className="grid grid-cols-2 gap-1">
            <span className="text-muted">Mean:</span>
            <span className="font-mono">
              {after.stats.mean?.toFixed(2) ?? "—"}
            </span>
            <span className="text-muted">Std:</span>
            <span className="font-mono">
              ±{after.stats.std?.toFixed(2) ?? "—"}
            </span>
            <span className="text-muted">Range:</span>
            <span className="font-mono">
              {after.stats.min?.toFixed(1)}–{after.stats.max?.toFixed(1)}
            </span>
          </div>
        </div>
      </div>

      {/* Overlay chart */}
      <ResponsiveContainer width="100%" height={200}>
        <LineChart
          data={mergedData}
          margin={{ top: 5, right: 20, bottom: 5, left: 10 }}
        >
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="var(--heroui-border)"
            opacity={0.3}
          />
          <XAxis
            dataKey="timestamp"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(ts) => format(new Date(ts), "HH:mm")}
            tick={{ fontSize: 10, fill: "var(--heroui-muted)" }}
            stroke="var(--heroui-border)"
          />
          <YAxis
            tick={{ fontSize: 10, fill: "var(--heroui-muted)" }}
            stroke="var(--heroui-border)"
            width={55}
          />
          <Tooltip
            labelFormatter={(ts) =>
              format(new Date(ts as number), "yyyy-MM-dd HH:mm")
            }
            contentStyle={{
              backgroundColor: "var(--heroui-surface)",
              border: "1px solid var(--heroui-border)",
              borderRadius: "8px",
              fontSize: "11px",
            }}
          />
          <Legend
            wrapperStyle={{ fontSize: "11px" }}
            iconType="plainline"
          />

          {/* Deployment marker line */}
          <ReferenceLine
            x={deployTs}
            stroke="#f59e0b"
            strokeWidth={2}
            strokeDasharray="6 3"
            label={{
              value: "Deploy",
              position: "top",
              fill: "#f59e0b",
              fontSize: 10,
            }}
          />

          {/* Before line (blue) */}
          <Line
            type="monotone"
            dataKey="before"
            name="Before"
            stroke="#3b82f6"
            strokeWidth={1.5}
            dot={false}
            connectNulls={false}
          />
          {/* After line (orange) */}
          <Line
            type="monotone"
            dataKey="after"
            name="After"
            stroke="#f97316"
            strokeWidth={1.5}
            dot={false}
            connectNulls={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </Card>
  );
}
