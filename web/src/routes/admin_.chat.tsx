import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

import { AdminChatDenied, AdminChatPage } from "@/features/admin-chat/admin-chat-page";
import { optimizationQueryOptions } from "@/features/admin/admin-queries";
import { pageMeta } from "@/lib/seo";

/**
 * `/admin/chat`. The `_` suffix keeps this out of `admin.tsx`, which renders no
 * `<Outlet />` and would otherwise swallow the child.
 *
 * The disabled state needs no chat request: `GET /admin/optimization` already
 * carries `chat_enabled`, so the page can say "turned off" before it touches a
 * chat route (FR-027).
 */
export const Route = createFileRoute("/admin_/chat")({
  head: () => ({
    meta: pageMeta(
      "Hermes chat",
      "Ask the DeepFix assistant about this server's runs and champion.",
    ),
  }),
  loader: ({ context }) => {
    if (context.currentUser?.role !== "admin") return null;
    return context.queryClient.ensureQueryData(optimizationQueryOptions());
  },
  component: AdminChatRoute,
});

function AdminChatRoute() {
  const { currentUser } = Route.useRouteContext();
  if (currentUser?.role !== "admin") return <AdminChatDenied />;
  return <AdminChatAdmin email={currentUser.email} />;
}

function AdminChatAdmin({ email }: { email: string }) {
  const status = useSuspenseQuery(optimizationQueryOptions());
  return <AdminChatPage chatEnabled={status.data.chat_enabled} email={email} />;
}
