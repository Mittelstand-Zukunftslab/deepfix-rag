import { createFileRoute, redirect } from "@tanstack/react-router";

import { pageMeta } from "@/lib/seo";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: pageMeta(
      "Smart document search",
      "Enterprise smart document search, chat and analytics.",
    ),
  }),
  beforeLoad: () => {
    throw redirect({ to: "/search" });
  },
});
