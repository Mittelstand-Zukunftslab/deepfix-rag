import { queryOptions } from "@tanstack/react-query";

import { health } from "@/client";

export const healthQueryOptions = () =>
  queryOptions({
    queryKey: ["health"],
    queryFn: async () => {
      const { data } = await health({ throwOnError: true });
      return data;
    },
    retry: false,
  });
