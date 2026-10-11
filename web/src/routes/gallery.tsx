import { createFileRoute } from "@tanstack/react-router";

import { initialGalleryQueryOptions } from "@/features/gallery/gallery-queries";
import { GalleryPage } from "@/features/gallery/gallery-page";
import { pageMeta } from "@/lib/seo";

export const Route = createFileRoute("/gallery")({
  head: () => ({
    meta: pageMeta("Data visualization", "Visual thumbnail catalog of every indexed document."),
  }),
  loader: ({ context }) => context.queryClient.ensureQueryData(initialGalleryQueryOptions()),
  component: GalleryPage,
});
