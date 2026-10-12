import { createFileRoute } from "@tanstack/react-router";

import { SignInPage } from "@/features/auth/sign-in-page";
import { pageMeta } from "@/lib/seo";

type SignInSearch = { created?: "1" };

export const Route = createFileRoute("/sign-in")({
  validateSearch: (search: Record<string, unknown>): SignInSearch => {
    if (search["created"] === "1" || search["created"] === 1) return { created: "1" };
    return {};
  },
  head: () => ({
    meta: pageMeta("Sign in", "Sign in to search the shared document workspace."),
  }),
  component: SignInRoute,
});

function SignInRoute() {
  const { created } = Route.useSearch();
  const notice = created === "1" ? "Account created. Sign in to continue." : null;
  return <SignInPage notice={notice} />;
}
