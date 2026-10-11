import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";

import { uploadService } from "@/services/upload-service";

export const uploadsQueryOptions = () =>
  queryOptions({ queryKey: ["uploads"], queryFn: uploadService.list });

export function useUploadFiles() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: uploadService.upload,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: uploadsQueryOptions().queryKey }),
  });
}

export function useRemoveUpload() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: uploadService.remove,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: uploadsQueryOptions().queryKey }),
  });
}
