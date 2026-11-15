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
    <section
      role="group"
      aria-label={`Run ${run.run_id}`}
      className="flex flex-col gap-3 rounded-2xl border border-border bg-muted/30 p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-mono text-label-md text-foreground">Run {run.run_id}</h3>
        <span className="rounded-full border border-border px-2 py-0.5 text-label-sm uppercase text-muted-foreground">
          {run.status}
        </span>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-label-md">
        <dt className="text-muted-foreground">Started</dt>
        <dd>{formatDateTime(run.started_at)}</dd>
        <dt className="text-muted-foreground">Reason</dt>
        <dd>{run.reason ?? "—"}</dd>
        <dt className="text-muted-foreground">Dev delta</dt>
        <dd className="tabular-nums">{delta(run.dev_delta)}</dd>
        <dt className="text-muted-foreground">Holdout delta</dt>
        <dd className="tabular-nums">{delta(run.holdout_delta)}</dd>
        <dt className="text-muted-foreground">Holdout wins / losses</dt>
        <dd className="tabular-nums">
          {run.holdout_wins ?? "—"} / {run.holdout_losses ?? "—"}
        </dd>
      </dl>
      {run.mlflow_url ? (
        <a
          className="text-label-md text-primary underline underline-offset-2"
          href={run.mlflow_url}
        >
          Open in MLflow
        </a>
      ) : null}
      {run.steps.length ? (
        <pre className="max-h-48 overflow-auto rounded-lg border border-border bg-background p-2 text-label-md">
          {run.steps.join("\n")}
        </pre>
      ) : (
        <p className="text-label-md text-muted-foreground">No steps yet.</p>
      )}
    </section>
  );
}
