import { Bot, User } from "lucide-react";

import { ChatMarkdown } from "@/components/chat/markdown";
import { ASSISTANT_NAME } from "@/features/admin-chat/chat-header";
import { cn } from "@/lib/utils";

/**
 * One message.
 *
 * The assistant's answer is prose in the column, with no bubble: it is the
 * reading surface, and a bubble around a long markdown answer only adds a second
 * edge to scan. A user message is a small right-aligned bubble, and it is plain
 * text, never markdown, so nothing a user types can become markup. Both render
 * through the Ask page's `ChatMarkdown` (CHAT-016) for the assistant half.
 */
export function ChatMessageView({
  role,
  content,
}: {
  role: "user" | "assistant";
  content: string;
}) {
  if (role === "user") {
    return (
      <div className="flex max-w-[85%] items-start justify-end gap-3">
        <div className="rounded-2xl rounded-tr-sm bg-muted px-4 py-2.5 text-body-md whitespace-pre-wrap text-foreground">
          <span className="sr-only">You said: </span>
          {content}
        </div>
        <span
          aria-hidden
          className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-secondary text-muted-foreground"
        >
          <User className="size-3.5" />
        </span>
      </div>
    );
  }
  return (
    <div className="flex items-start gap-3">
      <span
        aria-hidden
        className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary"
      >
        <Bot className="size-3.5" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="mb-1 text-label-sm uppercase text-muted-foreground">{ASSISTANT_NAME}</p>
        <div className={cn("text-body-md")}>
          <ChatMarkdown prose={false}>{content}</ChatMarkdown>
        </div>
      </div>
    </div>
  );
}
