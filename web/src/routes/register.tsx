import { createFileRoute } from "@tanstack/react-router";

import { RegisterPage } from "@/features/auth/register-page";
import { pageMeta } from "@/lib/seo";

export const Route = createFileRoute("/register")({
  head: () => ({
    meta: pageMeta("Create account", "Create a member account for the document workspace."),
  }),
  component: RegisterPage,
});
