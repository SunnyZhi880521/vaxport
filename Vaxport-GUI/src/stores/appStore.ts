import { create } from "zustand";

type Theme = "dark" | "light" | "system";

function applyTheme(theme: Theme) {
  const isLight =
    theme === "system"
      ? !window.matchMedia("(prefers-color-scheme: dark)").matches
      : theme === "light";
  document.documentElement.classList.toggle("light", isLight);
}

// Listen for system theme changes when in "system" mode
if (typeof window !== "undefined") {
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    const { theme } = useAppStore.getState();
    if (theme === "system") applyTheme("system");
  });
}

interface ChartItem {
  id: string;
  filePath: string;
  image: string; // base64
  timestamp: number;
}

interface AppState {
  theme: Theme;
  fontSize: number;
  density: "comfy" | "compact";
  sidebarOpen: boolean;
  rightPanelOpen: boolean;
  rightPanelTab: "schema" | "charts" | "skills" | "ear";
  debugMode: boolean;
  backendOnline: boolean;
  charts: ChartItem[];

  setTheme: (theme: Theme) => void;
  setFontSize: (size: number) => void;
  setDensity: (density: "comfy" | "compact") => void;
  toggleSidebar: () => void;
  toggleRightPanel: () => void;
  setRightPanelTab: (tab: "schema" | "charts" | "skills" | "ear") => void;
  toggleDebug: () => void;
  setBackendOnline: (online: boolean) => void;
  addChart: (chart: Omit<ChartItem, "id" | "timestamp">) => void;
  clearCharts: () => void;
}

function applyFontSize(size: number) {
  document.documentElement.style.setProperty("--chat-font-size", size + "px");
}

function applyDensity(density: "comfy" | "compact") {
  document.documentElement.classList.toggle("density-compact", density === "compact");
}

export const useAppStore = create<AppState>((set) => ({
  theme: "light",
  fontSize: 14,
  density: "comfy",
  sidebarOpen: true,
  rightPanelOpen: true,
  rightPanelTab: "schema",
  debugMode: false,
  backendOnline: false,
  charts: [],

  setTheme: (theme) => {
    applyTheme(theme);
    set({ theme });
  },
  setFontSize: (size) => {
    applyFontSize(size);
    set({ fontSize: size });
  },
  setDensity: (density) => {
    applyDensity(density);
    set({ density });
  },
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  toggleRightPanel: () => set((s) => ({ rightPanelOpen: !s.rightPanelOpen })),
  setRightPanelTab: (tab) => set({ rightPanelTab: tab }),
  toggleDebug: () => set((s) => ({ debugMode: !s.debugMode })),
  setBackendOnline: (online) => set({ backendOnline: online }),
  addChart: (chart) =>
    set((s) => ({
      charts: [
        ...s.charts,
        {
          id: crypto.randomUUID(),
          filePath: chart.filePath,
          image: chart.image,
          timestamp: Date.now(),
        },
      ],
    })),
  clearCharts: () => set({ charts: [] }),
}));

// Apply default theme on load
if (typeof window !== "undefined") {
  applyTheme(useAppStore.getState().theme);
  applyFontSize(useAppStore.getState().fontSize);
  applyDensity(useAppStore.getState().density);
}
