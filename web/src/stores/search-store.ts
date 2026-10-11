import { create } from "zustand";

import type { SearchFilters } from "@/schemas/document";

const DEFAULT_FILTERS: SearchFilters = {
  query: "Find utility invoices over $200 from Q2 2026",
  docType: "Utility invoices",
  period: "Q2 2026",
  minAmount: 200,
};

interface SearchState {
  draft: string;
  filters: SearchFilters;
  votes: Record<string, "up" | "down">;
  setDraft: (draft: string) => void;
  submit: () => void;
  resetFilters: () => void;
  vote: (id: string, vote: "up" | "down") => void;
}

export const useSearchStore = create<SearchState>((set) => ({
  draft: DEFAULT_FILTERS.query,
  filters: DEFAULT_FILTERS,
  votes: {},
  setDraft: (draft) => set({ draft }),
  submit: () => set((s) => ({ filters: { ...s.filters, query: s.draft } })),
  resetFilters: () => set((s) => ({ filters: { query: s.draft } })),
  vote: (id, vote) => set((s) => ({ votes: { ...s.votes, [id]: vote } })),
}));
