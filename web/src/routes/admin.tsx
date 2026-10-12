import { useSuspenseQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

import { OptimizationDenied, OptimizationPage } from "@/features/admin/admin-page";
import { optimizationQueryOptions } from "@/features/admin/admin-queries";
import { pageMeta } from "@/lib/seo";
import { optimizationService } from "@/services/optimization-service";

export const Route = createFileRoute("/admin")({
  head: () => ({
    meta: pageMeta("Optimization", "The nightly schedule, the current champion, and recent runs."),
  }),
  loader: ({ context }) => {
    if (context.currentUser?.role !== "admin") return null;
    return context.queryClient.ensureQueryData(optimizationQueryOptions());
  },
  component: OptimizationRoute,
});

function OptimizationRoute() {
  const { currentUser } = Route.useRouteContext();
  if (currentUser?.role !== "admin") return <OptimizationDenied />;
  return <OptimizationAdmin />;
}

function OptimizationAdmin() {
  const status = useSuspenseQuery(optimizationQueryOptions());
  const queryClient = useQueryClient();
  return (
    <OptimizationPage
      status={status.data}
      onRun={async () => {
        await optimizationService.runNow();
        await queryClient.invalidateQueries({ queryKey: optimizationQueryOptions().queryKey });
      }}
      onSave={async (cron, enabled) => {
        const next = await optimizationService.update({ cron, enabled });
        queryClient.setQueryData(optimizationQueryOptions().queryKey, next);
      }}
    />
  );
}
