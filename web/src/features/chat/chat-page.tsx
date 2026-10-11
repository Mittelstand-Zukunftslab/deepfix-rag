import { useSuspenseQuery } from "@tanstack/react-query";
import {
  ArrowUp,
  CheckCheck,
  FileText,
  Paperclip,
  Plus,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { TopBar } from "@/components/layout/app-shell";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { formatCurrency } from "@/lib/format";
import type { ChatMessage } from "@/schemas/chat";
import { conversationQueryOptions, useResetConversation, useSendMessage } from "./chat-queries";

const timeFormatter = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });

export function ChatPage() {
  const { data: messages } = useSuspenseQuery(conversationQueryOptions());
  const send = useSendMessage();
  const reset = useResetConversation();
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [messages.length]);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!draft.trim()) return;
    send.mutate({ content: draft });
    setDraft("");
  };

  return (
    <div className="flex h-screen flex-col">
      <TopBar
        title="Archival intelligence chat"
        actions={
          <Button variant="soft" size="sm" onClick={reset}>
            <Plus />
            New chat
          </Button>
        }
      />
      <div className="flex-1 overflow-y-auto px-8 py-4">
        <div className="mx-auto flex max-w-4xl flex-col gap-8">
          {messages.length === 0 ? (
            <p className="py-24 text-center text-body-md text-muted-foreground">
              Start a new conversation about your documents.
            </p>
          ) : null}
          {messages.map((m) =>
            m.role === "user" ? (
              <UserBubble key={m.id} message={m} />
            ) : (
              <AssistantBubble key={m.id} message={m} />
            ),
          )}
          {send.isPending ? (
            <p className="pl-14 text-body-sm text-muted-foreground">Searching your archive…</p>
          ) : null}
          <div ref={endRef} />
        </div>
      </div>
      <form onSubmit={onSubmit} className="mx-auto w-full max-w-4xl px-8 pb-8">
        <Card className="flex items-center gap-3 px-4 py-2 shadow-soft">
          <Paperclip className="size-5 text-muted-foreground" aria-hidden />
          <Input
            aria-label="Message"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Ask anything about your documents, fiscal reports, or agreements…"
            className="border-0 shadow-none"
          />
          <Button type="submit" size="icon" aria-label="Send message" disabled={send.isPending}>
            <ArrowUp />
          </Button>
        </Card>
      </form>
    </div>
  );
}

function UserBubble({ message }: { message: ChatMessage }) {
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-start gap-3">
        <div className="rounded-2xl bg-surface-high px-5 py-3 text-body-md text-foreground">
          {message.content}
        </div>
        <Avatar>
          <AvatarFallback>FS</AvatarFallback>
        </Avatar>
      </div>
      <p className="flex items-center gap-1 pr-14 text-label-md text-muted-foreground">
        {timeFormatter.format(new Date(message.createdAt))}
        <CheckCheck className="size-3.5 text-primary" />
      </p>
    </div>
  );
}

function AssistantBubble({ message }: { message: ChatMessage }) {
  return (
    <div className="flex items-start gap-4">
      <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
        <Sparkles className="size-4" />
      </div>
      <Card className="flex max-w-2xl flex-col gap-4 p-5 shadow-soft">
        <p className="text-body-md text-foreground">{message.content}</p>
        {message.breakdown ? (
          <div className="flex flex-col gap-3 rounded-lg bg-surface-low p-5">
            {message.breakdown.items.map((item) => (
              <div key={item.label} className="flex justify-between text-body-sm">
                <span className="text-foreground">{item.label}</span>
                <span className="font-semibold text-foreground">{formatCurrency(item.amount)}</span>
              </div>
            ))}
            <Separator />
            <div className="flex items-center justify-between">
              <span className="text-label-lg text-foreground">{message.breakdown.totalLabel}</span>
              <span className="text-headline-md text-primary">
                {formatCurrency(message.breakdown.total)}
              </span>
            </div>
          </div>
        ) : null}
        <div className="flex items-center gap-2">
          {message.sources?.length ? (
            <span className="text-label-md text-muted-foreground">Sources:</span>
          ) : null}
          {message.sources?.map((s) => (
            <Badge key={s} variant="soft" className="gap-1">
              <FileText className="size-3" />
              {s}
            </Badge>
          ))}
          <div className="ml-auto flex">
            <Button variant="icon" size="icon" aria-label="Helpful answer">
              <ThumbsUp />
            </Button>
            <Button variant="icon" size="icon" aria-label="Unhelpful answer">
              <ThumbsDown />
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
