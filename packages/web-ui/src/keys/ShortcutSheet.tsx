/** The `?` overlay: every live shortcut, grouped, read-only. */

import { useMemo } from "react";
import { Dialog } from "../components/Dialog";
import { formatBinding, isApplePlatform } from "./chord";
import { useKeymap } from "./KeymapProvider";
import type { ResolvedAction } from "./resolve";

export interface ShortcutSheetProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  /** Rendered under the list — the app uses it to link to Settings ▸ Keyboard. */
  footer?: React.ReactNode;
}

function groupActions(resolved: readonly ResolvedAction[]): { name: string; items: ResolvedAction[] }[] {
  const groups: { name: string; items: ResolvedAction[] }[] = [];
  for (const entry of resolved) {
    if (!entry.binding) continue; // unbound actions are not shortcuts
    const existing = groups.find((g) => g.name === entry.action.group);
    if (existing) existing.items.push(entry);
    else groups.push({ name: entry.action.group, items: [entry] });
  }
  return groups;
}

export function ShortcutSheet({ isOpen, onOpenChange, footer }: ShortcutSheetProps) {
  const { resolved } = useKeymap();
  const apple = useMemo(() => isApplePlatform(), []);
  const groups = useMemo(() => groupActions(resolved), [resolved]);

  return (
    <Dialog
      title="Keyboard shortcuts"
      description="Press Esc to close. Sequences like G then I are typed one key after the other."
      size="lg"
      isOpen={isOpen}
      onOpenChange={onOpenChange}
    >
      <div className="cf-keysheet">
        {groups.map((group) => (
          <section key={group.name} className="cf-keysheet__group">
            <h3 className="cf-keysheet__heading">{group.name}</h3>
            <dl className="cf-keysheet__list">
              {group.items.map(({ action, binding, custom }) => (
                <div key={action.id} className="cf-keysheet__row">
                  <dt className="cf-keysheet__label">
                    {action.title}
                    {custom && (
                      <span className="cf-keysheet__custom" title="Changed from the default">
                        edited
                      </span>
                    )}
                  </dt>
                  <dd className="cf-keysheet__keys">
                    {(binding ?? []).map((chord, i) => (
                      <span key={`${chord}-${i}`}>
                        {i > 0 && <span className="cf-keysheet__then">then</span>}
                        <kbd className="cf-kbd">{formatBinding([chord], apple)}</kbd>
                      </span>
                    ))}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
        {groups.length === 0 && <p className="cf-keysheet__empty">Every shortcut is currently unbound.</p>}
        {footer && <div className="cf-keysheet__foot">{footer}</div>}
      </div>
    </Dialog>
  );
}
