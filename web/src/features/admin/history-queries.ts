import { keepPreviousData, queryOptions } from "@tanstack/react-query";

import type { HistoryFilters } from "@/schemas/history";
import { historyService } from "@/services/history-service";

export const historyQueryOptions = (filters: HistoryFilters) =>
  queryOptions({
    queryKey: ["history", filters],
    queryFn: () => historyService.list(filters),
    placeholderData: keepPreviousData,
  });

export const interactionReviewQueryOptions = (id: string) =>
  queryOptions({
    queryKey: ["history", "review", id],
    queryFn: () => historyService.review(id),
  });
