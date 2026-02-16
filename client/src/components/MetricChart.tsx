/**
 * Metric time-series chart with anomaly markers.
 */
import { useMemo } from "react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceDot,
  CartesianGrid,
} from "recharts";
import { format } from "date-fns";
import type { MetricDataPoint, Anomaly } from "../types";

interface MetricChartProps {
  data: MetricDataPoint[];
  anomalies?: Anomaly[];
  title?: string;
  height?: number;
  onAnomalyClick?: (anomaly: Anomaly) => void;
}

export function MetricChart({
  data,
  anomalies = [],
  title,
  height = 280,
  onAnomalyClick,
}: MetricChartProps) {
  const chartData = useMemo(() => {
    return data.map((dp) => ({
      timestamp: new Date(dp.timestamp).getTime(),
      value: dp.value,
      label: format(new Date(dp.timestamp), "HH:mm"),
    }));
  }, [data]);

  const anomalyPoints = useMemo(() => {
    return anomalies.map((a) => ({
      timestamp: new Date(a.detected_at).getTime(),
      value: a.metric_value,
      anomaly: a,
    }));
  }, [anomalies]);

  if (chartData.length === 0) {
    return (
      <div className="flex items-center justify-center h-[200px] text-muted">
        No data available
      </div>
    );
  }

  return (
    <div>
      {title && (
        <h3 className="text-sm font-semibold text-foreground mb-2">{title}</h3>
      )}
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={chartData} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--heroui-border)" opacity={0.3} />
          <XAxis
            dataKey="timestamp"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(ts) => format(new Date(ts), "HH:mm")}
            tick={{ fontSize: 11, fill: "var(--heroui-muted)" }}
            stroke="var(--heroui-border)"
          />
          <YAxis
            tick={{ fontSize: 11, fill: "var(--heroui-muted)" }}
            stroke="var(--heroui-border)"
            width={60}
          />
          <Tooltip
            labelFormatter={(ts) => format(new Date(ts as number), "yyyy-MM-dd HH:mm")}
            contentStyle={{
              backgroundColor: "var(--heroui-surface)",
              border: "1px solid var(--heroui-border)",
              borderRadius: "8px",
              fontSize: "12px",
            }}
          />
          <Line
            type="monotone"
            dataKey="value"
            stroke="#3b82f6"
            strokeWidth={1.5}
            dot={false}
            activeDot={{ r: 3 }}
          />
          {anomalyPoints.map((point, i) => (
            <ReferenceDot
              key={i}
              x={point.timestamp}
              y={point.value}
              r={6}
              fill={
                point.anomaly.severity === "critical"
                  ? "#ef4444"
                  : point.anomaly.severity === "warning"
                    ? "#f59e0b"
                    : "#6b7280"
              }
              stroke="#fff"
              strokeWidth={2}
              style={{ cursor: onAnomalyClick ? "pointer" : "default" }}
              onClick={() => onAnomalyClick?.(point.anomaly)}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
