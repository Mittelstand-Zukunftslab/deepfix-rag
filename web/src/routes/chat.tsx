import { createFileRoute } from "@tanstack/react-router";

import { useSuspenseQuery } from "@tanstack/react-query";

import { ChatPage } from "@/features/chat/chat-page";
import { conversationQueryOptions } from "@/features/chat/chat-queries";
import { indexQueryOptions } from "@/features/index/index-queries";
import { pageMeta } from "@/lib/seo";

export const Route = createFileRoute("/chat")({
  head: () => ({
    meta: pageMeta("Chat", "Ask questions about your documents and get sourced answers."),
  }),
  loader: ({ context }) =>
    Promise.all([
      context.queryClient.ensureQueryData(conversationQueryOptions()),
      context.queryClient.ensureQueryData(indexQueryOptions()),
    ]),
  component: ChatRoute,
});

function ChatRoute() {
  const index = useSuspenseQuery(indexQueryOptions());
  return <ChatPage indexReady={index.data.serving_build_id != null} />;
}
