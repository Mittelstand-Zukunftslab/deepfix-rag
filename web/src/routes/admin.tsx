import { createFileRoute } from "@tanstack/react-router";

import { engineMetricsQueryOptions } from "@/features/admin/admin-queries";
import { AdminPage } from "@/features/admin/admin-page";
import { pageMeta } from "@/lib/seo";

export const Route = createFileRoute("/admin")({
  head: () => ({
    meta: pageMeta(
      "Engine administration",
      "Search accuracy, feedback signals and optimization controls.",
    ),
  }),
  loader: ({ context }) => context.queryClient.ensureQueryData(engineMetricsQueryOptions("30d")),
  component: AdminPage,
});
