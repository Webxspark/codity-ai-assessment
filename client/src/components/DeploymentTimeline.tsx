/**
 * Deployment timeline showing recent deployments and config changes.
 */
import { useState } from "react";
import { Chip, Button } from "@heroui/react";
import { format } from "date-fns";
import { GitCommit, Settings, FileCode, ArrowLeftRight } from "lucide-react";
import type { DeploymentLog, ConfigChangeLog } from "../types";
import { DeploymentComparisonChart } from "./DeploymentComparisonChart";

interface TimelineProps {
  deployments: DeploymentLog[];
  configChanges: ConfigChangeLog[];
}

type TimelineEvent = {
  type: "deployment" | "config_change";
  timestamp: string;
  data: DeploymentLog | ConfigChangeLog;
};

export function DeploymentTimeline({
  deployments,
  configChanges,
}: TimelineProps) {
  const [expandedDeployId, setExpandedDeployId] = useState<string | null>(null);
  const events: TimelineEvent[] = [
    ...deployments.map(
      (d) =>
        ({
          type: "deployment",
          timestamp: d.timestamp,
          data: d,
        }) as TimelineEvent
    ),
    ...configChanges.map(
      (c) =>
        ({
          type: "config_change",
          timestamp: c.timestamp,
          data: c,
        }) as TimelineEvent
    ),
  ].sort(
    (a, b) =>
      new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
  );

  if (events.length === 0) {
    return (
      <div className="text-sm text-muted text-center py-8">
        No recent events
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {events.map((event, i) => (
        <div key={i} className="flex gap-3">
          {/* Timeline line & dot */}
          <div className="flex flex-col items-center">
            <div
              className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${
                event.type === "deployment"
                  ? "bg-primary/10 text-primary"
                  : "bg-warning/10 text-warning"
              }`}
            >
              {event.type === "deployment" ? (
                <GitCommit size={14} />
              ) : (
                <Settings size={14} />
              )}
            </div>
            {i < events.length - 1 && (
              <div className="w-px flex-1 bg-border" />
            )}
          </div>

          {/* Content */}
          <div className="pb-4 flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs text-muted">
                {format(new Date(event.timestamp), "MMM d, HH:mm")}
              </span>
              <Chip
                size="sm"
                variant="soft"
                color={
                  event.type === "deployment" ? "accent" : "warning"
                }
              >
                {(event.data as DeploymentLog | ConfigChangeLog).service_name}
              </Chip>
            </div>
            {event.type === "deployment" ? (
              <div className="mt-1">
                <p className="text-sm text-foreground">
                  {(event.data as DeploymentLog).commit_message}
                </p>
                <div className="flex items-center gap-2 mt-1 text-xs text-muted">
                  <span className="font-mono">
                    {(event.data as DeploymentLog).commit_sha.slice(0, 8)}
                  </span>
                  <span>by {(event.data as DeploymentLog).author}</span>
                </div>
                {(event.data as DeploymentLog).changed_files && (
                  <div className="flex flex-wrap gap-1 mt-1.5">
                    {(event.data as DeploymentLog).changed_files!.map(
                      (file) => (
                        <Chip
                          key={file}
                          size="sm"
                          variant="secondary"
                        >
                          <FileCode size={10} />
                          {file.split("/").pop()}
                        </Chip>
                      )
                    )}
                  </div>
                )}
                {/* Before vs After comparison */}
                <Button
                  size="sm"
                  variant={
                    expandedDeployId === (event.data as DeploymentLog).id
                      ? "primary"
                      : "outline"
                  }
                  className="mt-2"
                  onPress={() => {
                    const id = (event.data as DeploymentLog).id;
                    setExpandedDeployId(
                      expandedDeployId === id ? null : id
                    );
                  }}
                >
                  <ArrowLeftRight size={12} />
                  {expandedDeployId === (event.data as DeploymentLog).id
                    ? "Hide Comparison"
                    : "Compare Before / After"}
                </Button>
                {expandedDeployId === (event.data as DeploymentLog).id && (
                  <div className="mt-3">
                    <DeploymentComparisonChart
                      deployment={event.data as DeploymentLog}
                    />
                  </div>
                )}
              </div>
            ) : (
              <div className="mt-1">
                <p className="text-sm text-foreground">
                  <span className="font-medium">
                    {(event.data as ConfigChangeLog).parameter}
                  </span>
                  {": "}
                  <span className="text-danger line-through">
                    {(event.data as ConfigChangeLog).old_value}
                  </span>
                  {" → "}
                  <span className="text-success">
                    {(event.data as ConfigChangeLog).new_value}
                  </span>
                </p>
                <p className="text-xs text-muted mt-0.5">
                  by {(event.data as ConfigChangeLog).changed_by}
                </p>
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
