import { createFileRoute } from "@tanstack/react-router";

import { useSuspenseQuery } from "@tanstack/react-query";

import { indexQueryOptions } from "@/features/index/index-queries";
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
  loader: ({ context }) =>
    Promise.all([
      context.queryClient.ensureQueryData(initialSearchQueryOptions()),
      context.queryClient.ensureQueryData(indexQueryOptions()),
    ]),
  component: SearchRoute,
});

function SearchRoute() {
  const index = useSuspenseQuery(indexQueryOptions());
  return <SearchPage indexReady={index.data.serving_build_id != null} />;
}
