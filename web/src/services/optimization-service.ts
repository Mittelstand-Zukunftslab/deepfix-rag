import {
  getOptimizationStatus,
  startOptimizationRun,
  updateOptimizationSchedule,
  type OptimizationRunResponse,
  type OptimizationStatusResponse,
} from "@/client";

export type OptimizationStatus = OptimizationStatusResponse;

export class OptimizationRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "OptimizationRequestError";
  }
}

function errorDetail(error: unknown): string {
  if (
    error &&
    typeof error === "object" &&
    "detail" in error &&
    typeof error.detail === "string" &&
    error.detail.length > 0
  ) {
    return error.detail;
  }
  return "Unable to reach the server. Try again.";
}

async function unwrap<T>(result: { data?: T; error?: unknown }): Promise<T> {
  if (result.error || result.data === undefined) {
    throw new OptimizationRequestError(errorDetail(result.error));
  }
  return result.data;
}

export const optimizationService = {
  async status(): Promise<OptimizationStatus> {
    try {
      return await unwrap(await getOptimizationStatus());
    } catch (error) {
      if (error instanceof OptimizationRequestError) throw error;
      throw new OptimizationRequestError("Unable to reach the server. Try again.");
    }
  },

  async update(input: { cron: string; enabled: boolean }): Promise<OptimizationStatus> {
    try {
      return await unwrap(await updateOptimizationSchedule({ body: input }));
    } catch (error) {
      if (error instanceof OptimizationRequestError) throw error;
      throw new OptimizationRequestError("Unable to reach the server. Try again.");
    }
  },

  async runNow(): Promise<OptimizationRunResponse> {
    try {
      return await unwrap(await startOptimizationRun({ body: {} }));
    } catch (error) {
      if (error instanceof OptimizationRequestError) throw error;
      throw new OptimizationRequestError("Unable to reach the server. Try again.");
    }
  },
};
