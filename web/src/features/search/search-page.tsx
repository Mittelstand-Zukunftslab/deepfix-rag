import { useEffect, useState, type FormEvent } from "react";

import { TopBar } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { commentKey, useFeedbackComments } from "@/features/feedback/use-feedback-comments";
import { VoteButtons, type Vote } from "@/features/feedback/vote-buttons";
import { VoteComment } from "@/features/feedback/vote-comment";
import { IndexPreparing } from "@/features/index/index-preparing";
import { feedbackService } from "@/services/feedback-service";
import { searchService, type SearchHit } from "@/services/search-service";

const SEARCH_INTERACTION = "deepfix.search.interaction";

export function SearchPage({
  indexReady = true,
  hits,
  searchedQuery = null,
}: {
  indexReady?: boolean;
  hits?: SearchHit[];
  searchedQuery?: string | null;
}) {
  const [draft, setDraft] = useState(searchedQuery ?? "");
  const [activeQuery, setActiveQuery] = useState<string | null>(searchedQuery);
  const [results, setResults] = useState<SearchHit[] | null>(hits ?? null);
  const [interactionId, setInteractionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const comments = useFeedbackComments();

  useEffect(() => {
    if (hits !== undefined) return;
    const stored = window.sessionStorage.getItem(SEARCH_INTERACTION);
    if (!stored) return;
    void feedbackService
      .read(stored)
      .then((interaction) => {
        if (interaction.kind !== "retrieval") return;
        setDraft(interaction.query);
        setActiveQuery(interaction.query);
        setResults(interaction.hits);
        setInteractionId(interaction.interaction_id);
      })
      .catch(() => undefined);
  }, [hits]);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const query = draft.trim();
    if (!query) {
      setError("Enter a query.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const response = await searchService.search(query);
      setActiveQuery(query);
      setResults(response.hits);
      setInteractionId(response.interaction_id);
      window.sessionStorage.setItem(SEARCH_INTERACTION, response.interaction_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to search. Try again.");
    } finally {
      setPending(false);
    }
  };

  if (!indexReady) {
    return (
      <>
        <TopBar title="Search" />
        <div className="mx-auto max-w-5xl px-8 py-16">
          <IndexPreparing />
        </div>
      </>
    );
  }

  const shown = results ?? [];
  const empty = activeQuery != null && shown.length === 0 && !pending && !error;

  const choosePassage = async (hit: SearchHit, action: "up" | "down" | "clear") => {
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
    comments.voted(commentKey(interactionId, "passage", hit.rank), "passage", saved.vote ?? null);
  };

  return (
    <>
      <TopBar title="Search" />
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-6 py-10 sm:px-8">
        <form onSubmit={onSubmit} className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <label className="flex min-w-0 flex-1 flex-col gap-2 text-sm font-medium text-foreground">
            Search
            <Input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              className="h-11 text-base md:text-base"
            />
          </label>
          <Button type="submit" className="h-11" disabled={pending} aria-busy={pending}>
            {pending ? "Searching" : "Search"}
          </Button>
        </form>
        {error ? (
          <p role="alert" className="text-sm font-medium text-destructive">
            {error}
          </p>
        ) : null}
        {empty ? (
          <p className="text-body-md text-muted-foreground">
            No passages matched &quot;{activeQuery}&quot;.
          </p>
        ) : null}
        {shown.length > 0 ? (
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
                  {interactionId ? (
                    <VoteComment
                      comments={comments}
                      interactionId={interactionId}
                      target="passage"
                      rank={hit.rank}
                      vote={hit.vote ?? null}
                    />
                  ) : null}
                </Card>
              </li>
            ))}
          </ol>
        ) : null}
      </div>
    </>
  );
}
