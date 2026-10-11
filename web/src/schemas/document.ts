import { z } from "zod";

export const documentCategorySchema = z.enum([
  "utility",
  "legal",
  "receipt",
  "banking",
  "invoice",
  "medical",
  "tax",
]);
export type DocumentCategory = z.infer<typeof documentCategorySchema>;

export const searchResultSchema = z.object({
  id: z.string(),
  title: z.string(),
  subtype: z.string(),
  category: documentCategorySchema,
  date: z.string(),
  amount: z.number(),
  matchScore: z.number(),
});
export type SearchResult = z.infer<typeof searchResultSchema>;

export const searchFiltersSchema = z.object({
  query: z.string(),
  docType: z.string().optional(),
  period: z.string().optional(),
  minAmount: z.number().optional(),
});
export type SearchFilters = z.infer<typeof searchFiltersSchema>;

export const feedbackSchema = z.object({
  documentId: z.string(),
  vote: z.enum(["up", "down"]),
});
export type Feedback = z.infer<typeof feedbackSchema>;

export const galleryDocumentSchema = z.object({
  id: z.string(),
  title: z.string(),
  kind: z.string(),
  category: documentCategorySchema,
  status: z.string(),
  date: z.string(),
  pages: z.number(),
  format: z.enum(["PDF", "JPG"]),
  sizeBytes: z.number(),
  total: z.number().optional(),
  lines: z.array(z.object({ label: z.string(), amount: z.number() })),
});
export type GalleryDocument = z.infer<typeof galleryDocumentSchema>;

export const galleryQuerySchema = z.object({
  category: z.string().optional(),
  text: z.string().optional(),
  page: z.number().int().min(1).default(1),
});
export type GalleryQuery = z.infer<typeof galleryQuerySchema>;
