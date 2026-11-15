import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

import { AskPage } from "@/features/chat/chat-page";
import { indexQueryOptions } from "@/features/index/index-queries";
import { pageMeta } from "@/lib/seo";

export const Route = createFileRoute("/chat")({
  head: () => ({
    meta: pageMeta("Ask", "Ask one question and read an answer grounded in the shared corpus."),
  }),
  loader: ({ context }) => context.queryClient.ensureQueryData(indexQueryOptions()),
  component: ChatRoute,
});

function ChatRoute() {
  const index = useSuspenseQuery(indexQueryOptions());
  // The conversations live on the server and are scoped to the signed-in member
  // by its token, so the page needs no owner id of its own (D-5).
  return <AskPage indexReady={index.data.serving_build_id != null} />;
}
