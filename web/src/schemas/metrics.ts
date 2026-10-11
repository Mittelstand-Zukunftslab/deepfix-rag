import { z } from "zod";

export const metricsRangeSchema = z.enum(["30d", "90d", "all"]);
export type MetricsRange = z.infer<typeof metricsRangeSchema>;

export const engineMetricsSchema = z.object({
  mrr: z.number(),
  precisionAt1: z.number(),
  cycle: z.number(),
  signals: z.number(),
  positiveRate: z.number(),
  threshold: z.number(),
  lastOptimizationHours: z.number(),
  bufferGathered: z.number(),
  hoursRemaining: z.number(),
  series: z.array(
    z.object({
      label: z.string(),
      mrr: z.number(),
      baseline: z.number(),
      round: z.string().optional(),
    }),
  ),
});
export type EngineMetrics = z.infer<typeof engineMetricsSchema>;
