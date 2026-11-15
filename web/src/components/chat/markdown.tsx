"use client";

import { Fragment } from "react";
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
export type CitationTokens = {
  /** Wrap one marker in the private tokens the renderer recognises. */
  token: (marker: number) => string;
  /** The markers the server validated; anything else stays plain text. */
  markers: Set<number>;
  /** A control for one validated marker, or `null` to leave it as text. */
  render: (marker: number) => React.ReactNode;
};

export function ChatMarkdown({
  children,
  prose = true,
  className,
  citations,
}: {
  children: string;
  prose?: boolean;
  className?: string;
  /**
   * When present, `[n]` markers the server validated become citation controls.
   * The substitution happens on the markdown source, before parsing, so the
   * marker cannot be eaten by emphasis or link syntax — and it is the one
   * renderer both chats share, so there is no second markdown pipeline.
   */
  citations?: CitationTokens;
}) {
  const source = citations ? prepareCitations(children, citations) : children;
  return (
    <div
      className={cn(
        prose && "prose prose-sm dark:prose-invert max-w-none text-body-md leading-relaxed",
        "text-foreground",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={
          (citations ? citationComponents(citations) : MARKDOWN_COMPONENTS) as React.ComponentProps<
            typeof ReactMarkdown
          >["components"]
        }
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}

/**
 * The shared components plus a `p` that resolves citation tokens in its text.
 *
 * Only `p` needs the override: an answer's prose is paragraphs, and a marker in a
 * heading or a list item still renders as its characters rather than a control.
 * That is a deliberate limit — a citation belongs in a sentence, and widening it
 * would mean rewriting every block type for a case that does not occur.
 */
function citationComponents(
  citations: CitationTokens,
): React.ComponentProps<typeof ReactMarkdown>["components"] {
  return {
    ...MARKDOWN_COMPONENTS,
    p: ({ children }: { children?: React.ReactNode }) => (
      <p>{withCitations(children, citations)}</p>
    ),
  };
}

/** Rebuild a paragraph's children, replacing citation tokens with their controls. */
function withCitations(node: React.ReactNode, citations: CitationTokens): React.ReactNode {
  if (typeof node === "string") return <CitationText text={node} citations={citations} />;
  if (Array.isArray(node)) {
    return node.map((child, index) => (
      <Fragment key={index}>{withCitations(child, citations)}</Fragment>
    ));
  }
  return node;
}

/**
 * Rewrite validated markers into a token the component map can replace.
 *
 * The token survives markdown parsing as ordinary text, and the component map
 * below turns it into the caller's control. A marker that was not validated is
 * not rewritten, so it renders as the characters the answer wrote.
 */
function prepareCitations(text: string, citations: CitationTokens): string {
  return text.replace(/\[(\d{1,2})\]/g, (whole, digits: string) => {
    const marker = Number(digits);
    return citations.markers.has(marker) ? citations.token(marker) : whole;
  });
}

/** A text node, with any citation token replaced by its control. */
function CitationText({ text, citations }: { text: string; citations?: CitationTokens }) {
  if (!citations) return <>{text}</>;
  const pieces = text.split(/(\uE000\d{1,2}\uE001)/g);
  if (pieces.length === 1) return <>{text}</>;
  return (
    <>
      {pieces.map((piece, index) => {
        const match = /^\uE000(\d{1,2})\uE001$/.exec(piece);
        if (!match) return <Fragment key={index}>{piece}</Fragment>;
        return <Fragment key={index}>{citations.render(Number(match[1])) ?? piece}</Fragment>;
      })}
    </>
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
