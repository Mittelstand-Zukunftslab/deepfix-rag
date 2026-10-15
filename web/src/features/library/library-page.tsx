import { useRef, useState, type DragEvent } from "react";

import { PageHeader } from "@/components/layout/app-shell";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useRebuildIndex } from "@/features/index/index-queries";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { uploadSelectionSchema } from "@/schemas/library";
import type { IndexStatus } from "@/services/index-service";
import type { LibraryDocument } from "@/services/library-service";

import { useDeleteDocument, useUploadDocument } from "./library-queries";

const STATUS_LABEL: Record<LibraryDocument["status"], string> = {
  pending: "Pending",
  indexing: "Indexing",
  indexed: "Indexed",
  deleting: "Deleting",
  failed: "Failed",
};

const INDEX_LABEL: Record<IndexStatus["status"], string> = {
  empty: "Empty",
  updating: "Updating",
  rebuilding: "Rebuilding",
  ready: "Ready",
  ready_with_errors: "Ready with errors",
  failed: "Failed",
};

const EMPTY_INDEX: IndexStatus = {
  status: "empty",
  document_count: 0,
  chunk_count: 0,
  pending_document_count: 0,
  updated_at: null,
  config_path: "",
  serving_build_id: null,
  error: null,
};

function issueLabel(issue: LibraryDocument["issues"][number]): string {
  if (issue.kind === "scanned") return `Page ${issue.page} looks scanned`;
  return `Page ${issue.page} has garbled text`;
}

export function LibraryPage({
  documents,
  index = EMPTY_INDEX,
  canRebuild = false,
}: {
  documents: LibraryDocument[];
  index?: IndexStatus;
  canRebuild?: boolean;
}) {
  const upload = useUploadDocument();
  const remove = useDeleteDocument();
  const rebuild = useRebuildIndex();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (file: File | undefined) => {
    if (!file) {
      setError("Choose a file to upload.");
      return;
    }
    const selection = uploadSelectionSchema.safeParse({ name: file.name, size: file.size });
    if (!selection.success) {
      setError(selection.error.issues[0]?.message ?? "Upload a text, Markdown, or PDF file.");
      return;
    }
    setError(null);
    try {
      await upload.mutateAsync(file);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to upload this file. Try again.");
    } finally {
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const onDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    setDragging(false);
    if (event.dataTransfer.files.length > 1) {
      setError("Upload one file at a time.");
      return;
    }
    void submit(event.dataTransfer.files[0]);
  };

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-8 px-6 py-10 sm:px-8">
      <PageHeader
        title="Library"
        description="Add a text, Markdown, or PDF file to the shared corpus. Scanned and garbled PDF pages are marked. OCR is not available."
      />
      <section className="flex flex-col gap-3 rounded-2xl border border-border bg-card px-6 py-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-headline-md text-foreground">Index</h2>
          {canRebuild ? (
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  type="button"
                  className="h-11"
                  disabled={index.status === "rebuilding" || rebuild.isPending}
                >
                  {index.status === "rebuilding" || rebuild.isPending
                    ? "Rebuilding"
                    : "Rebuild index"}
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Rebuild the index?</AlertDialogTitle>
                  <AlertDialogDescription>
                    The current index remains available until the new build is ready.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel className="h-11">Cancel</AlertDialogCancel>
                  <AlertDialogAction
                    className="h-11"
                    onClick={() => {
                      rebuild.mutate(undefined, {
                        onError: (caught) => {
                          setError(
                            caught instanceof Error
                              ? caught.message
                              : "Unable to rebuild the index. Try again.",
                          );
                        },
                      });
                    }}
                  >
                    Rebuild index
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          ) : null}
        </div>
        <p className="flex flex-wrap items-center gap-2 text-body-sm text-muted-foreground">
          <span>Status</span>
          <Badge variant={index.status === "failed" ? "destructive" : "soft"}>
            {INDEX_LABEL[index.status]}
          </Badge>
          <span>Documents {index.document_count}</span>
          <span>Chunks {index.chunk_count}</span>
          <span>Pending {index.pending_document_count}</span>
        </p>
        {index.updated_at ? (
          <p className="text-body-sm text-muted-foreground">
            Updated {formatDateTime(index.updated_at)}
          </p>
        ) : null}
        {index.error ? <p className="text-body-sm text-destructive">{index.error}</p> : null}
      </section>
      <section
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          "flex flex-col items-start gap-3 rounded-2xl border-2 border-dashed bg-card px-6 py-8",
          dragging ? "border-primary bg-surface-low" : "border-accent",
        )}
      >
        <h2 className="text-headline-md text-foreground">Upload</h2>
        <p className="text-body-sm text-muted-foreground">
          One .txt, .md, or .pdf file, up to 20 MB. You can also drop it here.
        </p>
        <label className="inline-flex h-11 cursor-pointer items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground focus-within:ring-2 focus-within:ring-ring">
          <span>{upload.isPending ? "Uploading" : "Upload a file"}</span>
          <input
            ref={inputRef}
            type="file"
            accept=".txt,.md,.pdf,text/plain,text/markdown,application/pdf"
            className="sr-only"
            disabled={upload.isPending}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "upload-error" : undefined}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (!file) return;
              void submit(file);
            }}
          />
        </label>
        {error ? (
          <p id="upload-error" role="alert" className="text-body-sm text-destructive">
            {error}
          </p>
        ) : null}
      </section>

      <section className="flex flex-col gap-4" aria-busy={upload.isPending || remove.isPending}>
        <h2 className="text-headline-md text-foreground">Documents</h2>
        {documents.length === 0 ? (
          <p className="text-body-md text-muted-foreground">
            No documents yet. Upload a text, Markdown, or PDF file to start.
          </p>
        ) : (
          <Card className="overflow-hidden shadow-soft">
            <ul className="divide-y divide-border">
              {documents.map((document) => (
                <DocumentRow
                  key={document.doc_id}
                  document={document}
                  onDelete={(docId) => {
                    setError(null);
                    remove.mutate(docId, {
                      onError: (caught) => {
                        setError(
                          caught instanceof Error
                            ? caught.message
                            : "Unable to delete this file. Try again.",
                        );
                      },
                    });
                  }}
                />
              ))}
            </ul>
          </Card>
        )}
      </section>
    </div>
  );
}

function DocumentRow({
  document,
  onDelete,
}: {
  document: LibraryDocument;
  onDelete: (docId: string) => void;
}) {
  return (
    <li className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0 flex flex-col gap-2">
        <p className="truncate text-label-lg text-foreground">{document.filename}</p>
        <p className="text-body-sm text-muted-foreground">Uploaded by {document.uploader_email}</p>
        <p className="text-body-sm text-muted-foreground">
          Uploaded {formatDateTime(document.created_at)}
        </p>
        <p className="flex flex-wrap items-center gap-2 text-body-sm text-muted-foreground">
          <span>Status</span>
          <Badge variant={document.status === "failed" ? "destructive" : "soft"}>
            {STATUS_LABEL[document.status]}
          </Badge>
          {document.issues.map((issue) => (
            <Badge key={`${issue.page}-${issue.kind}`} variant="outline">
              {issueLabel(issue)}
            </Badge>
          ))}
        </p>
        {document.index_error ? (
          <p className="text-body-sm text-destructive">{document.index_error}</p>
        ) : null}
      </div>
      {document.can_delete ? (
        <Button
          type="button"
          variant="outline"
          className="h-11 shrink-0"
          aria-label={`Delete ${document.filename}`}
          onClick={() => onDelete(document.doc_id)}
        >
          Delete
        </Button>
      ) : null}
    </li>
  );
}
