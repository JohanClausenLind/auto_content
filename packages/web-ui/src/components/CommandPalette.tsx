import { useEffect, useMemo, useState } from "react";
import {
  Autocomplete,
  Dialog,
  Header,
  Input,
  Keyboard,
  ListBox,
  ListBoxItem,
  ListBoxSection,
  Modal,
  ModalOverlay,
  SearchField,
  Text,
  Collection,
} from "react-aria-components";
import { fuzzyMatches, fuzzyScore } from "./fuzzy";

export interface Command {
  id: string;
  title: string;
  group: string;
  shortcut?: string;
  keywords?: string;
  run: () => void;
}

export interface CommandPaletteProps {
  commands: readonly Command[];
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  placeholder?: string;
}

/** Register ⌘K / Ctrl+K and return controlled open state. */
export function useCommandPaletteHotkey(): [boolean, (open: boolean) => void] {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return [open, setOpen];
}

interface GroupNode {
  id: string;
  name: string;
  items: Command[];
}

export function CommandPalette({ commands, isOpen, onOpenChange, placeholder = "Type a command or search…" }: CommandPaletteProps) {
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (!isOpen) setQuery("");
  }, [isOpen]);

  const byId = useMemo(() => new Map(commands.map((c) => [c.id, c])), [commands]);

  // Filter + rank once here; Autocomplete's own filter then accepts everything that survived.
  const groups = useMemo<GroupNode[]>(() => {
    const scored = commands
      .map((c) => ({ c, s: fuzzyScore(query, `${c.title} ${c.group} ${c.keywords ?? ""}`) }))
      .filter((x): x is { c: Command; s: number } => x.s !== null)
      .sort((a, b) => b.s - a.s);
    const map = new Map<string, GroupNode>();
    for (const { c } of scored) {
      const g = map.get(c.group) ?? { id: c.group, name: c.group, items: [] };
      g.items.push(c);
      map.set(c.group, g);
    }
    return [...map.values()];
  }, [commands, query]);

  const total = groups.reduce((n, g) => n + g.items.length, 0);

  const runCommand = (key: React.Key) => {
    const cmd = byId.get(String(key));
    if (!cmd) return;
    onOpenChange(false);
    // Let the dialog close before the command navigates or opens another dialog.
    queueMicrotask(() => cmd.run());
  };

  return (
    <ModalOverlay isOpen={isOpen} onOpenChange={onOpenChange} isDismissable className="cf-overlay cf-overlay--top">
      <Modal className="cf-modal cf-modal--palette">
        <Dialog aria-label="Command palette" className="cf-palette">
          <Autocomplete inputValue={query} onInputChange={setQuery} filter={(text, input) => fuzzyMatches(input, text)}>
            <SearchField aria-label="Search commands" className="cf-palette__search" autoFocus>
              <Input className="cf-palette__input" placeholder={placeholder} />
            </SearchField>
            <div className="cf-visually-hidden" aria-live="polite" aria-atomic="true">
              {total === 0 ? "No matching commands" : `${total} ${total === 1 ? "command" : "commands"}`}
            </div>
            <ListBox
              aria-label="Commands"
              items={groups}
              className="cf-palette__list"
              selectionMode="single"
              selectionBehavior="replace"
              onAction={runCommand}
              renderEmptyState={() => (
                <div className="cf-palette__empty" role="presentation">
                  <p>Nothing matches “{query}”.</p>
                  <p className="cf-text-muted">Try a different word, or press Escape to close.</p>
                </div>
              )}
            >
              {(group) => (
                <ListBoxSection id={group.id} className="cf-palette__section">
                  <Header className="cf-palette__group">{group.name}</Header>
                  <Collection items={group.items}>
                    {(cmd) => (
                      <ListBoxItem id={cmd.id} textValue={`${cmd.title} ${cmd.group} ${cmd.keywords ?? ""}`} className="cf-palette__item">
                        <Text slot="label">{cmd.title}</Text>
                        {cmd.shortcut && <Keyboard className="cf-kbd">{cmd.shortcut}</Keyboard>}
                      </ListBoxItem>
                    )}
                  </Collection>
                </ListBoxSection>
              )}
            </ListBox>
          </Autocomplete>
          <footer className="cf-palette__footer" aria-hidden="true">
            <span><kbd className="cf-kbd">↑↓</kbd> move</span>
            <span><kbd className="cf-kbd">↵</kbd> run</span>
            <span><kbd className="cf-kbd">esc</kbd> close</span>
          </footer>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
