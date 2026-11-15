"use client";

import { AuiIf, ComposerPrimitive } from "@assistant-ui/react";
import { ArrowUp, Square } from "lucide-react";

import { ChatComposerBar, ChatComposerHint } from "@/components/chat/composer";
import { cn } from "@/lib/utils";

/** The line under the composer; it is the promise the action card keeps. */
export const CHAT_DISCLAIMER =
  "Hermes can look things up and propose a run. Nothing starts until you confirm it.";

const BUTTON =
  "flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground transition-all hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

const INPUT =
  "max-h-48 min-h-10 w-full flex-1 resize-none bg-transparent px-2 py-2 text-base outline-none placeholder:text-muted-foreground/70 md:text-sm";

/**
 * The admin chat's composer, built from Assistant UI's composer primitives.
 *
 * `ComposerPrimitive.Root` is the form, `Input` an auto-resizing textarea that
 * sends on Enter and inserts a newline on Shift+Enter, and one control that is
 * `Send` while idle and `Cancel` — labelled **Stop generating** — while a turn
 * runs. The surface around them is the Ask page's `ChatComposerBar` (CHAT-016),
 * so both chats read as the same composer without sharing a submit path they do
 * not have in common.
 */
export function ChatComposer({ disabled }: { disabled: boolean }) {
  return (
    <div className="flex flex-col">
      <ComposerPrimitive.Root className="flex flex-col">
        <ChatComposerBar className="items-end">
          <ComposerPrimitive.Input
            aria-label="Message the assistant"
            placeholder={disabled ? "Start a thread to send a message." : "Ask about this server…"}
            rows={1}
            disabled={disabled}
            className={cn(INPUT)}
          />
          <AuiIf condition={(state) => !state.thread.isRunning}>
            <ComposerPrimitive.Send asChild>
              <button
                type="submit"
                aria-label="Send message"
                disabled={disabled}
                className={BUTTON}
              >
                <ArrowUp className="size-4" aria-hidden />
              </button>
            </ComposerPrimitive.Send>
          </AuiIf>
          <AuiIf condition={(state) => state.thread.isRunning}>
            <ComposerPrimitive.Cancel asChild>
              <button type="button" aria-label="Stop generating" className={BUTTON}>
                <Square className="size-3.5 fill-current" aria-hidden />
              </button>
            </ComposerPrimitive.Cancel>
          </AuiIf>
        </ChatComposerBar>
      </ComposerPrimitive.Root>
      <ChatComposerHint>{CHAT_DISCLAIMER}</ChatComposerHint>
    </div>
  );
}

/**
 * The same bar with no thread behind it, for the moment before a conversation
 * exists. It is a plain disabled field rather than a runtime composer, because
 * there is no runtime to send through yet; it keeps the surface and the hint so
 * the page does not visibly change shape when the first thread opens.
 */
export function DraftComposer({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="flex flex-col">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onCreate();
        }}
      >
        <ChatComposerBar className="items-end">
          <label htmlFor="admin-chat-draft" className="sr-only">
            Message the assistant
          </label>
          <textarea
            id="admin-chat-draft"
            rows={1}
            value=""
            readOnly
            placeholder="Start a thread to send a message."
            className={cn(INPUT)}
          />
          <button type="submit" aria-label="Start a conversation" className={BUTTON}>
            <ArrowUp className="size-4" aria-hidden />
          </button>
        </ChatComposerBar>
      </form>
      <ChatComposerHint>{CHAT_DISCLAIMER}</ChatComposerHint>
    </div>
  );
}
