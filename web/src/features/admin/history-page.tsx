import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { PageHeader } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDateTime } from "@/lib/format";
import {
  HISTORY_PAGE_SIZE,
  type HistoryFeedback,
  type HistoryFilters,
  type HistoryKind,
} from "@/schemas/history";
import type { HistoryEntry, HistoryPage, InteractionReview } from "@/services/history-service";

const KIND_LABEL: Record<HistoryKind, string> = { retrieval: "Search", ask: "Ask" };

const FEEDBACK_OPTIONS: { value: HistoryFeedback; label: string }[] = [
  { value: "all", label: "All" },
  { value: "any", label: "With feedback" },
  { value: "negative", label: "Negative feedback" },
  { value: "none", label: "No feedback" },
];

const SELECT_CLASS =
  "flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-base shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring md:text-sm";

export interface ReviewState {
  data?: InteractionReview | undefined;
  error?: string | undefined;
  loading: boolean;
}

export function HistoryDenied() {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-3 px-8 py-10">
      <h1 className="text-headline-md text-foreground">History</h1>
      <p>This page is only available to admins.</p>
    </div>
  );
}

export function HistoryReviewPage({
  page,
  filters,
  selectedId,
  review,
  onFiltersChange,
  onSelect,
}: {
  page: HistoryPage;
  filters: HistoryFilters;
  selectedId: string | undefined;
  review: ReviewState;
  onFiltersChange: (next: HistoryFilters) => void;
  onSelect: (id: string | undefined) => void;
}) {
  const [search, setSearch] = useState(filters.q ?? "");

  useEffect(() => {
    setSearch(filters.q ?? "");
  }, [filters.q]);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    const q = search.trim();
    onFiltersChange({ ...filters, q: q || undefined, page: 1 });
  };

  const first = page.total === 0 ? 0 : page.offset + 1;
  const last = page.offset + page.items.length;
  const filtered = Boolean(filters.q || filters.kind || filters.feedback !== "all");

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-8 px-8 py-10">
      <PageHeader
        title="History"
        description="Every search and question from the team, with the answer returned and the feedback given."
      />

      <form
        role="search"
        aria-label="Filter history"
        className="flex flex-wrap items-end gap-4"
        onSubmit={submitSearch}
      >
        <div className="flex min-w-64 flex-1 flex-col gap-2">
          <Label htmlFor="history-search">Question or person</Label>
          <div className="flex gap-2">
            <Input
              id="history-search"
              type="search"
              value={search}
              maxLength={200}
              autoComplete="off"
              placeholder="Words from the question, or an email"
              onChange={(event) => setSearch(event.target.value)}
            />
            <Button type="submit" variant="outline">
              Search
            </Button>
          </div>
        </div>
        <div className="flex w-40 flex-col gap-2">
          <Label htmlFor="history-kind">Type</Label>
          <select
            id="history-kind"
            className={SELECT_CLASS}
            value={filters.kind ?? ""}
            onChange={(event) =>
              onFiltersChange({
                ...filters,
                kind: (event.target.value || undefined) as HistoryKind | undefined,
                page: 1,
              })
            }
          >
            <option value="">All</option>
            <option value="retrieval">Searches</option>
            <option value="ask">Asks</option>
          </select>
        </div>
        <div className="flex w-48 flex-col gap-2">
          <Label htmlFor="history-feedback">Feedback</Label>
          <select
            id="history-feedback"
            className={SELECT_CLASS}
            value={filters.feedback}
            onChange={(event) =>
              onFiltersChange({
                ...filters,
                feedback: event.target.value as HistoryFeedback,
                page: 1,
              })
            }
          >
            {FEEDBACK_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      </form>

      <Card className="rounded-2xl shadow-soft">
        <CardContent className="pt-6">
          {page.items.length === 0 ? (
            <p>
              {filtered
                ? "Nothing matches these filters."
                : "No searches or questions yet. They appear here once people use Search or Ask."}
            </p>
          ) : (
            <Table>
              <TableCaption className="sr-only">Search and ask history, newest first</TableCaption>
              <TableHeader>
                <TableRow>
                  <TableHead className="whitespace-nowrap">When</TableHead>
                  <TableHead>Person</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Question</TableHead>
                  <TableHead>Answer</TableHead>
                  <TableHead>Feedback</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {page.items.map((entry) => (
                  <HistoryRow key={entry.interaction_id} entry={entry} onSelect={onSelect} />
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <nav aria-label="History pages" className="flex items-center justify-between gap-4">
        <p className="text-sm text-muted-foreground" aria-live="polite">
          {page.total === 0 ? "No results" : `Showing ${first}–${last} of ${page.total}`}
        </p>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={filters.page <= 1}
            onClick={() => onFiltersChange({ ...filters, page: filters.page - 1 })}
          >
            Previous
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={filters.page * HISTORY_PAGE_SIZE >= page.total}
            onClick={() => onFiltersChange({ ...filters, page: filters.page + 1 })}
          >
            Next
          </Button>
        </div>
      </nav>

      <Sheet
        open={selectedId !== undefined}
        onOpenChange={(open) => {
          if (!open) onSelect(undefined);
        }}
      >
        <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
          <ReviewPanel review={review} />
        </SheetContent>
      </Sheet>
    </div>
  );
}

function HistoryRow({ entry, onSelect }: { entry: HistoryEntry; onSelect: (id: string) => void }) {
  return (
    <TableRow>
      <TableCell className="whitespace-nowrap align-top">
        {formatDateTime(entry.created_at)}
      </TableCell>
      <TableCell className="align-top">{entry.user_email || "Unknown"}</TableCell>
      <TableCell className="align-top">
        <Badge variant="soft">{KIND_LABEL[entry.kind]}</Badge>
      </TableCell>
      <TableCell className="max-w-64 align-top">
        <button
          type="button"
          className="text-left font-medium text-foreground underline-offset-4 hover:underline focus-visible:underline"
          onClick={() => onSelect(entry.interaction_id)}
        >
          {entry.query}
        </button>
      </TableCell>
      <TableCell className="max-w-80 align-top text-muted-foreground">
        <span className="line-clamp-2">{answerSummary(entry)}</span>
      </TableCell>
      <TableCell className="whitespace-nowrap align-top">
        <FeedbackSummary entry={entry} />
      </TableCell>
    </TableRow>
  );
}

function answerSummary(entry: HistoryEntry): string {
  if (entry.error) return `Failed: ${entry.error}`;
  if (entry.answer) return entry.answer;
  const passages = entry.hit_count === 1 ? "1 passage" : `${entry.hit_count} passages`;
  return entry.kind === "retrieval" ? `${passages} shown` : `No answer, ${passages}`;
}

function FeedbackSummary({ entry }: { entry: HistoryEntry }) {
  const answer = entry.answer_votes;
  const passages = entry.passage_votes;
  const parts = [
    entry.kind === "ask" && answer.up + answer.down > 0 ? { label: "Answer", votes: answer } : null,
    passages.up + passages.down > 0 ? { label: "Passages", votes: passages } : null,
  ].filter((part) => part !== null);
  if (parts.length === 0) return <span className="text-muted-foreground">None</span>;
  return (
    <ul className="flex flex-col gap-1 text-sm">
      {parts.map((part) => (
        <li key={part.label}>
          {part.label} <VoteCounts up={part.votes.up} down={part.votes.down} />
        </li>
      ))}
    </ul>
  );
}

function VoteCounts({ up, down }: { up: number; down: number }) {
  return (
    <span className="inline-flex items-center gap-2 tabular-nums">
      <span className="inline-flex items-center gap-1" aria-label={`${up} helpful`}>
        <ThumbsUp className="size-3.5" aria-hidden />
        {up}
      </span>
      <span
        className={`inline-flex items-center gap-1 ${down > 0 ? "text-destructive" : ""}`}
        aria-label={`${down} not helpful`}
      >
        <ThumbsDown className="size-3.5" aria-hidden />
        {down}
      </span>
    </span>
  );
}

function ReviewPanel({ review }: { review: ReviewState }) {
  if (review.error) {
    return (
      <SheetHeader>
        <SheetTitle>Unable to load this entry</SheetTitle>
        <SheetDescription role="alert">{review.error}</SheetDescription>
      </SheetHeader>
    );
  }
  if (review.loading || !review.data) {
    return (
      <SheetHeader>
        <SheetTitle>Loading</SheetTitle>
        <SheetDescription role="status">Loading this entry.</SheetDescription>
      </SheetHeader>
    );
  }

  const { entry, hits, build_id: buildId } = review.data;
  return (
    <div className="flex flex-col gap-6">
      <SheetHeader className="pr-8">
        <SheetTitle className="text-headline-md">{entry.query}</SheetTitle>
        <SheetDescription>
          {KIND_LABEL[entry.kind]} by {entry.user_email || "an unknown person"} on{" "}
          {formatDateTime(entry.created_at)}
        </SheetDescription>
      </SheetHeader>

      {entry.kind === "ask" ? (
        <section aria-labelledby="review-answer" className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-4">
            <h3 id="review-answer" className="text-label-md font-semibold">
              Answer
            </h3>
            <VoteCounts up={entry.answer_votes.up} down={entry.answer_votes.down} />
          </div>
          {entry.error ? (
            <p className="text-sm font-medium text-destructive">{entry.error}</p>
          ) : entry.answer ? (
            <div className="prose prose-sm dark:prose-invert max-w-none text-body-md leading-relaxed text-foreground">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{entry.answer}</ReactMarkdown>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No answer was returned.</p>
          )}
        </section>
      ) : entry.error ? (
        <p className="text-sm font-medium text-destructive">{entry.error}</p>
      ) : null}

      <section aria-labelledby="review-passages" className="flex flex-col gap-3">
        <h3 id="review-passages" className="text-label-md font-semibold">
          Passages shown
        </h3>
        {hits.length === 0 ? (
          <p className="text-sm text-muted-foreground">No passages were shown.</p>
        ) : (
          <ol className="flex flex-col gap-3">
            {hits.map((hit) => (
              <li key={hit.rank} className="rounded-xl border border-border p-4">
                <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="font-medium">
                    {hit.rank}. {hit.document_filename}
                  </span>
                  <span className="flex items-center gap-3 text-muted-foreground">
                    <span className="tabular-nums">Score {hit.score.toFixed(3)}</span>
                    <VoteCounts up={hit.votes.up} down={hit.votes.down} />
                  </span>
                </div>
                <p className="mt-2 whitespace-pre-wrap text-sm text-muted-foreground">{hit.text}</p>
              </li>
            ))}
          </ol>
        )}
      </section>

      <p className="break-all font-mono text-xs text-muted-foreground">
        {entry.config_path} · build {buildId}
      </p>
    </div>
  );
}
