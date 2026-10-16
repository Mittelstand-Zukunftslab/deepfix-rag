import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";

import {
  getOptimizationStatus,
  startOptimizationRun,
  updateOptimizationSchedule,
  type OptimizationRunResponse,
  type OptimizationStatusResponse,
} from "@/client";
import { requireRole } from "@/lib/auth-middleware";
import { unwrap } from "@/services/api-result";

const adminOnly = (action: string) => requireRole("admin", `Only admins can ${action}.`);

export const fetchOptimizationStatus = createServerFn({ method: "GET" })
  .middleware([adminOnly("view optimization")])
  .handler(async ({ context }): Promise<OptimizationStatusResponse> => {
    return unwrap(await getOptimizationStatus({ headers: context.apiHeaders }));
  });

const scheduleInput = z.object({ cron: z.string(), enabled: z.boolean() });

export const saveOptimizationSchedule = createServerFn({ method: "POST" })
  .middleware([adminOnly("change the optimization schedule")])
  .validator(scheduleInput)
  .handler(async ({ context, data }): Promise<OptimizationStatusResponse> => {
    return unwrap(await updateOptimizationSchedule({ body: data, headers: context.apiHeaders }));
  });

export const runOptimizationNow = createServerFn({ method: "POST" })
  .middleware([adminOnly("start an optimization run")])
  .handler(async ({ context }): Promise<OptimizationRunResponse> => {
    return unwrap(await startOptimizationRun({ body: {}, headers: context.apiHeaders }));
  });
