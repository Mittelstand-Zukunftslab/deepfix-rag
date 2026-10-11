import { sendMessageInputSchema, type ChatMessage, type SendMessageInput } from "@/schemas/chat";
import { mockDelay } from "./mock-delay";

const SEED: ChatMessage[] = [
  {
    id: "m-1",
    role: "user",
    content: "What was our total utility expenditure across all properties in May 2026?",
    createdAt: "2026-05-31T10:42:00",
  },
  {
    id: "m-2",
    role: "assistant",
    content:
      "Aggregated utility disbursements across registered properties for May 2026 total $349.70. The verified ledger breakdown derived from processed invoices comprises:",
    createdAt: "2026-05-31T10:42:05",
    breakdown: {
      items: [
        { label: "Northwind Power (Electricity & grid)", amount: 142.52 },
        { label: "Cascade Water Authority (Usage + stormwater)", amount: 71.05 },
        { label: "Vantage Telecom (Site data uplink)", amount: 136.13 },
      ],
      totalLabel: "Total consolidated utility cost",
      total: 349.7,
    },
    sources: ["Doc #402", "Doc #418"],
  },
];

export const chatService = {
  async getConversation(): Promise<ChatMessage[]> {
    await mockDelay();
    return SEED;
  },
  async sendMessage(input: SendMessageInput): Promise<ChatMessage> {
    const { content } = sendMessageInputSchema.parse(input);
    await mockDelay(700);
    return {
      id: crypto.randomUUID(),
      role: "assistant",
      content: `I searched your archive for "${content}". I found 3 related documents; the most relevant is shown in the sources below.`,
      createdAt: new Date().toISOString(),
      sources: ["Doc #402"],
    };
  },
};
