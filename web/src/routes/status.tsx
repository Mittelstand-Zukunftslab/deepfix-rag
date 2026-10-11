import { createFileRoute } from "@tanstack/react-router";

import { StatusPage } from "@/features/status/status-page";
import { pageMeta } from "@/lib/seo";

export const Route = createFileRoute("/status")({
  head: () => ({
    meta: pageMeta("System status", "Connection status of the DeepFix API."),
  }),
  component: StatusPage,
});
