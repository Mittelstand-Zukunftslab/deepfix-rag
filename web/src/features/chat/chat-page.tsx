import { useState, type FormEvent } from "react";

import { TopBar } from "@/components/layout/app-shell";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { IndexPreparing } from "@/features/index/index-preparing";
import { askService, type AskHit } from "@/services/ask-service";

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
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

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
      setResults(response.hits);
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
          <Card className="flex flex-col gap-2 p-5 shadow-soft">
            <p className="text-label-lg text-foreground">Answer</p>
            <p className="text-body-md text-foreground">{resultAnswer}</p>
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
