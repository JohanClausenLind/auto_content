import type { BaseTokens, ColorTokenKey } from "./tokens";

export interface ThemePreset {
  id: string;
  name: string;
  description: string;
  /** Which system colour scheme this preset answers to when following the OS. */
  scheme: "light" | "dark";
  fun?: boolean;
  base: BaseTokens;
  advanced?: Partial<Record<ColorTokenKey, string>>;
}

export const THEME_PRESETS: readonly ThemePreset[] = [
  {
    id: "dark",
    name: "Dark",
    description: "Default dark theme.",
    scheme: "dark",
    base: { bg: "#0f1115", fg: "#e6e8ee", panel: "#171a21", border: "#2a2f3a", accent: "#6ea8fe" },
  },
  {
    id: "light",
    name: "Light",
    description: "Default light theme.",
    scheme: "light",
    base: { bg: "#ffffff", fg: "#1b1f27", panel: "#f5f6f8", border: "#d9dce3", accent: "#2f6fed" },
  },
  {
    id: "midnight",
    name: "Midnight",
    description: "Deep blue-black for late sessions.",
    scheme: "dark",
    base: { bg: "#060a18", fg: "#dfe6ff", panel: "#0c1226", border: "#1c2547", accent: "#8b9cff" },
  },
  {
    id: "paper",
    name: "Paper",
    description: "Warm off-white with ink text.",
    scheme: "light",
    base: { bg: "#f6f1e7", fg: "#2b2620", panel: "#fdfaf3", border: "#d8cfbf", accent: "#9a4a1f" },
  },
  {
    id: "high-contrast",
    name: "High contrast",
    description: "Pure black and white, strong borders. Meets WCAG AAA.",
    scheme: "dark",
    base: { bg: "#000000", fg: "#ffffff", panel: "#0a0a0a", border: "#ffffff", accent: "#ffd400" },
    advanced: {
      muted: "#d8d8d8",
      danger: "#ff6b6b",
      success: "#5ee38a",
      warn: "#ffd400",
      hover: "#2a2a2a",
      "border-strong": "#ffffff",
    },
  },
  {
    id: "forest",
    name: "Forest",
    description: "Mossy greens.",
    scheme: "dark",
    fun: true,
    base: { bg: "#0e1a12", fg: "#dfeee2", panel: "#142418", border: "#27412f", accent: "#6fcf97" },
  },
  {
    id: "ocean",
    name: "Ocean",
    description: "Cool teal depths.",
    scheme: "dark",
    fun: true,
    base: { bg: "#071a24", fg: "#dcf1f7", panel: "#0c2733", border: "#1b4353", accent: "#3cc4e6" },
  },
  {
    id: "retrowave",
    name: "Retrowave",
    description: "Neon on violet.",
    scheme: "dark",
    fun: true,
    base: { bg: "#12071f", fg: "#f4e6ff", panel: "#1c0b30", border: "#3a1d5c", accent: "#ff5fbf" },
  },
  {
    id: "terminal",
    name: "Terminal",
    description: "Green phosphor on black.",
    scheme: "dark",
    fun: true,
    base: { bg: "#000000", fg: "#33ff66", panel: "#0a0f0a", border: "#1f4d2a", accent: "#33ff66" },
    advanced: { muted: "#25b34a" },
  },
];

export const DEFAULT_PRESET_FOR_SCHEME = { light: "light", dark: "dark" } as const;

export function findPreset(id: string): ThemePreset | undefined {
  return THEME_PRESETS.find((p) => p.id === id);
}
