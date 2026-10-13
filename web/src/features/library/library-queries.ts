import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";

import { libraryService } from "@/services/library-service";

export const libraryQueryOptions = () =>
  queryOptions({
    queryKey: ["documents"],
    queryFn: () => libraryService.list(),
    refetchInterval: (query) => {
      const documents = query.state.data;
      const busy = documents?.some(
        (document) =>
          document.status === "pending" ||
          document.status === "indexing" ||
          document.status === "deleting",
      );
      return busy ? 3000 : false;
    },
  });

export function useUploadDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: libraryService.upload,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: libraryQueryOptions().queryKey });
      queryClient.invalidateQueries({ queryKey: ["index"] });
    },
  });
}

export function useDeleteDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: libraryService.remove,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: libraryQueryOptions().queryKey });
      queryClient.invalidateQueries({ queryKey: ["index"] });
    },
  });
}
