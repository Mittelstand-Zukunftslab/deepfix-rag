import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

beforeAll(() => {
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
});

import { OptimizationDenied, OptimizationPage } from "@/features/admin/admin-page";
import { optimizationPollInterval } from "@/features/admin/admin-queries";
import type { OptimizationStatus } from "@/services/optimization-service";

function status(overrides: Partial<OptimizationStatus> = {}): OptimizationStatus {
  return {
    agent_available: true,
    champion_config_path: "/workspace/configs/champion.yaml",
    champion_diff: {},
    cron: "0 2 * * *",
    enabled: true,
    next_run_at: "2026-10-13T00:00:00.000Z",
    runs: [],
    timezone: "Europe/Berlin",
    ...overrides,
  };
}

const skippedRun: OptimizationStatus["runs"][number] = {
  run_id: "run-skipped",
  status: "skipped",
  trigger: "manual",
  started_at: "2026-10-12T12:00:00.000Z",
  ended_at: "2026-10-12T12:00:01.000Z",
  reason: "Skipped: 14 labeled questions in dev, 50 needed.",
  primary_metric: "ndcg",
  dev_delta: { ndcg: 0 },
  holdout_delta: null,
  holdout_wins: null,
  holdout_losses: null,
  mlflow_run_id: null,
  mlflow_url: null,
};

describe("optimization poll interval", () => {
  it("polls every 5 seconds only while a run is running", () => {
    expect(optimizationPollInterval(undefined)).toBe(false);
    expect(optimizationPollInterval([])).toBe(false);
    expect(optimizationPollInterval([{ status: "skipped" }])).toBe(false);
    expect(optimizationPollInterval([{ status: "running" }])).toBe(5000);
  });
});

describe("Optimization screen", () => {
  it("tells members the page is only for admins and shows no controls", () => {
    render(<OptimizationDenied />);
    expect(screen.getByRole("heading", { name: "Optimization" })).toBeTruthy();
    expect(screen.getByText("This page is only available to admins.")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("disables Run now when Hermes is not set up and still shows the schedule", () => {
    render(
      <OptimizationPage
        status={status({ agent_available: false })}
        onRun={async () => {}}
        onSave={async () => {}}
      />,
    );
    expect(
      screen.getByText(
        "Hermes is not set up on this server, so a run cannot start. The schedule and history are still shown.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Run now" })).toBeDisabled();
    expect(screen.getByLabelText("Schedule")).toHaveValue("0 2 * * *");
    expect(screen.getByRole("switch", { name: "Enabled" })).toBeTruthy();
    expect(screen.getByText("No optimization runs yet.")).toBeTruthy();
    expect(screen.getByText("No differences from the previous champion.")).toBeTruthy();
  });

  it("disables Run now and notes that a run is already in progress", () => {
    render(
      <OptimizationPage
        status={status({
          runs: [
            { ...skippedRun, run_id: "run-live", status: "running", ended_at: null, reason: null },
          ],
        })}
        onRun={async () => {}}
        onSave={async () => {}}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("A run is already in progress.");
    expect(screen.getByRole("button", { name: "Run now" })).toBeDisabled();
  });

  it("shows a skipped reason with the actual counts", () => {
    render(
      <OptimizationPage
        status={status({ runs: [skippedRun] })}
        onRun={async () => {}}
        onSave={async () => {}}
      />,
    );
    expect(screen.getByText("Skipped: 14 labeled questions in dev, 50 needed.")).toBeTruthy();
    expect(screen.getByText("Skipped")).toBeTruthy();
    expect(screen.getByText("Manual")).toBeTruthy();
    expect(screen.getByText("ndcg +0.000")).toBeTruthy();
  });

  it("renders the champion path and the diff", () => {
    render(
      <OptimizationPage
        status={status({
          champion_diff: { "retrieval.top_k": { before: 1, after: 5 } },
        })}
        onRun={async () => {}}
        onSave={async () => {}}
      />,
    );
    expect(screen.getByText("/workspace/configs/champion.yaml")).toBeTruthy();
    expect(screen.getByRole("listitem")).toHaveTextContent("retrieval.top_k 1 to 5");
  });

  it("shows an invalid schedule under the field", async () => {
    const onSave = vi.fn(async () => {
      throw new Error("Enter five cron fields, such as 0 2 * * *.");
    });
    render(<OptimizationPage status={status()} onRun={async () => {}} onSave={onSave} />);
    fireEvent.change(screen.getByLabelText("Schedule"), { target: { value: "not a cron" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Enter five cron fields, such as 0 2 * * *.");
    expect(alert).toHaveAttribute("id", "schedule-error");
    expect(screen.getByLabelText("Schedule")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Schedule")).toHaveValue("not a cron");
    expect(onSave).toHaveBeenCalledWith("not a cron", true);
  });

  it("shows the saved expression and the scheduler's next run", async () => {
    const saved = status({ cron: "15 3 * * *", next_run_at: "2026-10-13T01:15:00.000Z" });
    const onSave = vi.fn(async () => {});
    const view = render(
      <OptimizationPage status={status()} onRun={async () => {}} onSave={onSave} />,
    );
    fireEvent.change(screen.getByLabelText("Schedule"), { target: { value: "15 3 * * *" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith("15 3 * * *", true));
    view.rerender(<OptimizationPage status={saved} onRun={async () => {}} onSave={onSave} />);
    await waitFor(() => expect(screen.getByLabelText("Schedule")).toHaveValue("15 3 * * *"));
    expect(screen.getByText(/Next run .+ \(Europe\/Berlin\)\./)).toBeTruthy();
  });

  it("keeps Run now available while the schedule is paused", async () => {
    const onSave = vi.fn(async () => {});
    const onRun = vi.fn(async () => {});
    render(
      <OptimizationPage
        status={status({ enabled: false, next_run_at: null })}
        onRun={onRun}
        onSave={onSave}
      />,
    );
    expect(screen.getByText("Paused. Run now still starts a job.")).toBeTruthy();
    const run = screen.getByRole("button", { name: "Run now" });
    expect(run).toBeEnabled();
    fireEvent.click(run);
    await waitFor(() => expect(onRun).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("switch", { name: "Enabled" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith("0 2 * * *", true));
  });
});
