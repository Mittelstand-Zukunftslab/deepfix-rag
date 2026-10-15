import { z } from "zod";

export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

export const uploadSelectionSchema = z.object({
  name: z.string().regex(/\.(txt|md|pdf)$/i, "Upload a text, Markdown, or PDF file."),
  size: z.number().max(MAX_UPLOAD_BYTES, "Files must be 20 MB or smaller."),
});
