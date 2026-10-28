import type { OptimizationRunResponse, OptimizationStatusResponse } from "@/client";
import {
  fetchOptimizationStatus,
  runOptimizationNow,
  saveOptimizationSchedule,
} from "@/services/optimization.functions";

export type OptimizationStatus = OptimizationStatusResponse;

export class OptimizationRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "OptimizationRequestError";
  }
}

async function call<T>(request: () => Promise<T>): Promise<T> {
  try {
    return await request();
  } catch (error) {
    const message =
      error instanceof Error && error.message
        ? error.message
        : "Unable to reach the server. Try again.";
    throw new OptimizationRequestError(message);
  }
}

/** Optimization runs only through admin-checked server functions. */
export const optimizationService = {
  status(): Promise<OptimizationStatus> {
    return call(() => fetchOptimizationStatus());
  },

  update(input: { cron: string; enabled: boolean }): Promise<OptimizationStatus> {
    return call(() => saveOptimizationSchedule({ data: input }));
  },

  runNow(): Promise<OptimizationRunResponse> {
    return call(() => runOptimizationNow());
  },
};
