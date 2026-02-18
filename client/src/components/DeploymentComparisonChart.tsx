/**
 * Before vs After deployment comparison chart.
 *
 * Overlays metric data from a configurable window before and after
 * a deployment so the user can visually inspect the impact.
 */
import { useState, useMemo, useCallback, useRef, useEffect } from "react";
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
  ReferenceDot,
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

/* ── Chart layout constants (must match LineChart props) ───────────
 * Used to convert data-space timestamps into pixel X coordinates so
 * we can absolutely-position the mirror tooltip on the opposite side.
 */
const CHART_HEIGHT = 200;
const CHART_MARGIN = { top: 5, right: 20, bottom: 5, left: 10 };
const Y_AXIS_WIDTH = 55; // matches width={55} on <YAxis>
const X_AXIS_HEIGHT = 28; // approximate rendered height of the XAxis

/* ── Primary (cursor) tooltip ──────────────────────────────────── */
function PrimaryTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: any[];
}) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;
  const phase: "before" | "after" = d.before != null ? "before" : "after";
  const value: number = d.before ?? d.after;
  return (
    <div className="bg-surface border border-border rounded-lg px-2.5 py-2 text-xs shadow-lg">
      <p className="text-muted mb-1">{format(new Date(d.timestamp), "HH:mm:ss")}</p>
      <div className="flex items-center gap-1.5">
        <span
          className="inline-block w-2 h-2 rounded-full shrink-0"
          style={{ backgroundColor: phase === "before" ? "#3b82f6" : "#f97316" }}
        />
        <span className="capitalize font-medium">{phase}:</span>
        <span className="font-mono ml-1">{value?.toFixed(2)}</span>
      </div>
    </div>
  );
}

/* ── Mirror tooltip (absolutely positioned overlay) ────────────── */
function MirrorTooltipOverlay({
  mirror,
  hoveredPhase,
  hoveredValue,
  pixelX,
  chartHeight,
  containerWidth,
}: {
  mirror: { timestamp: number; value: number; phase: "before" | "after" } | null;
  hoveredPhase: "before" | "after" | null;
  hoveredValue: number | null;
  pixelX: number | null;
  chartHeight: number;
  containerWidth: number;
}) {
  if (!mirror || pixelX === null || hoveredPhase === null || hoveredValue === null || containerWidth <= 0) return null;

  const bv = hoveredPhase === "before" ? hoveredValue : mirror.value;
  const av = hoveredPhase === "after" ? hoveredValue : mirror.value;
  const diff = av - bv;
  const pct = bv !== 0 ? (diff / bv) * 100 : 0;
  const deltaClass =
    diff > 0.01 ? "text-red-500" : diff < -0.01 ? "text-green-500" : "text-muted";

  // Place the box above the mirror dot; clamp so it doesn't overflow left or right.
  // pixelX is in container-relative pixels (SVG x + CHART_MARGIN.left).
  const TOOLTIP_W = 160;
  const verticalCenter = chartHeight / 2;
  const clampedLeft = Math.min(
    Math.max(6, pixelX - TOOLTIP_W / 2),
    Math.max(6, containerWidth - TOOLTIP_W - 6)
  );

  return (
    <div
      className="absolute pointer-events-none z-20"
      style={{
        // Horizontally: centre on mirror dot, clamped to chart bounds
        left: clampedLeft,
        width: TOOLTIP_W,
        // Vertically: keep visible and stable
        top: 8,
      }}
    >
      {/* Dashed vertical line connecting tooltip to the dot */}
      <div
        className="absolute"
        style={{
          left: "50%",
          top: "100%",
          transform: "translateX(-50%)",
          width: 1,
          height: Math.max(verticalCenter - 12, 20),
          borderLeft: `2px dashed ${mirror.phase === "before" ? "#3b82f6" : "#f97316"}`,
          opacity: 0.5,
        }}
      />
      <div className="bg-surface border-2 border-border rounded-lg px-2.5 py-2 text-xs shadow-xl"
        style={{ borderColor: mirror.phase === "before" ? "#3b82f6" : "#f97316" }}
      >
        {/* Mirror label */}
        <p className="flex items-center gap-1 text-muted mb-1.5 font-medium">
          <ArrowLeftRight size={9} />
          Mirror · {format(new Date(mirror.timestamp), "HH:mm:ss")}
        </p>
        {/* Mirror value */}
        <div className="flex items-center gap-1.5 mb-1.5">
          <span
            className="inline-block w-2 h-2 rounded-full shrink-0"
            style={{ backgroundColor: mirror.phase === "before" ? "#3b82f6" : "#f97316" }}
          />
          <span className="capitalize font-medium">{mirror.phase}:</span>
          <span className="font-mono ml-auto">{mirror.value.toFixed(2)}</span>
        </div>
        {/* Δ */}
        <div className="border-t border-border pt-1.5 flex items-center gap-1">
          <span className="text-muted">Δ</span>
          <span className={`font-mono font-semibold ${deltaClass}`}>
            {diff >= 0 ? "+" : ""}{diff.toFixed(2)}{" "}
            <span className="opacity-70">({pct >= 0 ? "+" : ""}{pct.toFixed(1)}%)</span>
          </span>
        </div>
      </div>
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

  const minTs = mergedData[0]?.timestamp ?? 0;
  const maxTs = mergedData[mergedData.length - 1]?.timestamp ?? 1;

  // ── Container width tracking (for mirror pixel-X calculation) ───
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(0);
  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver(([entry]) =>
      setContainerWidth(entry.contentRect.width)
    );
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  // ── Hover state ──────────────────────────────────────────────────
  const [hoverInfo, setHoverInfo] = useState<{
    mirror: { timestamp: number; value: number; phase: "before" | "after" } | null;
    mirrorPixelX: number | null;
    hoveredPhase: "before" | "after";
    hoveredValue: number;
  } | null>(null);

  const beforeSorted = useMemo(
    () =>
      metric.before.data_points
        .map((dp) => ({ ts: new Date(dp.timestamp).getTime(), v: dp.value }))
        .sort((a, b) => a.ts - b.ts),
    [metric.before.data_points]
  );

  const afterSorted = useMemo(
    () =>
      metric.after.data_points
        .map((dp) => ({ ts: new Date(dp.timestamp).getTime(), v: dp.value }))
        .sort((a, b) => a.ts - b.ts),
    [metric.after.data_points]
  );

  const findNearest = useCallback(
    (points: { ts: number; v: number }[], targetTs: number) => {
      if (points.length === 0) return null;
      let lo = 0, hi = points.length - 1;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (points[mid].ts < targetTs) lo = mid + 1;
        else hi = mid;
      }
      const candidates = [points[lo]];
      if (lo > 0) candidates.push(points[lo - 1]);
      return candidates.reduce((best, c) =>
        Math.abs(c.ts - targetTs) < Math.abs(best.ts - targetTs) ? c : best
      );
    },
    []
  );

  /**
   * Convert a data-space timestamp to a pixel X offset relative to
   * the chart container div (same coordinate space as position:absolute).
   *
   * Plot area starts at: CHART_MARGIN.left + Y_AXIS_WIDTH
   * Plot area ends at:   containerWidth - CHART_MARGIN.right
   */
  const tsToPixelX = useCallback(
    (ts: number) => {
      if (containerWidth === 0 || maxTs === minTs) return null;
      const plotLeft = CHART_MARGIN.left + Y_AXIS_WIDTH;
      const plotRight = containerWidth - CHART_MARGIN.right;
      const ratio = (ts - minTs) / (maxTs - minTs);
      return plotLeft + ratio * (plotRight - plotLeft);
    },
    [containerWidth, minTs, maxTs]
  );

  const handleChartMouseMove = useCallback(
    (e: any) => {
      if (!e?.activePayload?.length) { setHoverInfo(null); return; }
      const activeItem =
        e.activePayload.find((p: any) => p?.value != null) ?? e.activePayload[0];
      const payload = activeItem?.payload;
      if (!payload) { setHoverInfo(null); return; }

      const ts = payload.timestamp as number;
      const mirrorTs = deployTs + (deployTs - ts);
      const hoveredPhase: "before" | "after" =
        activeItem?.dataKey === "before"
          ? "before"
          : activeItem?.dataKey === "after"
            ? "after"
            : payload.before != null
              ? "before"
              : "after";
      const hoveredValue: number =
        typeof activeItem?.value === "number"
          ? activeItem.value
          : (payload.before ?? payload.after);

      const mirrorPoints = hoveredPhase === "before" ? afterSorted : beforeSorted;
      const mirrorPhase: "before" | "after" = hoveredPhase === "before" ? "after" : "before";
      const m = findNearest(mirrorPoints, mirrorTs);

      setHoverInfo({
        mirror: m ? { timestamp: m.ts, value: m.v, phase: mirrorPhase } : null,
        mirrorPixelX: m ? tsToPixelX(m.ts) : null,
        hoveredPhase,
        hoveredValue,
      });
    },
    [deployTs, beforeSorted, afterSorted, findNearest, tsToPixelX]
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
    <Card variant="secondary" className="p-4 overflow-visible">
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

      {/* Overlay chart — wrapped in relative container for mirror tooltip */}
      <div ref={containerRef} className="relative overflow-visible">
        <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
          <LineChart
            data={mergedData}
            margin={CHART_MARGIN}
            onMouseMove={handleChartMouseMove}
            onMouseLeave={() => setHoverInfo(null)}
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
              width={Y_AXIS_WIDTH}
            />
            <Tooltip
              content={<PrimaryTooltip />}
              allowEscapeViewBox={{ x: false, y: true }}
            />
            <Legend wrapperStyle={{ fontSize: "11px" }} iconType="plainline" />

            {/* Deployment marker */}
            <ReferenceLine
              x={deployTs}
              stroke="#f59e0b"
              strokeWidth={2}
              strokeDasharray="6 3"
              label={{ value: "Deploy", position: "top", fill: "#f59e0b", fontSize: 10 }}
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

            {/* Mirror dot on the opposite line */}
            {hoverInfo?.mirror && (
              <ReferenceDot
                x={hoverInfo.mirror.timestamp}
                y={hoverInfo.mirror.value}
                r={5}
                fill={hoverInfo.mirror.phase === "before" ? "#3b82f6" : "#f97316"}
                stroke="white"
                strokeWidth={2}
              />
            )}
          </LineChart>
        </ResponsiveContainer>

        {/* Second, separately positioned mirror tooltip overlay */}
        <MirrorTooltipOverlay
          mirror={hoverInfo?.mirror ?? null}
          hoveredPhase={hoverInfo?.hoveredPhase ?? null}
          hoveredValue={hoverInfo?.hoveredValue ?? null}
          pixelX={hoverInfo?.mirrorPixelX ?? null}
          chartHeight={CHART_HEIGHT - X_AXIS_HEIGHT}
          containerWidth={containerWidth}
        />
      </div>
    </Card>
  );
}
