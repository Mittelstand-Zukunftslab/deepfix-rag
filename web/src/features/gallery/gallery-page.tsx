import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { LayoutGrid, Search } from "lucide-react";

import { PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { formatDate, formatFileSize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { GALLERY_CATEGORIES, GALLERY_PAGE_SIZE } from "@/services/gallery-service";
import { useGalleryStore, type GalleryDensity } from "@/stores/gallery-store";
import { DocumentThumbnail } from "./document-thumbnail";
import { galleryQueryOptions } from "./gallery-queries";

const GRID: Record<GalleryDensity, string> = {
  compact: "grid-cols-2 md:grid-cols-4 xl:grid-cols-5",
  standard: "grid-cols-1 md:grid-cols-3 xl:grid-cols-4",
  large: "grid-cols-1 md:grid-cols-2 xl:grid-cols-3",
};

const isDensity = (v: string): v is GalleryDensity =>
  v === "compact" || v === "standard" || v === "large";

export function GalleryPage() {
  const { density, category, text, page, setDensity, setCategory, setText, setPage } =
    useGalleryStore();
  const { data } = useQuery({
    ...galleryQueryOptions({ category, text, page }),
    placeholderData: keepPreviousData,
  });
  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / GALLERY_PAGE_SIZE));
  const first = total ? (page - 1) * GALLERY_PAGE_SIZE + 1 : 0;
  const last = Math.min(page * GALLERY_PAGE_SIZE, total);

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6 px-8 py-10">
      <PageHeader
        eyebrow="Repository visual catalog"
        title="Data visualization"
        description="Visual overview and high-fidelity thumbnail catalog of all digitized, parsed, and indexed documents in your archival corpus."
        actions={
          <ToggleGroup
            type="single"
            value={density}
            onValueChange={(v) => isDensity(v) && setDensity(v)}
            aria-label="Thumbnail density"
          >
            <ToggleGroupItem value="compact">Compact</ToggleGroupItem>
            <ToggleGroupItem value="standard">
              <LayoutGrid />
              Standard
            </ToggleGroupItem>
            <ToggleGroupItem value="large">Large</ToggleGroupItem>
          </ToggleGroup>
        }
      />

      <div className="-mt-4 flex flex-wrap items-center gap-2">
        {GALLERY_CATEGORIES.map((c) => (
          <Button
            key={c.id}
            size="sm"
            variant={category === c.id ? "chipActive" : "chip"}
            onClick={() => setCategory(c.id)}
          >
            {c.label} ({data?.counts[c.id] ?? 0})
          </Button>
        ))}
        <Card className="ml-auto flex w-80 items-center gap-2 px-3">
          <Search className="size-4 text-muted-foreground" />
          <Input
            aria-label="Filter thumbnails"
            placeholder="Filter thumbnails by title, tag, status…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            className="border-0 shadow-none"
          />
        </Card>
      </div>

      {data && data.items.length === 0 ? (
        <Card className="flex flex-col items-center gap-2 rounded-2xl border-0 px-6 py-16 text-center shadow-soft">
          <h2 className="text-headline-md text-foreground">No documents match your filters</h2>
          <p className="text-body-sm text-muted-foreground">
            Try another category or clear the search to see more of your archive.
          </p>
          <Button variant="soft" className="mt-2" onClick={() => setText("")}>
            Clear search
          </Button>
        </Card>
      ) : null}

      <ul className={cn("grid gap-5", GRID[density])}>
        {data?.items.map((doc) => (
          <li key={doc.id}>
            <Card className="flex h-full flex-col overflow-hidden rounded-2xl border-0 shadow-soft">
              <div className={cn("bg-surface-low p-2", density === "compact" ? "h-44" : "h-72")}>
                <DocumentThumbnail doc={doc} />
              </div>
              <div className="flex flex-col gap-2 p-4">
                <h2 className="truncate text-label-lg text-foreground">{doc.title}</h2>
                <p className="text-body-sm text-muted-foreground">
                  {doc.kind} • {formatDate(doc.date)} • {doc.pages}{" "}
                  {doc.pages === 1 ? "page" : "pages"}
                </p>
                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-label-sm">
                  <span className="flex items-center gap-1.5 text-primary">
                    <span className="size-2 rounded-full bg-primary" aria-hidden />
                    Indexed
                  </span>
                  <span className="text-muted-foreground">
                    {doc.format} • {formatFileSize(doc.sizeBytes)}
                  </span>
                </div>
              </div>
            </Card>
          </li>
        ))}
      </ul>

      <div className="flex items-center justify-between border-t pt-6">
        <p className="text-body-sm text-muted-foreground">
          Showing{" "}
          <strong className="text-foreground">
            {first}–{last}
          </strong>{" "}
          of <strong className="text-foreground">{total}</strong> ingested documents
        </p>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" disabled={page === 1} onClick={() => setPage(page - 1)}>
            Previous
          </Button>
          {Array.from({ length: pageCount }, (_, i) => i + 1).map((p) => (
            <Button
              key={p}
              size="icon"
              variant={p === page ? "default" : "ghost"}
              onClick={() => setPage(p)}
              aria-current={p === page ? "page" : undefined}
            >
              {p}
            </Button>
          ))}
          <Button
            variant="ghost"
            size="sm"
            disabled={page === pageCount}
            onClick={() => setPage(page + 1)}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}
