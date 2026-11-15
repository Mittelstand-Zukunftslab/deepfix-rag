"use client";

import { Check, Copy } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard";
import { cn } from "@/lib/utils";

/**
 * The app's one markdown renderer for chat answers.
 *
 * Both the member-facing Ask page and the admin Hermes chat render model output
 * through this component, so a code block copies, a table scrolls, and a link
 * opens the same way in both. The prose wrapper is the default because the Ask
 * page's answer body has always been a `prose` block; the admin chat passes
 * `prose={false}` because its assistant messages are already spaced by the
 * message column.
 */
export function ChatMarkdown({
  children,
  prose = true,
  className,
}: {
  children: string;
  prose?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        prose && "prose prose-sm dark:prose-invert max-w-none text-body-md leading-relaxed",
        "text-foreground",
        className,
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
        {children}
      </ReactMarkdown>
    </div>
  );
}

function CodeBlock({ children, className }: { children?: React.ReactNode; className?: string }) {
  const { isCopied, copyToClipboard } = useCopyToClipboard();
  const language = /language-(\w+)/.exec(className ?? "")?.[1];
  const text = flatten(children);

  return (
    <div className="not-prose my-4 overflow-hidden rounded-xl border border-border">
      <div className="flex items-center justify-between gap-2 border-b border-border bg-muted/50 px-3 py-1.5">
        <span className="font-mono text-label-sm text-muted-foreground">{language ?? "text"}</span>
        <button
          type="button"
          aria-label={isCopied ? "Copied" : "Copy"}
          onClick={() => copyToClipboard(text)}
          className="flex items-center gap-1 rounded px-1.5 py-1 text-label-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
        >
          {isCopied ? <Check className="size-3" /> : <Copy className="size-3" />}
        </button>
      </div>
      <pre className="overflow-x-auto bg-muted/30 p-3 text-label-md leading-relaxed">
        <code className={className}>{children}</code>
      </pre>
    </div>
  );
}

/** The raw text of a rendered code block, for the copy button. */
function flatten(node: React.ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flatten).join("");
  if (node && typeof node === "object" && "props" in node) {
    const props = (node as { props?: { children?: React.ReactNode } }).props;
    return flatten(props?.children);
  }
  return "";
}

const MARKDOWN_COMPONENTS = {
  pre: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  code: ({ className, children }: { className?: string; children?: React.ReactNode }) => {
    // A fence arrives as `code` with a `language-*` class or a newline in it;
    // inline code is a bare `code` and stays inline.
    const fenced = Boolean(className) || String(children ?? "").includes("\n");
    return fenced ? (
      <CodeBlock className={className}>{children}</CodeBlock>
    ) : (
      <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[0.85em]">{children}</code>
    );
  },
};
