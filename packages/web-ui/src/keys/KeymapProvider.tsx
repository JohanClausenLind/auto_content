/**
 * Owns the keymap: stored overrides, the live binding table, and the one global `keydown` listener
 * that dispatches them. Persists locally on every change and to the account (debounced) when a
 * sync adapter is supplied, exactly as the theme does.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { chordHasModifier, eventToChord, isTypingContext, MAX_CHORDS_PER_BINDING } from "./chord";
import { conflictsByAction, findConflicts, matchSequence, resolveKeymap, type KeyAction, type KeymapConflict, type ResolvedAction } from "./resolve";
import { DEFAULT_KEYMAP_STATE, type Binding, type KeymapState } from "./schema";
import { initialKeymapState, saveKeymapState } from "./storage";

/** Remote persistence. Failures are swallowed and surfaced as `syncError` only. */
export interface KeymapSyncAdapter {
  load(): Promise<KeymapState | null>;
  save(state: KeymapState): Promise<void>;
}

export interface KeymapContextValue {
  state: KeymapState;
  /** Every action with the binding that is actually live. */
  resolved: readonly ResolvedAction[];
  conflicts: readonly KeymapConflict[];
  conflictsFor: (actionId: string) => readonly KeymapConflict[];
  /** Chords pressed so far in an unfinished sequence, for the "g …" hint. */
  pending: readonly string[];
  syncError: string | null;
  bindingFor(actionId: string): Binding | null;
  setBinding(actionId: string, binding: Binding | null): void;
  resetBinding(actionId: string): void;
  resetAll(): void;
  /** Suspend dispatch while the editor is capturing a keypress. */
  setCapturing(on: boolean): void;
  capturing: boolean;
}

const KeymapContext = createContext<KeymapContextValue | null>(null);

export interface KeymapProviderProps {
  children: ReactNode;
  /** The application's action table. Memoise it: identity change re-registers the listener. */
  actions: readonly KeyAction[];
  sync?: KeymapSyncAdapter | null;
  /** Override for tests / SSR; defaults to localStorage or the built-in default. */
  initialState?: KeymapState;
  saveDebounceMs?: number;
  /** How long a partial sequence waits for its second chord. */
  sequenceTimeoutMs?: number;
}

export function KeymapProvider({
  children,
  actions,
  sync = null,
  initialState,
  saveDebounceMs = 400,
  sequenceTimeoutMs = 1400,
}: KeymapProviderProps) {
  const [state, setState] = useState<KeymapState>(() => initialState ?? initialKeymapState());
  const [pending, setPending] = useState<readonly string[]>([]);
  const [capturing, setCapturing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const dirty = useRef(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sequenceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const resolved = useMemo(() => resolveKeymap(actions, state), [actions, state]);
  const conflicts = useMemo(() => findConflicts(resolved), [resolved]);
  const conflictIndex = useMemo(() => conflictsByAction(conflicts), [conflicts]);

  // Persist locally immediately; remotely debounced.
  useEffect(() => {
    saveKeymapState(state);
    if (!sync || !dirty.current) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      sync.save(state).then(
        () => setSyncError(null),
        (err: unknown) => setSyncError(err instanceof Error ? err.message : "Could not save shortcuts to the server."),
      );
    }, saveDebounceMs);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [state, sync, saveDebounceMs]);

  // Pull from the account once per adapter; the server wins if it has something.
  useEffect(() => {
    if (!sync) return;
    let cancelled = false;
    sync.load().then(
      (remote) => {
        if (cancelled || !remote) return;
        dirty.current = false;
        setState(remote);
      },
      (err: unknown) => {
        if (!cancelled) setSyncError(err instanceof Error ? err.message : "Could not load shortcuts from the server.");
      },
    );
    return () => {
      cancelled = true;
    };
  }, [sync]);

  // Keep the dispatcher reading current values without re-registering on every state change.
  // Written in an effect, not during render: it lands before any keystroke can be handled.
  const resolvedRef = useRef(resolved);
  useEffect(() => {
    resolvedRef.current = resolved;
  }, [resolved]);
  const bufferRef = useRef<string[]>([]);

  const clearSequence = useCallback(() => {
    bufferRef.current = [];
    setPending([]);
    if (sequenceTimer.current) {
      clearTimeout(sequenceTimer.current);
      sequenceTimer.current = null;
    }
  }, []);

  useEffect(() => {
    if (capturing) return; // the editor owns the keyboard while recording
    if (typeof window === "undefined") return;

    const onKeyDown = (event: KeyboardEvent) => {
      const chord = eventToChord(event);
      if (chord === null) return;
      // Typing wins, unless the chord needs a modifier and so cannot be part of ordinary input.
      if (isTypingContext(event) && !chordHasModifier(chord)) {
        clearSequence();
        return;
      }

      const buffer = [...bufferRef.current, chord];
      const { exact, partial } = matchSequence(resolvedRef.current, buffer);

      if (exact) {
        event.preventDefault();
        clearSequence();
        exact.action.run();
        return;
      }
      if (partial && buffer.length < MAX_CHORDS_PER_BINDING) {
        event.preventDefault();
        bufferRef.current = buffer;
        setPending(buffer);
        if (sequenceTimer.current) clearTimeout(sequenceTimer.current);
        sequenceTimer.current = setTimeout(clearSequence, sequenceTimeoutMs);
        return;
      }
      // Not ours: drop any half-typed sequence and let the browser have the key.
      clearSequence();
    };

    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      if (sequenceTimer.current) clearTimeout(sequenceTimer.current);
    };
  }, [capturing, clearSequence, sequenceTimeoutMs]);

  const update = useCallback((fn: (s: KeymapState) => KeymapState) => {
    dirty.current = true;
    setState(fn);
  }, []);

  const value = useMemo<KeymapContextValue>(() => {
    const lockedIds = new Set(actions.filter((a) => a.locked === true).map((a) => a.id));
    return {
      state,
      resolved,
      conflicts,
      conflictsFor: (id) => conflictIndex.get(id) ?? [],
      pending,
      syncError,
      capturing,
      setCapturing,
      bindingFor: (id) => resolved.find((r) => r.action.id === id)?.binding ?? null,
      setBinding: (id, binding) => {
        if (lockedIds.has(id)) return;
        update((s) => ({ ...s, overrides: { ...s.overrides, [id]: binding } }));
      },
      resetBinding: (id) =>
        update((s) => {
          if (!Object.prototype.hasOwnProperty.call(s.overrides, id)) return s;
          const { [id]: _dropped, ...rest } = s.overrides;
          return { ...s, overrides: rest };
        }),
      resetAll: () => update(() => DEFAULT_KEYMAP_STATE),
    };
  }, [actions, state, resolved, conflicts, conflictIndex, pending, syncError, capturing, update]);

  return <KeymapContext.Provider value={value}>{children}</KeymapContext.Provider>;
}

export function useKeymap(): KeymapContextValue {
  const ctx = useContext(KeymapContext);
  if (!ctx) throw new Error("useKeymap must be used inside <KeymapProvider>");
  return ctx;
}

/** Non-throwing variant for components that render with or without a provider. */
export function useOptionalKeymap(): KeymapContextValue | null {
  return useContext(KeymapContext);
}
