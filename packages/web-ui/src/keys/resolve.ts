/** Turning an action table plus stored overrides into the bindings that are actually live. */

import { bindingsEqual, isPrefixOf } from "./chord";
import type { Binding, KeymapState } from "./schema";

export interface KeyAction {
  id: string;
  /** Shown in the shortcut sheet and the settings editor. */
  title: string;
  /** Heading to file it under, e.g. "Navigate". */
  group: string;
  /** What it does with no override. `null` ships unbound. */
  defaultBinding: Binding | null;
  /** Bindings the operator cannot change — the palette's ⌘K is the browser-level entry point. */
  locked?: boolean;
  run(): void;
}

export interface ResolvedAction {
  action: KeyAction;
  binding: Binding | null;
  /** True when the live binding differs from the action's default. */
  custom: boolean;
}

export type ConflictKind = "duplicate" | "shadow";

export interface KeymapConflict {
  actionId: string;
  otherId: string;
  kind: ConflictKind;
}

/** Apply overrides to the action table. A locked action always keeps its default. */
export function resolveKeymap(actions: readonly KeyAction[], state: KeymapState): ResolvedAction[] {
  return actions.map((action) => {
    if (action.locked === true) return { action, binding: action.defaultBinding, custom: false };
    const has = Object.prototype.hasOwnProperty.call(state.overrides, action.id);
    if (!has) return { action, binding: action.defaultBinding, custom: false };
    const binding = state.overrides[action.id] ?? null;
    return { action, binding, custom: !bindingsEqual(binding, action.defaultBinding) };
  });
}

/**
 * Every pair that cannot coexist: the same sequence bound twice, or a short binding that would
 * always fire before a longer one starting with it could complete.
 */
export function findConflicts(resolved: readonly ResolvedAction[]): KeymapConflict[] {
  const out: KeymapConflict[] = [];
  for (let i = 0; i < resolved.length; i++) {
    const a = resolved[i];
    if (!a || !a.binding) continue;
    for (let j = i + 1; j < resolved.length; j++) {
      const b = resolved[j];
      if (!b || !b.binding) continue;
      if (bindingsEqual(a.binding, b.binding)) {
        out.push({ actionId: a.action.id, otherId: b.action.id, kind: "duplicate" });
      } else if (isPrefixOf(a.binding, b.binding) || isPrefixOf(b.binding, a.binding)) {
        out.push({ actionId: a.action.id, otherId: b.action.id, kind: "shadow" });
      }
    }
  }
  return out;
}

/** Conflicts indexed by action id, both directions, so the editor can flag either row. */
export function conflictsByAction(conflicts: readonly KeymapConflict[]): Map<string, KeymapConflict[]> {
  const map = new Map<string, KeymapConflict[]>();
  const push = (id: string, c: KeymapConflict) => {
    const list = map.get(id);
    if (list) list.push(c);
    else map.set(id, [c]);
  };
  for (const c of conflicts) {
    push(c.actionId, c);
    push(c.otherId, { actionId: c.otherId, otherId: c.actionId, kind: c.kind });
  }
  return map;
}

export interface SequenceMatch {
  /** The action to fire, if the buffer completes a binding. */
  exact: ResolvedAction | null;
  /** True when the buffer starts some longer binding, so we should keep waiting. */
  partial: boolean;
}

/**
 * Match a pressed sequence. An exact hit wins even when a longer binding also starts with it —
 * `findConflicts` reports that pair so it can be fixed rather than silently swallowed.
 */
export function matchSequence(resolved: readonly ResolvedAction[], buffer: readonly string[]): SequenceMatch {
  if (buffer.length === 0) return { exact: null, partial: false };
  let exact: ResolvedAction | null = null;
  let partial = false;
  for (const entry of resolved) {
    if (!entry.binding) continue;
    if (exact === null && bindingsEqual(entry.binding, buffer)) exact = entry;
    else if (isPrefixOf(buffer, entry.binding)) partial = true;
  }
  return { exact, partial };
}
