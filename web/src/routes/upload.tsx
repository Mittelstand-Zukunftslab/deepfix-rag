import { createFileRoute } from "@tanstack/react-router";

import { uploadsQueryOptions } from "@/features/upload/upload-queries";
import { UploadPage } from "@/features/upload/upload-page";
import { pageMeta } from "@/lib/seo";

export const Route = createFileRoute("/upload")({
  head: () => ({
    meta: pageMeta("Data upload", "Upload PDFs and images to parse and index them."),
  }),
  loader: ({ context }) => context.queryClient.ensureQueryData(uploadsQueryOptions()),
  component: UploadPage,
});
