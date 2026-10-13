import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import { PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDateTime } from "@/lib/format";
import type { OptimizationStatus } from "@/services/optimization-service";

const AGENT_UNAVAILABLE =
  "OpenHands is not set up on this server, so a run cannot start. The schedule and history are still shown.";

const STATUS_LABEL: Record<OptimizationStatus["runs"][number]["status"], string> = {
  running: "Running",
  skipped: "Skipped",
  rejected: "Rejected",
  promoted: "Promoted",
  succeeded: "Succeeded",
  failed: "Failed",
};

const TRIGGER_LABEL = { cron: "Scheduled", manual: "Manual" } as const;

export function OptimizationDenied() {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-3 px-8 py-10">
      <h1 className="text-headline-md text-foreground">Optimization</h1>
      <p>This page is only available to admins.</p>
    </div>
  );
}

export function OptimizationPage({
  status,
  onRun,
  onSave,
}: {
  status: OptimizationStatus;
  onRun: () => Promise<void>;
  onSave: (cron: string, enabled: boolean) => Promise<void>;
}) {
  const running = status.runs.some((run) => run.status === "running");
  const [cron, setCron] = useState(status.cron);
  const [enabled, setEnabled] = useState(status.enabled);
  const [dirty, setDirty] = useState(false);
  const [scheduleError, setScheduleError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    if (dirty) return;
    setCron(status.cron);
    setEnabled(status.enabled);
  }, [dirty, status.cron, status.enabled]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setScheduleError(null);
    setSaving(true);
    try {
      await onSave(cron.trim(), enabled);
      setDirty(false);
    } catch (error) {
      setScheduleError(
        error instanceof Error ? error.message : "Enter five cron fields, such as 0 2 * * *.",
      );
    } finally {
      setSaving(false);
    }
  };

  const run = async () => {
    setRunError(null);
    setStarting(true);
    try {
      await onRun();
    } catch (error) {
      setRunError(
        error instanceof Error ? error.message : "Unable to reach the server. Try again.",
      );
    } finally {
      setStarting(false);
    }
  };

  const changes = Object.entries(status.champion_diff);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-8 px-8 py-10">
      <PageHeader
        title="Optimization"
        description="The nightly schedule, the current champion, and recent runs."
        actions={
          <div className="flex flex-col items-start gap-2">
            <Button
              type="button"
              onClick={run}
              disabled={!status.agent_available || running || starting}
            >
              Run now
            </Button>
            {running ? <p role="status">A run is already in progress.</p> : null}
            {runError ? (
              <p role="alert" className="text-sm font-medium text-destructive">
                {runError}
              </p>
            ) : null}
          </div>
        }
      />

      {status.agent_available ? null : <p>{AGENT_UNAVAILABLE}</p>}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="rounded-2xl shadow-soft">
          <CardHeader>
            <CardTitle className="text-headline-md">Schedule</CardTitle>
          </CardHeader>
          <CardContent>
            <form className="flex flex-col gap-4" onSubmit={save}>
              <div className="flex flex-col gap-2">
                <Label htmlFor="schedule">Schedule</Label>
                <Input
                  id="schedule"
                  name="cron"
                  value={cron}
                  spellCheck={false}
                  autoComplete="off"
                  aria-invalid={scheduleError ? true : undefined}
                  aria-describedby={scheduleError ? "schedule-error" : "schedule-hint"}
                  className="font-mono"
                  onChange={(event) => {
                    setCron(event.target.value);
                    setDirty(true);
                    setScheduleError(null);
                  }}
                />
                <p id="schedule-hint" className="text-sm text-muted-foreground">
                  Five fields: minute, hour, day, month, and weekday. 0 2 * * * runs at 02:00.
                </p>
                {scheduleError ? (
                  <p
                    id="schedule-error"
                    role="alert"
                    className="text-sm font-medium text-destructive"
                  >
                    {scheduleError}
                  </p>
                ) : null}
              </div>
              <div className="flex items-center justify-between gap-4">
                <Label htmlFor="schedule-enabled">Enabled</Label>
                <Switch
                  id="schedule-enabled"
                  checked={enabled}
                  onCheckedChange={(checked) => {
                    setEnabled(checked);
                    setDirty(true);
                  }}
                />
              </div>
              <Button type="submit" disabled={saving}>
                Save
              </Button>
            </form>
            <p className="mt-4 text-sm text-muted-foreground">
              {status.enabled && status.next_run_at
                ? `Next run ${formatDateTime(status.next_run_at)} (${status.timezone}).`
                : "Paused. Run now still starts a job."}
            </p>
          </CardContent>
        </Card>

        <Card className="rounded-2xl shadow-soft">
          <CardHeader>
            <CardTitle className="text-headline-md">Champion</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <p className="break-all font-mono text-sm">{status.champion_config_path}</p>
            {changes.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No differences from the previous champion.
              </p>
            ) : (
              <ul className="flex flex-col gap-2 text-sm">
                {changes.map(([key, change]) => (
                  <li key={key}>
                    <span className="font-mono">{key}</span> {formatValue(change.before)} to{" "}
                    {formatValue(change.after)}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="rounded-2xl shadow-soft">
        <CardHeader>
          <CardTitle className="text-headline-md">Recent runs</CardTitle>
        </CardHeader>
        <CardContent>
          {status.runs.length === 0 ? (
            <p>No optimization runs yet.</p>
          ) : (
            <Table>
              <TableCaption className="sr-only">Recent optimization runs</TableCaption>
              <TableHeader>
                <TableRow>
                  <TableHead>Status</TableHead>
                  <TableHead>Trigger</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead>Ended</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead>Dev</TableHead>
                  <TableHead>Holdout</TableHead>
                  <TableHead>Holdout questions</TableHead>
                  <TableHead>MLflow</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {status.runs.map((run) => (
                  <TableRow key={run.run_id}>
                    <TableCell>{STATUS_LABEL[run.status]}</TableCell>
                    <TableCell>{TRIGGER_LABEL[run.trigger]}</TableCell>
                    <TableCell>{formatDateTime(run.started_at)}</TableCell>
                    <TableCell>{run.ended_at ? formatDateTime(run.ended_at) : "—"}</TableCell>
                    <TableCell>{run.reason ?? "—"}</TableCell>
                    <TableCell>{formatDelta(run.dev_delta, run.primary_metric)}</TableCell>
                    <TableCell>{formatDelta(run.holdout_delta, run.primary_metric)}</TableCell>
                    <TableCell>{formatWins(run.holdout_wins, run.holdout_losses)}</TableCell>
                    <TableCell>
                      {run.mlflow_url ? (
                        <a
                          href={run.mlflow_url}
                          target="_blank"
                          rel="noreferrer"
                          aria-label={`Open the MLflow run ${run.mlflow_run_id ?? ""}`.trim()}
                        >
                          MLflow
                        </a>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function formatDelta(delta: { [key: string]: number } | null, metric: string | null): string {
  if (!delta || !metric || typeof delta[metric] !== "number") return "—";
  const value = delta[metric];
  const sign = value >= 0 ? "+" : "";
  return `${metric} ${sign}${value.toFixed(3)}`;
}

function formatWins(wins: number | null, losses: number | null): string {
  if (wins === null || losses === null) return "—";
  return `${wins} won, ${losses} lost`;
}

function formatValue(value: string | number | boolean | null): ReactNode {
  if (value === null) return "empty";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}
