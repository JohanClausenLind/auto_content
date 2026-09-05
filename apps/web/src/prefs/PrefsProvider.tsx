/**
 * App preferences: local first, synced to the account when signed in, painted onto the document
 * root as data attributes so the CSS can respond without every component reading context.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { DEFAULT_APP_PREFS, initialAppPrefs, saveAppPrefs, type AppPrefs } from "./schema";

export interface PrefsSyncAdapter {
  load(): Promise<AppPrefs | null>;
  save(prefs: AppPrefs): Promise<void>;
}

export interface PrefsContextValue {
  prefs: AppPrefs;
  syncError: string | null;
  set<K extends keyof AppPrefs>(key: K, value: AppPrefs[K]): void;
  reset(): void;
}

const PrefsContext = createContext<PrefsContextValue | null>(null);

export interface PrefsProviderProps {
  children: ReactNode;
  sync?: PrefsSyncAdapter | null;
  initialState?: AppPrefs;
  saveDebounceMs?: number;
}

/** Paint the parts the stylesheet cares about onto <html>. */
function applyPrefs(prefs: AppPrefs): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.dataset["density"] = prefs.density;
  if (prefs.reducedMotion === "system") delete root.dataset["motion"];
  else root.dataset["motion"] = prefs.reducedMotion === "always" ? "reduced" : "full";
}

export function PrefsProvider({ children, sync = null, initialState, saveDebounceMs = 400 }: PrefsProviderProps) {
  const [prefs, setPrefs] = useState<AppPrefs>(() => initialState ?? initialAppPrefs());
  const [syncError, setSyncError] = useState<string | null>(null);
  const dirty = useRef(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    applyPrefs(prefs);
  }, [prefs]);

  useEffect(() => {
    saveAppPrefs(prefs);
    if (!sync || !dirty.current) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      sync.save(prefs).then(
        () => setSyncError(null),
        (err: unknown) => setSyncError(err instanceof Error ? err.message : "Could not save preferences to the server."),
      );
    }, saveDebounceMs);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [prefs, sync, saveDebounceMs]);

  useEffect(() => {
    if (!sync) return;
    let cancelled = false;
    sync.load().then(
      (remote) => {
        if (cancelled || !remote) return;
        dirty.current = false;
        setPrefs(remote);
      },
      (err: unknown) => {
        if (!cancelled) setSyncError(err instanceof Error ? err.message : "Could not load preferences from the server.");
      },
    );
    return () => {
      cancelled = true;
    };
  }, [sync]);

  const update = useCallback((fn: (p: AppPrefs) => AppPrefs) => {
    dirty.current = true;
    setPrefs(fn);
  }, []);

  const value = useMemo<PrefsContextValue>(
    () => ({
      prefs,
      syncError,
      set: (key, val) => update((p) => ({ ...p, [key]: val })),
      reset: () => update(() => DEFAULT_APP_PREFS),
    }),
    [prefs, syncError, update],
  );

  return <PrefsContext.Provider value={value}>{children}</PrefsContext.Provider>;
}

export function usePrefs(): PrefsContextValue {
  const ctx = useContext(PrefsContext);
  if (!ctx) throw new Error("usePrefs must be used inside <PrefsProvider>");
  return ctx;
}

/** For components that must render outside the provider (the login page mounts above it). */
export function useOptionalPrefs(): PrefsContextValue | null {
  return useContext(PrefsContext);
}
