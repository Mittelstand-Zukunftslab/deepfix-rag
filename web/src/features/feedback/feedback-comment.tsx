import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import {
  COMMENT_MAX_LENGTH,
  COMMENT_REASON_LABEL,
  COMMENT_REASONS,
  isDraftEmpty,
  type CommentDraft,
  type FeedbackTarget,
} from "@/schemas/feedback";

import type { CommentSlot } from "./use-feedback-comments";
import type { Vote } from "./vote-actions";

const SHOW_COUNTER_BELOW = 100;

/**
 * The optional comment that follows a vote. It opens as soon as the vote is saved without
 * taking focus, and can be ignored: the vote is already stored. Reopening it from its
 * link moves focus into the form.
 */
export function FeedbackComment({
  target,
  vote,
  slot,
  onOpen,
  onChange,
  onSkip,
  onSend,
}: {
  target: FeedbackTarget;
  vote: Vote;
  slot: CommentSlot;
  onOpen: () => void;
  onChange: (draft: CommentDraft) => void;
  onSkip: () => void;
  onSend: (draft: CommentDraft) => Promise<void>;
}) {
  const id = useId();
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refocus, setRefocus] = useState(false);
  const [focusForm, setFocusForm] = useState(false);
  const followUp = useRef<HTMLElement | null>(null);
  const form = useRef<HTMLFormElement | null>(null);

  useEffect(() => {
    if (slot.open) setError(null);
  }, [slot.open, vote]);

  useEffect(() => {
    if (refocus && !slot.open) {
      followUp.current?.focus();
      setRefocus(false);
    }
  }, [refocus, slot.open]);

  useEffect(() => {
    if (focusForm && slot.open) {
      form.current?.querySelector<HTMLElement>("button, input, textarea")?.focus();
      setFocusForm(false);
    }
  }, [focusForm, slot.open]);

  if (vote === null) return null;

  const { draft } = slot;

  if (!slot.open) {
    if (slot.sent) {
      return (
        <p
          ref={(node) => {
            followUp.current = node;
          }}
          tabIndex={-1}
          role="status"
          className="text-sm text-muted-foreground outline-none"
        >
          Thanks, your comment was sent.
        </p>
      );
    }
    return (
      <button
        ref={(node) => {
          followUp.current = node;
        }}
        type="button"
        onClick={() => {
          setFocusForm(true);
          onOpen();
        }}
        className="self-start text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline focus-visible:underline"
      >
        {isDraftEmpty(draft) ? "Add a comment" : "Comment not sent · Continue"}
      </button>
    );
  }

  const skip = () => {
    setRefocus(true);
    onSkip();
  };

  const send = async (event?: FormEvent) => {
    event?.preventDefault();
    if (isDraftEmpty(draft) || sending) return;
    setSending(true);
    setError(null);
    try {
      await onSend({ reason: draft.reason, text: draft.text.trim() });
      setRefocus(true);
    } catch (caught) {
      setError(
        caught instanceof Error && caught.message
          ? caught.message
          : "Your comment was not sent. Try again.",
      );
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      skip();
    } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      void send();
    }
  };

  const remaining = COMMENT_MAX_LENGTH - draft.text.length;
  const textId = `${id}-text`;
  const noteId = `${id}-note`;
  const errorId = `${id}-error`;
  const describedBy = [noteId, error ? errorId : null].filter(Boolean).join(" ");

  return (
    <form
      ref={form}
      aria-label={vote === "down" ? "Tell us what was wrong" : "Add a comment"}
      onSubmit={send}
      onKeyDown={onKeyDown}
      className="flex flex-col gap-3 rounded-xl border border-border bg-muted/30 p-3 animate-in fade-in-0 slide-in-from-top-1 duration-200 motion-reduce:animate-none"
    >
      {vote === "down" ? (
        <>
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-2 text-sm font-medium text-foreground">What was wrong?</legend>
            <div className="flex flex-wrap gap-2">
              {COMMENT_REASONS[target].map((reason) => {
                const pressed = draft.reason === reason;
                return (
                  <Button
                    key={reason}
                    type="button"
                    size="sm"
                    variant={pressed ? "default" : "outline"}
                    aria-pressed={pressed}
                    className="h-9 rounded-full"
                    onClick={() => onChange({ ...draft, reason: pressed ? null : reason })}
                  >
                    {COMMENT_REASON_LABEL[reason]}
                  </Button>
                );
              })}
            </div>
          </fieldset>
          <div className="flex flex-col gap-1.5">
            <label htmlFor={textId} className="text-sm text-muted-foreground">
              Anything else? (optional)
            </label>
            <Textarea
              id={textId}
              rows={2}
              value={draft.text}
              maxLength={COMMENT_MAX_LENGTH}
              aria-describedby={describedBy}
              onChange={(event) => onChange({ ...draft, text: event.target.value })}
              className="min-h-16 resize-y bg-background"
            />
          </div>
        </>
      ) : (
        <div className="flex flex-col gap-1.5">
          <label htmlFor={textId} className="text-sm text-muted-foreground">
            What made it helpful? (optional)
          </label>
          <Input
            id={textId}
            value={draft.text}
            maxLength={COMMENT_MAX_LENGTH}
            autoComplete="off"
            aria-describedby={describedBy}
            onChange={(event) => onChange({ ...draft, text: event.target.value })}
            className="bg-background"
          />
        </div>
      )}

      {error ? (
        <p id={errorId} role="alert" className="text-sm font-medium text-destructive">
          {error}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p id={noteId} className="text-xs text-muted-foreground">
          Only admins can read this.
          {remaining <= SHOW_COUNTER_BELOW ? (
            <span className={cn("ml-2 tabular-nums", remaining < 20 && "text-destructive")}>
              {remaining} characters left
            </span>
          ) : null}
        </p>
        <div className="flex gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={skip}>
            Skip
          </Button>
          <Button type="submit" size="sm" disabled={isDraftEmpty(draft) || sending}>
            {sending ? "Sending" : error ? "Try again" : "Send"}
          </Button>
        </div>
      </div>
    </form>
  );
}
