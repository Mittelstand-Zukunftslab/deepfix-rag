import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

import { indexQueryOptions } from "@/features/index/index-queries";
import { LibraryPage } from "@/features/library/library-page";
import { libraryQueryOptions } from "@/features/library/library-queries";
import { pageMeta } from "@/lib/seo";

export const Route = createFileRoute("/upload")({
  head: () => ({
    meta: pageMeta("Library", "Upload a text, Markdown, or PDF file into the shared corpus."),
  }),
  loader: ({ context }) =>
    Promise.all([
      context.queryClient.ensureQueryData(libraryQueryOptions()),
      context.queryClient.ensureQueryData(indexQueryOptions()),
    ]),
  component: LibraryRoute,
});

function LibraryRoute() {
  const { currentUser } = Route.useRouteContext();
  const documents = useSuspenseQuery(libraryQueryOptions());
  const index = useSuspenseQuery(indexQueryOptions());
  return (
    <LibraryPage
      documents={documents.data}
      index={index.data}
      canRebuild={currentUser?.role === "admin"}
    />
  );
}
