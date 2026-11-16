import { useEffect, useRef, useState } from "react";
import { BookOpen } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ChatMarkdown } from "@/components/chat/markdown";
import { Card } from "@/components/ui/card";
import { commentKey, useFeedbackComments } from "@/features/feedback/use-feedback-comments";
import { VoteButtons, type Vote } from "@/features/feedback/vote-buttons";
import { VoteComment } from "@/features/feedback/vote-comment";
import { feedbackService } from "@/services/feedback-service";
import type { AskCitation, AskPassage } from "@/schemas/ask-chat";

/**
 * The passages behind an answer.
 *
 * Every card comes from what `search_documents` returned — the tool's output
 * object — never from parsing the answer's prose, so the panel and the model saw
 * the same evidence (FR-007). Each card is votable through the existing feedback
 * endpoint with the rank the tool returned, which is the rank the member is
 * looking at.
 *
 * A citation marker in the answer focuses the card it names. A marker that
 * resolved to nothing never reaches this component: the server already dropped
 * it from `citations`, and the answer renders it as plain text.
 */

export type SourcesState = {
  passages: AskPassage[];
  citations: AskCitation[];
  /** The interaction the votes belong to, once the turn is stored. */
  interactionId: string | null;
};

export function SourcesPanel({
  state,
  selectedRank,
  onSelect,
  onVote,
}: {
  state: SourcesState;
  selectedRank: number | null;
  onSelect: (rank: number) => void;
  onVote: (rank: number, action: "up" | "down" | "clear") => void;
}) {
  if (state.passages.length === 0) return null;
  return (
    <aside className="w-full min-w-0" aria-label="Sources">
      <section className="flex h-full min-w-0 flex-col gap-3 rounded-2xl border border-border/80 bg-muted/20 p-4 shadow-sm">
        <div className="flex items-center justify-between border-b border-border/60 pb-2.5">
          <div className="flex items-center gap-2">
            <BookOpen className="size-4 text-primary" aria-hidden />
            <h2 className="text-label-md font-semibold uppercase tracking-wider text-foreground">
              Sources
            </h2>
          </div>
          <span className="rounded-full bg-background px-2.5 py-0.5 text-label-sm font-medium text-muted-foreground ring-1 ring-border">
            {state.passages.length} {state.passages.length === 1 ? "passage" : "passages"}
          </span>
        </div>
        <ol className="flex flex-1 flex-col gap-3 overflow-y-auto pr-1">
          {state.passages.map((passage) => (
            <li key={`${passage.doc_id}-${passage.rank}`}>
              <PassageCard
                passage={passage}
                cited={state.citations.some((citation) => citation.rank === passage.rank)}
                selected={selectedRank === passage.rank}
                interactionId={state.interactionId}
                onSelect={() => onSelect(passage.rank)}
                onVote={(action) => onVote(passage.rank, action)}
              />
            </li>
          ))}
        </ol>
      </section>
    </aside>
  );
}

function PassageCard({
  passage,
  cited,
  selected,
  interactionId,
  onSelect,
  onVote,
}: {
  passage: AskPassage;
  cited: boolean;
  selected: boolean;
  interactionId: string | null;
  onSelect: () => void;
  onVote: (action: "up" | "down" | "clear") => void;
}) {
  const comments = useFeedbackComments();
  const [vote, setVote] = useState<Vote>(null);
  const ref = useRef<HTMLDivElement>(null);

  // A citation click selects the card; moving selection here also moves focus, so
  // a keyboard user follows the marker to its evidence.
  useEffect(() => {
    if (!selected) return;
    if (typeof ref.current?.scrollIntoView === "function") {
      ref.current.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
    ref.current?.focus({ preventScroll: true });
  }, [selected]);

  const choose = async (action: "up" | "down" | "clear") => {
    if (!interactionId) {
      setVote(action === "clear" ? null : action);
      onVote(action);
      return;
    }
    const saved = await feedbackService.submit({
      interactionId,
      target: "passage",
      rank: passage.rank,
      action,
    });
    setVote(saved.vote ?? null);
    onVote(action);
    comments.voted(
      commentKey(interactionId, "passage", passage.rank),
      "passage",
      saved.vote ?? null,
    );
  };

  return (
    <div
      ref={ref}
      id={`passage-${passage.rank}`}
      tabIndex={-1}
      onFocus={onSelect}
      onClick={onSelect}
      aria-current={selected ? "true" : undefined}
      className={`flex flex-col gap-2 rounded-xl border p-4 shadow-soft transition-all ${
        selected
          ? "border-primary bg-primary/5 ring-2 ring-primary/30"
          : "border-border bg-card hover:border-primary/50"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        {/* `min-w-0` on the name group is what lets `truncate` actually work: a
            flex child defaults to its content width, so without it a long
            filename pushes the score out of the card instead of ellipsising. */}
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="flex size-5 shrink-0 items-center justify-center rounded bg-primary/10 font-mono text-label-sm font-semibold text-primary">
            {passage.rank}
          </span>
          <span
            className="min-w-0 truncate text-label-sm font-medium text-foreground"
            title={passage.filename}
          >
            {passage.filename}
          </span>
          {cited ? (
            <span className="shrink-0 rounded bg-primary/10 px-1 text-label-sm text-primary">
              cited
            </span>
          ) : null}
        </div>
        <span className="shrink-0 font-mono text-label-sm tabular-nums text-muted-foreground">
          {passage.score.toFixed(3)}
        </span>
      </div>
      <p className="line-clamp-6 break-words text-label-sm leading-relaxed text-foreground/85">
        {passage.text}
      </p>
      {passage.truncated ? (
        <p className="text-label-sm italic text-muted-foreground">
          Shortened for the model; the full passage is what the vote records.
        </p>
      ) : null}
      <div className="border-t border-border/40 pt-2">
        <VoteButtons
          vote={vote}
          upLabel={`Mark passage ${passage.rank} as relevant`}
          downLabel={`Mark passage ${passage.rank} as not relevant`}
          onAction={(action) => void choose(action)}
        />
        {interactionId ? (
          <div className="mt-2">
            <VoteComment
              comments={comments}
              interactionId={interactionId}
              target="passage"
              rank={passage.rank}
              vote={vote}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}

/**
 * Renders an answer with its citation markers resolved.
 *
 * The answer is rendered by the shared `ChatMarkdown` once, so formatting and
 * every other markdown feature behave as they do everywhere else. The validated
 * markers are handed to it as a token map: inside the rendered tree each token
 * becomes a control, and a marker the server did not validate is left exactly as
 * the member's answer wrote it — which is D-10's rule that a citation pointing at
 * no evidence is never a link.
 */
export function AnswerWithCitations({
  answer,
  citations,
  onSelect,
  Markdown = ChatMarkdown,
}: {
  answer: string;
  citations: AskCitation[];
  onSelect: (rank: number) => void;
  /** The shared renderer; injectable so a test can drive the token map directly. */
  Markdown?: typeof ChatMarkdown;
}) {
  const byMarker = new Map(citations.map((citation) => [citation.marker, citation]));
  return (
    <div className="text-body-md leading-relaxed text-foreground">
      <Markdown
        prose={false}
        citations={{
          token: (marker) => `${TOKEN_OPEN}${marker}${TOKEN_CLOSE}`,
          markers: new Set(byMarker.keys()),
          render: (marker) => {
            const citation = byMarker.get(marker);
            if (!citation) return null;
            return (
              <button
                type="button"
                onClick={() => onSelect(citation.rank)}
                aria-label={`Show passage ${citation.rank}`}
                className="mx-0.5 rounded px-1 align-baseline text-label-sm font-medium text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {citation.marker}
              </button>
            );
          },
        }}
      >
        {answer}
      </Markdown>
    </div>
  );
}

// Deliberately not something a model writes in prose, so a real answer cannot
// collide with it and be mistaken for a marker.
const TOKEN_OPEN = "\uE000";
const TOKEN_CLOSE = "\uE001";

export function SourcesToggle({
  open,
  count,
  onToggle,
}: {
  open: boolean;
  count: number;
  onToggle: () => void;
}) {
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={onToggle}
      className="h-7 gap-1 px-2 text-label-sm text-muted-foreground hover:text-foreground"
    >
      {open ? "Hide sources" : `Sources (${count})`}
    </Button>
  );
}
