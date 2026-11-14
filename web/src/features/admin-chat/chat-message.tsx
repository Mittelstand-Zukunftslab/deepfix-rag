import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

/**
 * One message. An assistant message renders markdown and code; a user message
 * is plain text, never HTML, so nothing a user types can become markup.
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
      <div className={cn("rounded-lg bg-muted px-3 py-2 text-body-md whitespace-pre-wrap")}>
        <span className="sr-only">You said: </span>
        {content}
      </div>
    );
  }
  return (
    <div className={cn("prose prose-sm max-w-none text-body-md text-foreground")}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          pre: ({ children }) => (
            <pre className="overflow-x-auto rounded-md border bg-muted p-3 text-label-sm">
              {children}
            </pre>
          ),
          code: ({ children }) => <code className="text-label-sm">{children}</code>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
