import { createFileRoute } from "@tanstack/react-router";

import { conversationQueryOptions } from "@/features/chat/chat-queries";
import { ChatPage } from "@/features/chat/chat-page";
import { pageMeta } from "@/lib/seo";

export const Route = createFileRoute("/chat")({
  head: () => ({
    meta: pageMeta("Chat", "Ask questions about your documents and get sourced answers."),
  }),
  loader: ({ context }) => context.queryClient.ensureQueryData(conversationQueryOptions()),
  component: ChatPage,
});
