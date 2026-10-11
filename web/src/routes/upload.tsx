import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

import { LibraryPage } from "@/features/library/library-page";
import { libraryQueryOptions } from "@/features/library/library-queries";
import { pageMeta } from "@/lib/seo";

export const Route = createFileRoute("/upload")({
  head: () => ({
    meta: pageMeta("Library", "Upload a text, Markdown, or PDF file into the shared corpus."),
  }),
  loader: ({ context }) => context.queryClient.ensureQueryData(libraryQueryOptions()),
  component: LibraryRoute,
});

function LibraryRoute() {
  const { data } = useSuspenseQuery(libraryQueryOptions());
  return <LibraryPage documents={data} />;
}
