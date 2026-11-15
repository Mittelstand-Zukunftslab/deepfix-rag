"use client";

import { AuiIf, ThreadPrimitive } from "@assistant-ui/react";
import type { ReactNode } from "react";
import { Sparkles } from "lucide-react";

import {
  EmptyState,
  EmptyStateGreeting,
  EmptyStateSuggestion,
  EmptyStateSuggestions,
} from "@/components/assistant-ui/elements/empty-state";

/**
 * The three ways in, as product copy.
 *
 * They are static prompts, not runtime-generated suggestions: the backend sends
 * none, and a fixed set is what lets the test and the page share one literal.
 * Each one is something the assistant can answer from this server's own state —
 * a run it can propose, the champion config, and the recent runs.
 */
export const WELCOME_SUGGESTIONS = [
  {
    label: "Start an optimization run",
    prompt: "Propose an optimization run and tell me what it would change.",
  },
  {
    label: "Show the champion configuration",
    prompt: "Show me the current champion configuration and explain the main settings.",
  },
  {
    label: "Summarize the recent runs",
    prompt: "Summarize the recent optimization runs and what they changed.",
  },
] as const;

const GREETING = "How can I help with this server?";

const DESCRIPTION =
  "Ask about this server's champion, its runs, or what Hermes would change next. It can propose a run; nothing starts until you confirm it.";

function WelcomeBody({ children }: { children?: ReactNode }) {
  return (
    <EmptyState>
      <span className="flex size-11 items-center justify-center rounded-2xl bg-primary/10 text-primary">
        <Sparkles className="size-5" aria-hidden />
      </span>
      <EmptyStateGreeting>{GREETING}</EmptyStateGreeting>
      <p className="-mt-3 max-w-sm text-center text-body-sm text-muted-foreground">{DESCRIPTION}</p>
      <EmptyStateSuggestions>{children}</EmptyStateSuggestions>
    </EmptyState>
  );
}

/** The greeting and the three chips with no runtime behind them, for the page before a thread exists. */
export function WelcomePanel({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="flex flex-1 items-center justify-center py-10">
      <WelcomeBody>
        {WELCOME_SUGGESTIONS.map((suggestion, index) => (
          <EmptyStateSuggestion
            key={suggestion.label}
            index={index}
            onClick={() => onPick(suggestion.prompt)}
          >
            {suggestion.label}
          </EmptyStateSuggestion>
        ))}
      </WelcomeBody>
    </div>
  );
}

/**
 * The first screen of an empty thread.
 *
 * `ThreadPrimitive.Suggestion` fills the composer with the prompt instead of
 * sending it, so a chip can never start a turn — let alone a run — on one click.
 * The admin edits, then sends.
 */
export function ThreadWelcome() {
  return (
    <AuiIf condition={(state) => state.thread.isEmpty}>
      <div className="flex flex-1 items-center justify-center py-10">
        <WelcomeBody>
          {WELCOME_SUGGESTIONS.map((suggestion, index) => (
            <ThreadPrimitive.Suggestion key={suggestion.label} prompt={suggestion.prompt} asChild>
              <EmptyStateSuggestion index={index}>{suggestion.label}</EmptyStateSuggestion>
            </ThreadPrimitive.Suggestion>
          ))}
        </WelcomeBody>
      </div>
    </AuiIf>
  );
}
