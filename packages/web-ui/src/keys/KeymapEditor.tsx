/**
 * Settings ▸ Keyboard. Every action, its live binding, and a Record button that captures the next
 * keypress (or two, for a sequence). Conflicts are shown rather than prevented — the operator can
 * see which pair collides and decide which one to move.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "../components/Button";
import { formatBinding, isApplePlatform, MAX_CHORDS_PER_BINDING, eventToChord } from "./chord";
import { useKeymap } from "./KeymapProvider";
import type { ResolvedAction } from "./resolve";

export interface KeymapEditorProps {
  /** How long recording waits for a second chord before committing. */
  commitDelayMs?: number;
}

function groupActions(resolved: readonly ResolvedAction[]): { name: string; items: ResolvedAction[] }[] {
  const groups: { name: string; items: ResolvedAction[] }[] = [];
  for (const entry of resolved) {
    const existing = groups.find((g) => g.name === entry.action.group);
    if (existing) existing.items.push(entry);
    else groups.push({ name: entry.action.group, items: [entry] });
  }
  return groups;
}

export function KeymapEditor({ commitDelayMs = 800 }: KeymapEditorProps) {
  const { resolved, conflictsFor, setBinding, resetBinding, resetAll, setCapturing, syncError } = useKeymap();
  const apple = useMemo(() => isApplePlatform(), []);
  const groups = useMemo(() => groupActions(resolved), [resolved]);

  const [recordingId, setRecordingId] = useState<string | null>(null);
  const [captured, setCaptured] = useState<string[]>([]);
  const capturedRef = useRef<string[]>([]);
  const commitTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const titleById = useMemo(() => new Map(resolved.map((r) => [r.action.id, r.action.title])), [resolved]);

  const stop = useCallback(() => {
    if (commitTimer.current) {
      clearTimeout(commitTimer.current);
      commitTimer.current = null;
    }
    capturedRef.current = [];
    setCaptured([]);
    setRecordingId(null);
    setCapturing(false);
  }, [setCapturing]);

  const start = useCallback(
    (id: string) => {
      capturedRef.current = [];
      setCaptured([]);
      setRecordingId(id);
      setCapturing(true);
    },
    [setCapturing],
  );

  // While recording, this listener owns the keyboard: capture phase, everything prevented, so a
  // recorded chord never also triggers the action it is being bound to.
  useEffect(() => {
    if (recordingId === null) return;
    const id = recordingId;

    const commit = () => {
      const chords = capturedRef.current;
      if (chords.length > 0) setBinding(id, [...chords]);
      stop();
    };

    const onKeyDown = (event: KeyboardEvent) => {
      // Escape always abandons recording; it is the way out of a half-typed sequence.
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        stop();
        return;
      }
      const chord = eventToChord(event);
      if (chord === null) return; // a bare modifier: wait for the real key
      event.preventDefault();
      event.stopPropagation();

      capturedRef.current = [...capturedRef.current, chord];
      setCaptured(capturedRef.current);
      if (commitTimer.current) clearTimeout(commitTimer.current);
      if (capturedRef.current.length >= MAX_CHORDS_PER_BINDING) commit();
      else commitTimer.current = setTimeout(commit, commitDelayMs);
    };

    window.addEventListener("keydown", onKeyDown, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      if (commitTimer.current) clearTimeout(commitTimer.current);
    };
  }, [recordingId, commitDelayMs, setBinding, stop]);

  return (
    <div className="cf-keymap">
      <div className="cf-keymap__intro">
        <p className="cf-field__description">
          Press <kbd className="cf-kbd">?</kbd> anywhere for the shortcut sheet. Recording captures the next key, or two
          keys pressed in a row for a sequence like <kbd className="cf-kbd">G</kbd> then <kbd className="cf-kbd">I</kbd>.
          Escape cancels. Shortcuts without a modifier are ignored while you are typing in a field.
        </p>
        <Button variant="ghost" size="sm" onPress={resetAll}>
          Reset all to defaults
        </Button>
      </div>
      {syncError && (
        <p className="cf-keymap__sync" role="status">
          {syncError} Your shortcuts are still saved in this browser.
        </p>
      )}

      <div className="cf-keymap__live" role="status" aria-live="polite">
        {recordingId !== null &&
          (captured.length === 0
            ? `Recording ${titleById.get(recordingId) ?? ""}: press a key.`
            : `Captured ${formatBinding(captured, apple)}.`)}
      </div>

      {groups.map((group) => (
        <section key={group.name} className="cf-keymap__group">
          <h3 className="cf-keymap__heading">{group.name}</h3>
          <ul className="cf-keymap__rows">
            {group.items.map(({ action, binding, custom }) => {
              const recording = recordingId === action.id;
              const locked = action.locked === true;
              const rowConflicts = conflictsFor(action.id);
              return (
                <li key={action.id} className="cf-keymap__row" data-recording={recording || undefined}>
                  <span className="cf-keymap__name">
                    {action.title}
                    {custom && <span className="cf-keymap__badge">edited</span>}
                  </span>
                  <span className="cf-keymap__binding">
                    {recording ? (
                      <span className="cf-keymap__recording">
                        {captured.length === 0 ? "Press a key…" : formatBinding(captured, apple)}
                      </span>
                    ) : (
                      <kbd className="cf-kbd" data-unbound={binding === null || undefined}>
                        {formatBinding(binding, apple)}
                      </kbd>
                    )}
                  </span>
                  <span className="cf-keymap__actions">
                    {locked ? (
                      <span className="cf-keymap__locked" title="This one is fixed">
                        Fixed
                      </span>
                    ) : recording ? (
                      <Button variant="ghost" size="sm" onPress={stop}>
                        Cancel
                      </Button>
                    ) : (
                      <>
                        <Button
                          variant="ghost"
                          size="sm"
                          aria-label={`Record a shortcut for ${action.title}`}
                          onPress={() => start(action.id)}
                        >
                          Record
                        </Button>
                        {binding !== null && (
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-label={`Unbind ${action.title}`}
                            onPress={() => setBinding(action.id, null)}
                          >
                            Clear
                          </Button>
                        )}
                        {custom && (
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-label={`Reset ${action.title} to its default`}
                            onPress={() => resetBinding(action.id)}
                          >
                            Reset
                          </Button>
                        )}
                      </>
                    )}
                  </span>
                  {rowConflicts.length > 0 && (
                    <span className="cf-keymap__conflict" role="status">
                      {rowConflicts
                        .map((c) =>
                          c.kind === "duplicate"
                            ? `Same as ${titleById.get(c.otherId) ?? c.otherId}`
                            : `Shadows ${titleById.get(c.otherId) ?? c.otherId}`,
                        )
                        .join(" · ")}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
