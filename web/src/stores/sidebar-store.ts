import { create } from "zustand";
import { persist } from "zustand/middleware";

interface SidebarState {
  /** Whether the app navigation is expanded; collapsed is the icon rail on a wide viewport. */
  isCollapsed: boolean;
  toggleSidebar: () => void;
}

/** Client UI state for the app sidebar; persisted so the preference survives reloads. */
export const useSidebarStore = create<SidebarState>()(
  persist(
    (set) => ({
      isCollapsed: false,
      toggleSidebar: () => set((state) => ({ isCollapsed: !state.isCollapsed })),
    }),
    { name: "searchdocs-sidebar" },
  ),
);
