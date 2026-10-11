import { createFileRoute } from "@tanstack/react-router";

import { initialSearchQueryOptions } from "@/features/search/search-queries";
import { SearchPage } from "@/features/search/search-page";
import { pageMeta } from "@/lib/seo";

export const Route = createFileRoute("/search")({
  head: () => ({
    meta: pageMeta(
      "Search",
      "Find invoices, statements and contracts with natural-language search.",
    ),
  }),
  loader: ({ context }) => context.queryClient.ensureQueryData(initialSearchQueryOptions()),
  component: SearchPage,
});
