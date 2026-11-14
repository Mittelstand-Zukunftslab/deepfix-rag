import { useQuery, useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

import { HistoryDenied, HistoryReviewPage } from "@/features/admin/history-page";
import {
  historyQueryOptions,
  interactionReviewQueryOptions,
} from "@/features/admin/history-queries";
import { pageMeta } from "@/lib/seo";
import { historySearchSchema, type HistoryFilters } from "@/schemas/history";
import { messageOf } from "@/services/api-result";

export const Route = createFileRoute("/history")({
  validateSearch: historySearchSchema,
  loaderDeps: ({ search: { page, kind, feedback, q } }) => ({ page, kind, feedback, q }),
  head: () => ({
    meta: pageMeta("History", "Every search and question with its answer and feedback."),
  }),
  loader: ({ context, deps }) => {
    if (context.currentUser?.role !== "admin") return null;
    return context.queryClient.ensureQueryData(historyQueryOptions(deps));
  },
  component: HistoryRoute,
});

function HistoryRoute() {
  const { currentUser } = Route.useRouteContext();
  if (currentUser?.role !== "admin") return <HistoryDenied />;
  return <HistoryAdmin />;
}

function HistoryAdmin() {
  const { id, ...filters } = Route.useSearch();
  const navigate = Route.useNavigate();
  const page = useSuspenseQuery(historyQueryOptions(filters));
  const review = useQuery({ ...interactionReviewQueryOptions(id ?? ""), enabled: Boolean(id) });

  return (
    <HistoryReviewPage
      page={page.data}
      filters={filters}
      selectedId={id}
      review={{
        data: review.data,
        error: review.error ? messageOf(review.error) : undefined,
        loading: review.isPending,
      }}
      onFiltersChange={(next: HistoryFilters) => void navigate({ search: next })}
      onSelect={(next) => void navigate({ search: { ...filters, id: next } })}
    />
  );
}
