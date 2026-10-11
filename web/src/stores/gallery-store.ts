import { create } from "zustand";

export type GalleryDensity = "compact" | "standard" | "large";

interface GalleryState {
  density: GalleryDensity;
  category: string;
  text: string;
  page: number;
  setDensity: (d: GalleryDensity) => void;
  setCategory: (c: string) => void;
  setText: (t: string) => void;
  setPage: (p: number) => void;
}

export const useGalleryStore = create<GalleryState>((set) => ({
  density: "standard",
  category: "all",
  text: "",
  page: 1,
  setDensity: (density) => set({ density }),
  setCategory: (category) => set({ category, page: 1 }),
  setText: (text) => set({ text, page: 1 }),
  setPage: (page) => set({ page }),
}));
