import { queryOptions, useMutation } from "@tanstack/react-query";

import type { MetricsRange } from "@/schemas/metrics";
import { metricsService } from "@/services/metrics-service";

export const engineMetricsQueryOptions = (range: MetricsRange) =>
  queryOptions({
    queryKey: ["engine-metrics", range],
    queryFn: () => metricsService.getEngineMetrics(range),
  });

export function useTriggerOptimization() {
  return useMutation({ mutationFn: metricsService.triggerOptimization });
}
