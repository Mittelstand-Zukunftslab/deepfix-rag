import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AdminChatState {
  /** The chat page's own sidebar, distinct from the app shell's navigation. */
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
}

/** Client UI state for the Hermes chat's thread sidebar; the preference survives reloads. */
export const useAdminChatStore = create<AdminChatState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
    }),
    { name: "searchdocs-admin-chat-sidebar" },
  ),
);
