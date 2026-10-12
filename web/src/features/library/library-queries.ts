import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";

import { libraryService } from "@/services/library-service";

export const libraryQueryOptions = () =>
  queryOptions({ queryKey: ["documents"], queryFn: () => libraryService.list() });

export function useUploadDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: libraryService.upload,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: libraryQueryOptions().queryKey }),
  });
}

export function useDeleteDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: libraryService.remove,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: libraryQueryOptions().queryKey }),
  });
}
