import { Badge } from "@/components/ui/badge";
import { formatCurrency } from "@/lib/format";
import type { GalleryDocument } from "@/schemas/document";

// Stylized "paper" preview of a document, rendered from its parsed data.
export function DocumentThumbnail({ doc }: { doc: GalleryDocument }) {
  return (
    <div className="flex h-full flex-col gap-3 rounded-lg bg-card p-3 shadow-soft">
      <div className="flex items-center justify-between gap-2">
        <div className="flex gap-1">
          <Badge variant="match" className="capitalize">
            {doc.category}
          </Badge>
          <span className="text-label-sm text-primary">{doc.status}</span>
        </div>
        <span className="text-micro uppercase text-muted-foreground">{doc.kind}</span>
      </div>
      <div className="flex flex-col gap-1.5">
        <div className="h-1.5 w-3/4 rounded-full bg-surface-container" />
        <div className="h-1.5 w-1/2 rounded-full bg-surface-container" />
      </div>
      {doc.lines.length ? (
        <div className="flex flex-col gap-1">
          {doc.lines.map((l) => (
            <div key={l.label} className="flex justify-between text-micro text-muted-foreground">
              <span>{l.label}</span>
              <span>{formatCurrency(l.amount)}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-1 w-full rounded-full bg-surface-low" />
          ))}
        </div>
      )}
      <div className="mt-auto flex justify-between border-t pt-2 text-micro font-semibold text-foreground">
        <span className="text-muted-foreground">Page 1 of {doc.pages}</span>
        {doc.total ? <span>{formatCurrency(doc.total)}</span> : null}
      </div>
    </div>
  );
}
