import { z } from "zod";

export const chatBreakdownSchema = z.object({
  items: z.array(z.object({ label: z.string(), amount: z.number() })),
  totalLabel: z.string(),
  total: z.number(),
});

export const chatMessageSchema = z.object({
  id: z.string(),
  role: z.enum(["user", "assistant"]),
  content: z.string(),
  createdAt: z.string(),
  breakdown: chatBreakdownSchema.optional(),
  sources: z.array(z.string()).optional(),
});
export type ChatMessage = z.infer<typeof chatMessageSchema>;

export const sendMessageInputSchema = z.object({
  content: z.string().trim().min(1, "Please type a question first."),
});
export type SendMessageInput = z.infer<typeof sendMessageInputSchema>;
