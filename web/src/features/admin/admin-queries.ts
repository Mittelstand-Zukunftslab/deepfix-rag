import { queryOptions } from "@tanstack/react-query";

import { optimizationService, type OptimizationStatus } from "@/services/optimization-service";

export function optimizationPollInterval(runs: { status: string }[] | undefined): number | false {
  return runs?.some((run) => run.status === "running") ? 5000 : false;
}

export const optimizationQueryOptions = () =>
  queryOptions({
    queryKey: ["optimization"],
    queryFn: () => optimizationService.status(),
    refetchInterval: (query) => optimizationPollInterval(query.state.data?.runs),
  });

export type { OptimizationStatus };
