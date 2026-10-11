import {
  feedbackSchema,
  type Feedback,
  type SearchFilters,
  type SearchResult,
} from "@/schemas/document";
import { mockDelay } from "./mock-delay";

const RESULTS: SearchResult[] = [
  {
    id: "doc-101",
    title: "Northwind Power & Light: Electricity bill",
    subtype: "Utility bill",
    category: "utility",
    date: "2026-05-28",
    amount: 284.15,
    matchScore: 99.8,
  },
  {
    id: "doc-102",
    title: "Cascade Water District: Utility notice",
    subtype: "Municipal water",
    category: "utility",
    date: "2026-06-12",
    amount: 312.4,
    matchScore: 98.4,
  },
  {
    id: "doc-103",
    title: "Meridian Savings Bank: Q2 statement",
    subtype: "Escrow utility",
    category: "banking",
    date: "2026-06-30",
    amount: 4810,
    matchScore: 96.2,
  },
  {
    id: "doc-104",
    title: "Pinnacle Gas: Heating & thermal supply",
    subtype: "Natural gas",
    category: "utility",
    date: "2026-04-22",
    amount: 218.6,
    matchScore: 94.8,
  },
  {
    id: "doc-105",
    title: "Telco Pacific: Dedicated trunk billing",
    subtype: "Telecom",
    category: "invoice",
    date: "2026-05-14",
    amount: 450,
    matchScore: 91.1,
  },
  {
    id: "doc-106",
    title: "Valley Sanitation: Environmental haul",
    subtype: "Sanitation",
    category: "utility",
    date: "2026-06-04",
    amount: 225,
    matchScore: 88.5,
  },
];

export const searchService = {
  async search(filters: SearchFilters): Promise<SearchResult[]> {
    await mockDelay();
    const q = filters.query.trim().toLowerCase();
    return RESULTS.filter((r) => (filters.minAmount ? r.amount > filters.minAmount : true)).filter(
      (r) =>
        !q ||
        q.includes("invoice") ||
        r.title.toLowerCase().includes(q) ||
        r.subtype.toLowerCase().includes(q),
    );
  },
  async sendFeedback(input: Feedback): Promise<Feedback> {
    await mockDelay(100);
    return feedbackSchema.parse(input);
  },
};
