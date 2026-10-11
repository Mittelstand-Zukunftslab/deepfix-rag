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
