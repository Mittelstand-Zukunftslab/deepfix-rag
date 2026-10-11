import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import {
  ChevronDown,
  Droplet,
  FileText,
  Flame,
  Landmark,
  Router,
  Search,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import type { FormEvent } from "react";

import { TopBar } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { formatCurrency, formatDate, formatNumber } from "@/lib/format";
import type { SearchResult } from "@/schemas/document";
import { searchService } from "@/services/search-service";
import { useSearchStore } from "@/stores/search-store";
import { searchQueryOptions } from "./search-queries";

const ICONS: Record<string, LucideIcon> = {
  "Utility bill": FileText,
  "Municipal water": Droplet,
  "Escrow utility": Landmark,
  "Natural gas": Flame,
  Telecom: Router,
  Sanitation: Trash2,
};

export function SearchPage() {
  const { draft, filters, setDraft, submit, resetFilters } = useSearchStore();
  const { data = [], isFetching } = useQuery({
    ...searchQueryOptions(filters),
    placeholderData: keepPreviousData,
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit();
  };

  return (
    <>
      <TopBar title="Search" />
      <div className="mx-auto flex max-w-5xl flex-col gap-6 px-8 py-6">
        <Card className="flex flex-col gap-5 p-5 shadow-soft">
          <form
            onSubmit={onSubmit}
            className="flex items-center gap-3 rounded-lg bg-surface-low px-4 py-2"
          >
            <Search className="size-5 text-muted-foreground" />
            <Input
              aria-label="Search documents"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="border-0 shadow-none"
            />
            <Button type="submit">Search</Button>
          </form>
          <div className="flex flex-wrap items-center gap-3">
            {filters.docType ? <FilterChip label={`Doc type: ${filters.docType}`} /> : null}
            {filters.period ? <FilterChip label={`Period: ${filters.period}`} /> : null}
            {filters.minAmount ? (
              <Badge variant="match">{`> ${formatCurrency(filters.minAmount)}`}</Badge>
            ) : null}
            <Button variant="link" size="sm" className="ml-auto" onClick={resetFilters}>
              Reset filters
            </Button>
          </div>
        </Card>

        <div className="flex items-center justify-between px-1">
          <p className="text-label-lg text-foreground">
            Results{" "}
            <span className="text-label-md text-muted-foreground">({data.length} matches)</span>
          </p>
          <p className="text-label-md text-muted-foreground">
            Sort: <span className="text-foreground">Relevance</span>
          </p>
        </div>

        <ul className="flex flex-col gap-3" aria-busy={isFetching}>
          {data.map((r) => (
            <ResultRow key={r.id} result={r} />
          ))}
        </ul>

        <div className="flex items-center justify-between px-1 text-label-md text-muted-foreground">
          <span>
            Showing 1–{data.length} of {data.length} results
          </span>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" disabled>
              Previous
            </Button>
            <Button variant="soft" size="sm" disabled>
              Next
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}

function FilterChip({ label }: { label: string }) {
  return (
    <Badge variant="soft" className="gap-1 py-1.5">
      {label}
      <ChevronDown className="size-3" />
    </Badge>
  );
}

function ResultRow({ result }: { result: SearchResult }) {
  const vote = useSearchStore((s) => s.votes[result.id]);
  const setVote = useSearchStore((s) => s.vote);
  const feedback = useMutation({ mutationFn: searchService.sendFeedback });
  const Icon = ICONS[result.subtype] ?? FileText;

  const handleVote = (v: "up" | "down") => {
    setVote(result.id, v);
    feedback.mutate({ documentId: result.id, vote: v });
  };

  return (
    <li>
      <Card className="flex items-center gap-5 px-5 py-5 shadow-soft">
        <div className="flex size-10 items-center justify-center rounded-lg bg-surface-low text-primary">
          <Icon className="size-5" />
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-label-lg text-foreground">{result.title}</h2>
            <Badge variant="match">{formatNumber(result.matchScore)} % match</Badge>
          </div>
          <p className="text-body-sm text-muted-foreground">
            {result.subtype} • {formatDate(result.date)} •{" "}
            <span className="font-medium text-foreground">{formatCurrency(result.amount)}</span>
          </p>
        </div>
        <Button
          variant="icon"
          size="icon"
          aria-label="Helpful"
          aria-pressed={vote === "up"}
          onClick={() => handleVote("up")}
        >
          <ThumbsUp className={vote === "up" ? "fill-accent text-primary" : undefined} />
        </Button>
        <Button
          variant="icon"
          size="icon"
          aria-label="Not helpful"
          aria-pressed={vote === "down"}
          onClick={() => handleVote("down")}
        >
          <ThumbsDown
            className={vote === "down" ? "fill-surface-high text-destructive" : undefined}
          />
        </Button>
      </Card>
    </li>
  );
}

export function SearchPageSkeleton() {
  return (
    <div className="flex flex-col gap-3 p-8">
      {[1, 2, 3].map((i) => (
        <Skeleton key={i} className="h-20 w-full" />
      ))}
    </div>
  );
}
