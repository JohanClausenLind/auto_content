import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { DEFAULT_PRESET_FOR_SCHEME, findPreset, THEME_PRESETS, type ThemePreset } from "./presets";
import { applyTheme, resolveTheme, systemScheme, type PreviewTheme, type ResolvedTheme } from "./resolve";
import { coerceThemeState, isThemeState, MAX_CUSTOM_THEMES, parseThemeImport, SCALE_MAX, SCALE_MIN, type CustomTheme, type ImportResult, type ThemeState } from "./schema";
import { initialThemeState, saveThemeState } from "./storage";
import type { BaseTokens, ColorTokenKey, Scheme } from "./tokens";

/**
 * Remote persistence. Failures are swallowed and surfaced as `syncError` only.
 *
 * `load` returns whatever the store holds, unvalidated: the provider is the single place that
 * decides whether it is a usable state, so a row written by an older build is repaired here rather
 * than reaching `state` and crashing whatever reads a field it is missing.
 */
export interface ThemeSyncAdapter {
  load(): Promise<unknown>;
  save(state: ThemeState): Promise<void>;
}

export interface ThemeContextValue {
  state: ThemeState;
  resolved: ResolvedTheme;
  presets: readonly ThemePreset[];
  /** True while a live preview (customizer) is painted instead of `state`. */
  previewing: boolean;
  syncError: string | null;
  setPreset(id: string): void;
  followSystem(): void;
  /** Flip between the light and dark default presets. */
  toggleScheme(): void;
  setScale(scale: number): void;
  setReducedTransparency(on: boolean): void;
  preview(theme: PreviewTheme | null): void;
  saveCustomTheme(input: { name: string; base: BaseTokens; advanced?: Partial<Record<ColorTokenKey, string>>; id?: string }): CustomTheme | { error: string };
  deleteCustomTheme(id: string): void;
  activateCustomTheme(id: string): void;
  exportJson(): string;
  importJson(text: string): ImportResult;
  replaceState(next: ThemeState): void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export interface ThemeProviderProps {
  children: ReactNode;
  sync?: ThemeSyncAdapter | null;
  /** Override for tests / SSR; defaults to localStorage or the built-in default. */
  initialState?: ThemeState;
  saveDebounceMs?: number;
}

function newId(): string {
  return `custom-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

export function ThemeProvider({ children, sync = null, initialState, saveDebounceMs = 400 }: ThemeProviderProps) {
  const [state, setState] = useState<ThemeState>(() => initialState ?? initialThemeState());
  const [previewTheme, setPreviewTheme] = useState<PreviewTheme | null>(null);
  const [scheme, setScheme] = useState<Scheme>(() => systemScheme());
  const [syncError, setSyncError] = useState<string | null>(null);
  const dirty = useRef(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Follow prefers-color-scheme while in system mode.
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => setScheme(mq.matches ? "light" : "dark");
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);

  const resolved = useMemo(() => resolveTheme(state, scheme, previewTheme ?? undefined), [state, scheme, previewTheme]);

  // Paint whenever the resolution changes.
  useEffect(() => {
    applyTheme(state, previewTheme ?? undefined);
  }, [state, previewTheme, scheme]);

  // Persist locally immediately; remotely debounced.
  useEffect(() => {
    saveThemeState(state);
    if (!sync || !dirty.current) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      sync.save(state).then(
        () => setSyncError(null),
        (err: unknown) => setSyncError(err instanceof Error ? err.message : "Could not save theme to the server."),
      );
    }, saveDebounceMs);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [state, sync, saveDebounceMs]);

  // Pull from the server once per adapter; server wins if it has something.
  useEffect(() => {
    if (!sync) return;
    let cancelled = false;
    sync.load().then(
      (remote: unknown) => {
        if (cancelled) return;
        const repaired = coerceThemeState(remote);
        if (repaired) {
          // A row that had to be repaired is written back complete, so it stops being broken at
          // the source instead of being patched over on every load.
          dirty.current = !isThemeState(remote);
          setState(repaired);
        } else {
          // Nothing remote yet: push what we have so other devices pick it up.
          dirty.current = true;
          setState((s) => ({ ...s }));
        }
      },
      (err: unknown) => {
        if (!cancelled) setSyncError(err instanceof Error ? err.message : "Could not load theme from the server.");
      },
    );
    return () => {
      cancelled = true;
    };
  }, [sync]);

  const update = useCallback((fn: (s: ThemeState) => ThemeState) => {
    dirty.current = true;
    setState(fn);
  }, []);

  const value = useMemo<ThemeContextValue>(() => {
    const clampScale = (n: number) => Math.min(SCALE_MAX, Math.max(SCALE_MIN, Math.round(n * 100) / 100));
    return {
      state,
      resolved,
      presets: THEME_PRESETS,
      previewing: previewTheme !== null,
      syncError,
      setPreset: (id) => {
        if (!findPreset(id)) return;
        update((s) => ({ ...s, mode: "preset", preset: id, customId: null }));
      },
      followSystem: () => update((s) => ({ ...s, mode: "system", customId: null })),
      toggleScheme: () =>
        update((s) => {
          const current = resolveTheme(s, scheme).scheme;
          const next: Scheme = current === "dark" ? "light" : "dark";
          return { ...s, mode: "preset", preset: DEFAULT_PRESET_FOR_SCHEME[next], customId: null };
        }),
      setScale: (n) => update((s) => ({ ...s, scale: clampScale(n) })),
      setReducedTransparency: (on) => update((s) => ({ ...s, reducedTransparency: on })),
      preview: (t) => setPreviewTheme(t),
      saveCustomTheme: ({ name, base, advanced, id }) => {
        const trimmed = name.trim();
        if (!trimmed) return { error: "Give the theme a name." };
        const existing = id ? state.customThemes.find((c) => c.id === id) : undefined;
        if (!existing && state.customThemes.length >= MAX_CUSTOM_THEMES) {
          return { error: `You can keep up to ${MAX_CUSTOM_THEMES} custom themes. Delete one first.` };
        }
        const theme: CustomTheme = { id: existing?.id ?? newId(), name: trimmed, base, ...(advanced && Object.keys(advanced).length ? { advanced } : {}) };
        update((s) => ({
          ...s,
          mode: "custom",
          customId: theme.id,
          customThemes: existing ? s.customThemes.map((c) => (c.id === theme.id ? theme : c)) : [...s.customThemes, theme],
        }));
        setPreviewTheme(null);
        return theme;
      },
      deleteCustomTheme: (id) =>
        update((s) => {
          const customThemes = s.customThemes.filter((c) => c.id !== id);
          const wasActive = s.mode === "custom" && s.customId === id;
          return { ...s, customThemes, ...(wasActive ? { mode: "preset" as const, customId: null } : {}) };
        }),
      activateCustomTheme: (id) => {
        if (!state.customThemes.some((c) => c.id === id)) return;
        update((s) => ({ ...s, mode: "custom", customId: id }));
      },
      exportJson: () => JSON.stringify(state, null, 2),
      importJson: (text) => {
        const result = parseThemeImport(text);
        if (!result.ok) return result;
        if (result.kind === "state") {
          update(() => result.state);
        } else {
          const theme = result.theme;
          if (state.customThemes.length >= MAX_CUSTOM_THEMES && !state.customThemes.some((c) => c.id === theme.id)) {
            return { ok: false, error: `You can keep up to ${MAX_CUSTOM_THEMES} custom themes. Delete one first.` };
          }
          update((s) => ({
            ...s,
            mode: "custom",
            customId: theme.id,
            customThemes: s.customThemes.some((c) => c.id === theme.id)
              ? s.customThemes.map((c) => (c.id === theme.id ? theme : c))
              : [...s.customThemes, theme],
          }));
        }
        return result;
      },
      replaceState: (next) => update(() => next),
    };
  }, [state, resolved, previewTheme, syncError, scheme, update]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used inside <ThemeProvider>");
  return ctx;
}
