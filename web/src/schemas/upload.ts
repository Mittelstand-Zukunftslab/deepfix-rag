import { z } from "zod";

export const uploadStatusSchema = z.enum(["processed", "processing", "failed"]);

export const uploadedFileSchema = z.object({
  id: z.string(),
  name: z.string(),
  sizeBytes: z.number(),
  uploadedAt: z.string(),
  kind: z.enum(["document", "image"]),
  status: uploadStatusSchema,
  progress: z.number().min(0).max(100),
});
export type UploadedFile = z.infer<typeof uploadedFileSchema>;

export const ACCEPTED_UPLOAD_TYPES = ["application/pdf", "image/tiff", "image/png", "image/jpeg"];
export const MAX_UPLOAD_BYTES = 50_000_000;
