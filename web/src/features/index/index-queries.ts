import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";

import { indexService, type IndexStatus } from "@/services/index-service";

export const indexQueryOptions = () =>
  queryOptions({
    queryKey: ["index"],
    queryFn: () => indexService.status(),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "rebuilding" || status === "updating" ? 3000 : false;
    },
  });

export function useRebuildIndex() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => indexService.rebuild(),
    onSuccess: (status: IndexStatus) => {
      queryClient.setQueryData(indexQueryOptions().queryKey, status);
    },
  });
}
