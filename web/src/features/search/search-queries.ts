import { queryOptions } from "@tanstack/react-query";

import type { SearchFilters } from "@/schemas/document";
import { searchService } from "@/services/search-service";
import { useSearchStore } from "@/stores/search-store";

export const searchQueryOptions = (filters: SearchFilters) =>
  queryOptions({
    queryKey: ["search", filters],
    queryFn: () => searchService.search(filters),
  });

export const initialSearchQueryOptions = () =>
  searchQueryOptions(useSearchStore.getState().filters);
