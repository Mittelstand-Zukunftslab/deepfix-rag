import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import type { ChatRun } from "@/schemas/admin-chat";

function delta(value: Record<string, number> | null | undefined): string {
  if (!value || Object.keys(value).length === 0) return "—";
  return Object.entries(value)
    .map(([metric, change]) => `${metric} ${change >= 0 ? "+" : ""}${change.toFixed(3)}`)
    .join(", ");
}

/** One linked optimization run: status, deltas, the MLflow link, and the step tail. */
export function RunPanel({ run }: { run: ChatRun }) {
  return (
    <Card role="group" aria-label={`Run ${run.run_id}`}>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="text-label-lg">Run {run.run_id}</CardTitle>
        <span className="text-label-sm text-muted-foreground">{run.status}</span>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-label-sm">
          <dt className="text-muted-foreground">Started</dt>
          <dd>{formatDateTime(run.started_at)}</dd>
          <dt className="text-muted-foreground">Reason</dt>
          <dd>{run.reason ?? "—"}</dd>
          <dt className="text-muted-foreground">Dev delta</dt>
          <dd>{delta(run.dev_delta)}</dd>
          <dt className="text-muted-foreground">Holdout delta</dt>
          <dd>{delta(run.holdout_delta)}</dd>
          <dt className="text-muted-foreground">Holdout wins / losses</dt>
          <dd>
            {run.holdout_wins ?? "—"} / {run.holdout_losses ?? "—"}
          </dd>
        </dl>
        {run.mlflow_url ? (
          <a className="text-label-sm underline" href={run.mlflow_url}>
            Open in MLflow
          </a>
        ) : null}
        {run.steps.length ? (
          <pre className="max-h-48 overflow-auto rounded-md border bg-muted p-2 text-label-sm">
            {run.steps.join("\n")}
          </pre>
        ) : (
          <p className="text-label-sm text-muted-foreground">No steps yet.</p>
        )}
      </CardContent>
    </Card>
  );
}
