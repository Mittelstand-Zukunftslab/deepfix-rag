import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Appearance = "light" | "dark";

interface AppearanceState {
  appearance: Appearance;
  setAppearance: (appearance: Appearance) => void;
  toggleAppearance: () => void;
}

/**
 * The admin chat's light/dark choice.
 *
 * It is explicit and remembered, not taken from `prefers-color-scheme`: the
 * `dark` class lives on the document element, so following the operating system
 * would silently change every other page for an admin who has never asked for it.
 */
export const useAppearanceStore = create<AppearanceState>()(
  persist(
    (set) => ({
      appearance: "light",
      setAppearance: (appearance) => set({ appearance }),
      toggleAppearance: () =>
        set((state) => ({ appearance: state.appearance === "dark" ? "light" : "dark" })),
    }),
    { name: "searchdocs-appearance" },
  ),
);

/** Puts the stored choice on the document element, which is what `dark:` reads. */
export function applyAppearance(appearance: Appearance): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", appearance === "dark");
}
