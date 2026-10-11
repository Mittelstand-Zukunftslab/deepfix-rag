import { useEffect, useState, type FormEvent } from "react";

import { TopBar } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { VoteButtons, type Vote } from "@/features/feedback/vote-buttons";
import { IndexPreparing } from "@/features/index/index-preparing";
import { askService, type AskHit } from "@/services/ask-service";
import { feedbackService } from "@/services/feedback-service";

const ASK_INTERACTION = "deepfix.ask.interaction";

export function AskPage({
  indexReady = true,
  answer = undefined,
  error: initialError = null,
  hits,
  askedQuery = null,
}: {
  indexReady?: boolean;
  answer?: string | null;
  error?: string | null;
  hits?: AskHit[];
  askedQuery?: string | null;
}) {
  const [draft, setDraft] = useState(askedQuery ?? "");
  const [activeQuery, setActiveQuery] = useState<string | null>(askedQuery);
  const [resultAnswer, setResultAnswer] = useState<string | null>(hits ? (answer ?? null) : null);
  const [resultError, setResultError] = useState<string | null>(initialError);
  const [results, setResults] = useState<AskHit[] | null>(hits ?? null);
  const [answerVote, setAnswerVote] = useState<Vote>(null);
  const [interactionId, setInteractionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (hits !== undefined) return;
    const stored = window.sessionStorage.getItem(ASK_INTERACTION);
    if (!stored) return;
    void feedbackService
      .read(stored)
      .then((interaction) => {
        if (interaction.kind !== "ask") return;
        setDraft(interaction.query);
        setActiveQuery(interaction.query);
        setResultAnswer(interaction.answer);
        setResultError(interaction.error);
        setAnswerVote(interaction.vote ?? null);
        setResults(interaction.hits);
        setInteractionId(interaction.interaction_id);
      })
      .catch(() => undefined);
  }, [hits]);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const query = draft.trim();
    if (!query) {
      setError("Enter a question.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const response = await askService.ask(query);
      setActiveQuery(query);
      setResultAnswer(response.answer);
      setResultError(response.error);
      setAnswerVote(response.vote ?? null);
      setResults(response.hits);
      setInteractionId(response.interaction_id);
      window.sessionStorage.setItem(ASK_INTERACTION, response.interaction_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to ask. Try again.");
    } finally {
      setPending(false);
    }
  };

  if (!indexReady) {
    return (
      <>
        <TopBar title="Ask" />
        <div className="mx-auto max-w-5xl px-8 py-16">
          <IndexPreparing />
        </div>
      </>
    );
  }

  const shown = results ?? [];
  const empty = activeQuery != null && shown.length === 0 && !pending && !error;

  const chooseAnswer = async (action: "up" | "down" | "clear") => {
    const vote: Vote = action === "clear" ? null : action;
    if (!interactionId) {
      setAnswerVote(vote);
      return;
    }
    const saved = await feedbackService.submit({ interactionId, target: "answer", action });
    setAnswerVote(saved.vote ?? null);
  };

  const choosePassage = async (hit: AskHit, action: "up" | "down" | "clear") => {
    const vote: Vote = action === "clear" ? null : action;
    if (!interactionId) {
      setResults((current) =>
        (current ?? []).map((item) => (item.rank === hit.rank ? { ...item, vote } : item)),
      );
      return;
    }
    const saved = await feedbackService.submit({
      interactionId,
      target: "passage",
      rank: hit.rank,
      action,
    });
    setResults((current) =>
      (current ?? []).map((item) =>
        item.rank === hit.rank ? { ...item, vote: saved.vote ?? null } : item,
      ),
    );
  };

  return (
    <>
      <TopBar title="Ask" />
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-6 py-10 sm:px-8">
        <form onSubmit={onSubmit} className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <label className="flex min-w-0 flex-1 flex-col gap-2 text-sm font-medium text-foreground">
            Ask
            <Input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              className="h-11 text-base md:text-base"
            />
          </label>
          <Button type="submit" className="h-11" disabled={pending} aria-busy={pending}>
            {pending ? "Asking" : "Ask"}
          </Button>
        </form>
        {error ? (
          <p role="alert" className="text-sm font-medium text-destructive">
            {error}
          </p>
        ) : null}
        {resultError ? (
          <p role="alert" className="text-sm font-medium text-destructive">
            {resultError}
          </p>
        ) : null}
        {resultAnswer ? (
          <Card className="flex flex-col gap-3 p-5 shadow-soft">
            <p className="text-label-lg text-foreground">Answer</p>
            <p className="text-body-md text-foreground">{resultAnswer}</p>
            <VoteButtons
              vote={answerVote}
              upLabel="Mark answer as good"
              downLabel="Mark answer as bad"
              onAction={(action) => void chooseAnswer(action)}
            />
          </Card>
        ) : null}
        {empty ? (
          <p className="text-body-md text-muted-foreground">
            No passages matched &quot;{activeQuery}&quot;.
          </p>
        ) : null}
        {shown.length > 0 ? (
          <section className="flex flex-col gap-3">
            <h2 className="text-headline-md text-foreground">Passages</h2>
            <ol className="flex flex-col gap-3">
              {shown.map((hit) => (
                <li key={`${hit.doc_id}-${hit.rank}`}>
                  <Card className="flex flex-col gap-2 p-5 shadow-soft">
                    <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-body-sm text-muted-foreground">
                      <span className="tabular-nums">{hit.rank}</span>
                      <span className="text-label-lg text-foreground">{hit.document_filename}</span>
                      <span className="tabular-nums">{hit.score.toFixed(3)}</span>
                    </p>
                    <p className="text-body-md text-foreground">{hit.text}</p>
                    <VoteButtons
                      vote={hit.vote ?? null}
                      upLabel="Mark passage as relevant"
                      downLabel="Mark passage as not relevant"
                      onAction={(action) => void choosePassage(hit, action)}
                    />
                  </Card>
                </li>
              ))}
            </ol>
          </section>
        ) : null}
      </div>
    </>
  );
}
