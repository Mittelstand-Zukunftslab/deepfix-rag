import { useSuspenseQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  CloudUpload,
  Eye,
  FileText,
  Image,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { formatFileSize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ACCEPTED_UPLOAD_TYPES, MAX_UPLOAD_BYTES, type UploadedFile } from "@/schemas/upload";
import { uploadsQueryOptions, useRemoveUpload, useUploadFiles } from "./upload-queries";

const relativeFormatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

const relativeTime = (iso: string): string => {
  const minutes = Math.round((new Date(iso).getTime() - Date.now()) / 60_000);
  if (Math.abs(minutes) < 60) return relativeFormatter.format(minutes, "minute");
  return relativeFormatter.format(Math.round(minutes / 60), "hour");
};

export function UploadPage() {
  const { data: files } = useSuspenseQuery(uploadsQueryOptions());
  const upload = useUploadFiles();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const handleFiles = (list: FileList | null) => {
    if (!list) return;
    const all = Array.from(list);
    const valid = all.filter(
      (f) => ACCEPTED_UPLOAD_TYPES.includes(f.type) && f.size <= MAX_UPLOAD_BYTES,
    );
    if (valid.length < all.length)
      toast.error("Some files were skipped: only PDF, TIFF, PNG or JPEG up to 50 MB.");
    if (valid.length)
      upload.mutate(valid, { onSuccess: () => toast.success(`${valid.length} file(s) uploaded`) });
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    handleFiles(e.dataTransfer.files);
  };

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-10 px-8 py-10">
      <PageHeader
        title="Data upload"
        description="Upload files to parse, index, and organize your document intelligence pipeline."
      />
      <section
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          "-mt-6 flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed bg-card px-6 py-12 text-center transition-colors",
          dragging ? "border-primary bg-surface-low" : "border-accent",
        )}
      >
        <div className="flex size-16 items-center justify-center rounded-xl bg-surface-low text-primary">
          <CloudUpload className="size-8" />
        </div>
        <h2 className="text-headline-md text-foreground">Drag and drop files here</h2>
        <p className="text-body-sm text-muted-foreground">
          Supports PDF, TIFF, PNG, and JPEG up to 50 MB each
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPTED_UPLOAD_TYPES.join(",")}
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
        <Button
          size="lg"
          className="mt-3"
          onClick={() => inputRef.current?.click()}
          disabled={upload.isPending}
        >
          <Plus />
          Upload files
        </Button>
      </section>

      <section className="flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <h2 className="text-headline-md text-foreground">Recent uploads</h2>
          <span className="text-label-md text-muted-foreground">{files.length} files</span>
        </div>
        <Card className="overflow-hidden shadow-soft">
          <ul className="divide-y divide-border">
            {files.map((f) => (
              <UploadRow key={f.id} file={f} />
            ))}
          </ul>
        </Card>
      </section>
    </div>
  );
}

function UploadRow({ file }: { file: UploadedFile }) {
  const remove = useRemoveUpload();
  const Icon = file.kind === "image" ? Image : FileText;
  return (
    <li className="flex items-center gap-4 px-5 py-4">
      <div className="flex size-10 items-center justify-center rounded-lg bg-surface-low text-muted-foreground">
        <Icon className="size-5" />
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <p className="truncate text-label-lg text-foreground">{file.name}</p>
        <p className="text-body-sm text-muted-foreground">
          {formatFileSize(file.sizeBytes)} • Uploaded {relativeTime(file.uploadedAt)}
        </p>
      </div>
      <Badge variant="match" className="gap-1">
        {file.status === "processed" ? (
          <CheckCircle2 className="size-3.5" />
        ) : (
          <RefreshCw className="size-3.5 animate-spin" />
        )}
        {file.status === "processed" ? "Processed" : `Processing… ${file.progress} %`}
      </Badge>
      <Button variant="icon" size="icon" aria-label={`Preview ${file.name}`}>
        <Eye />
      </Button>
      <Button
        variant="icon"
        size="icon"
        aria-label={`Delete ${file.name}`}
        onClick={() => remove.mutate(file.id)}
      >
        <Trash2 />
      </Button>
    </li>
  );
}
